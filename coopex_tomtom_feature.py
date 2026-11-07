"""TomTom traffic for COOPEX map; never expose TOMTOM_API_KEY to clients."""
import json
import os
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from flask import jsonify, session

_LOCK = threading.Lock()
_CACHE = {"at": 0.0, "payload": None}
_TTL = 900  # 15 minutes, shared by viewers in this Python worker
# Natal/RN and nearby operating region. TomTom bbox order: west,south,east,north.
_BBOX = "-35.40,-6.05,-35.08,-5.66"
_FIELDS = "{incidents{type,geometry{type,coordinates},properties{id,iconCategory,magnitudeOfDelay,events{description,code},from,to,delay,roadNumbers,length}}}"


def install(app):
    @app.get("/api/coopex/tomtom/incidentes")
    def coopex_tomtom_incidentes():
        if not session.get("is_admin"):
            return jsonify({"ok": False, "error": "Acesso restrito ao administrador."}), 403
        key = os.environ.get("TOMTOM_API_KEY", "").strip()
        if not key:
            return jsonify({"ok": False, "error": "TOMTOM_API_KEY não configurada no Render.", "code": "MISSING_KEY"}), 503
        now = time.monotonic()
        with _LOCK:
            if _CACHE["payload"] is not None and now - _CACHE["at"] < _TTL:
                return jsonify(_CACHE["payload"])
            url = "https://api.tomtom.com/traffic/services/5/incidentDetails?" + urlencode({
                "key": key,
                "bbox": _BBOX,
                "fields": _FIELDS,
                "language": "pt-PT",
                "timeValidityFilter": "present",
            })
            try:
                req = Request(url, headers={"Accept": "application/json", "User-Agent": "COOPEX-Traffic/1.0"})
                with urlopen(req, timeout=9) as response:
                    raw = json.loads(response.read(1024 * 1024))
                incidents = raw.get("incidents", []) if isinstance(raw, dict) else []
                features = []
                for item in incidents[:300]:
                    if not isinstance(item, dict):
                        continue
                    geometry = item.get("geometry") or {}
                    if geometry.get("type") not in ("Point", "LineString", "MultiLineString"):
                        continue
                    props = item.get("properties") or {}
                    events = props.get("events") or []
                    title = ", ".join(str(e.get("description", "")) for e in events if isinstance(e, dict))[:180]
                    features.append({
                        "type": "Feature",
                        "geometry": geometry,
                        "properties": {
                            "id": str(props.get("id", ""))[:100],
                            "category": props.get("iconCategory"),
                            "description": title or "Ocorrência no trânsito",
                            "from": str(props.get("from", ""))[:140],
                            "to": str(props.get("to", ""))[:140],
                            "delay": props.get("delay"),
                        }
                    })
                payload = {
                    "ok": True, "source": "TomTom Traffic",
                    "updated_at": int(time.time()),
                    "refresh_after_seconds": _TTL,
                    "bbox": _BBOX,
                    "features": features,
                }
                _CACHE.update(at=now, payload=payload)
                return jsonify(payload)
            except HTTPError as exc:
                # Do not leak API key, query URL or third-party response body.
                status = exc.code
                message = ("TomTom recusou a chamada do servidor. Verifique a restrição de domínio "
                           "da chave, o plano e os produtos habilitados." if status in (401, 403)
                           else "Limite da TomTom atingido." if status == 429
                           else "TomTom temporariamente indisponível.")
                code = ("KEY_REJECTED" if status in (401, 403) else "QUOTA" if status == 429 else "UPSTREAM")
                app.logger.warning("COOPEX TomTom Traffic: HTTP %s (%s), sem expor credenciais", status, code)
                return jsonify({"ok": False, "error": message, "upstream_status": status, "code": code}), 503
            except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
                return jsonify({"ok": False, "error": "Não foi possível consultar o trânsito da TomTom."}), 503
