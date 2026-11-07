"""TomTom traffic for COOPEX map; never expose TOMTOM_API_KEY to clients."""
import json
import io
import os
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from flask import jsonify, session, send_file, make_response

_LOCK = threading.Lock()
_CACHE = {"at": 0.0, "payload": None}
_TTL = 900  # 15 minutes, shared by viewers in this Python worker
# Natal/RN and nearby operating region. TomTom bbox order: west,south,east,north.
_BBOX = "-35.40,-6.05,-35.08,-5.66"
_FIELDS = "{incidents{type,geometry{type,coordinates},properties{id,iconCategory,magnitudeOfDelay,events{description,code},from,to,delay,roadNumbers,length}}}"


# Tile transparente para não mostrar quadrados de erro no mapa.
_EMPTY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
)
_FLOW_CACHE = {}
_FLOW_LOCK = threading.Lock()
_FLOW_TTL = 90


def _flow_png(key, z, x, y):
    url = ("https://api.tomtom.com/maps/orbis/traffic/flow/raster/tile/"
           f"{z}/{x}/{y}?apiVersion=2&style=light&tileSize=256")
    req = Request(url, headers={
        "TomTom-Api-Key": key,
        "TomTom-Api-Version": "2",
        "Accept": "image/png",
        "User-Agent": "COOPEX-Traffic/2.0",
    })
    with urlopen(req, timeout=7) as result:
        if "image/png" not in result.headers.get("Content-Type", ""):
            raise ValueError("TomTom returned an unexpected content type")
        return result.read(250000)


def install(app):
    @app.get("/api/coopex/tomtom/flow/status")
    def coopex_tomtom_flow_status():
        if not session.get("is_admin"):
            return jsonify({"ok": False, "error": "Acesso restrito"}), 403
        key = os.environ.get("TOMTOM_API_KEY", "").strip()
        if not key:
            return jsonify({"ok": False, "code": "MISSING_KEY"}), 503
        import math
        z = 11
        x = int(((-35.211 + 180) / 360) * (1 << z))
        latrad = math.radians(-5.7945)
        y = int((1 - math.asinh(math.tan(latrad)) / math.pi) / 2 * (1 << z))
        try:
            _flow_png(key, z, x, y)
            return jsonify({"ok": True, "source": "TomTom Orbis Traffic Flow"})
        except HTTPError as exc:
            return jsonify({"ok": False, "code": "KEY_REJECTED" if exc.code in (401,403) else "UPSTREAM",
                            "upstream_status": exc.code}), 503
        except (URLError, TimeoutError, OSError, ValueError):
            return jsonify({"ok": False, "code": "UPSTREAM"}), 503

    @app.get("/api/coopex/tomtom/flow/<int:z>/<int:x>/<int:y>.png")
    def coopex_tomtom_flow_tile(z, x, y):
        if not session.get("is_admin"):
            return ("", 403)
        if z < 8 or z > 18 or x < 0 or y < 0 or x >= 2**z or y >= 2**z:
            return ("", 404)
        key = os.environ.get("TOMTOM_API_KEY", "").strip()
        if not key:
            return make_response(_EMPTY_PNG, 200, {"Content-Type": "image/png", "Cache-Control": "no-store"})

        cache_key = (z, x, y)
        now = time.monotonic()
        with _FLOW_LOCK:
            stored = _FLOW_CACHE.get(cache_key)
            if stored and now - stored[0] < _FLOW_TTL:
                data = stored[1]
            else:
                data = None
        if data is None:
            try:
                data = _flow_png(key, z, x, y)
                with _FLOW_LOCK:
                    if len(_FLOW_CACHE) >= 400:
                        _FLOW_CACHE.clear()
                    _FLOW_CACHE[cache_key] = (now, data)
            except HTTPError as exc:
                app.logger.warning("COOPEX TomTom flow HTTP %s, z=%s; credentials redacted", exc.code, z)
                data = _EMPTY_PNG
            except (URLError, TimeoutError, OSError, ValueError):
                data = _EMPTY_PNG
        response = make_response(data)
        response.headers["Content-Type"] = "image/png"
        response.headers["Cache-Control"] = "private, max-age=60"
        return response


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
            # Traffic API Orbis v2 é o produto disponível no portal TomTom atual.
            url = "https://api.tomtom.com/maps/orbis/traffic/incidents/details?" + urlencode({
                "bbox": _BBOX,
                "apiVersion": 2,
                "timeValidity": "present",
            })
            try:
                req = Request(url, headers={
                    "Accept": "application/json",
                    "TomTom-Api-Key": key,
                    "TomTom-Api-Version": "2",
                    "Attributes": "incidents(type,geometry(type,coordinates),properties(id,iconCategory,events(description),from,to,delayInSeconds))",
                    "Accept-Language": "pt-PT",
                    "User-Agent": "COOPEX-Traffic/2.0",
                })
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
                            "delay": props.get("delayInSeconds"),
                        }
                    })
                payload = {
                    "ok": True, "source": "TomTom Traffic Orbis v2",
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
