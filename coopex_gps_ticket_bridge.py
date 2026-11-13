"""Emite ticket do GPS Cloudflare ORIGINAL para o Financeiro incorporado.

NUNCA copia a chave do Cloudflare para o Financeiro. Autentica a solicitação
entre serviços com chave SSO compartilhada e HMAC do corpo e timestamp.
"""
import base64
import hashlib
import hmac
import json
import os
import time
from flask import jsonify, request


def registrar(app):
    @app.post("/api/interno/coopex-financeiro/gps-ticket")
    def ticket_gps_financeiro():
        secret = os.environ.get("COOPEX_SUPERVISAO_SSO_SECRET", "")
        if len(secret) < 48 or not request.content_length or request.content_length > 256:
            return jsonify(ok=False, error="not_available"), 403
        ts = request.headers.get("X-COOPEX-Timestamp", "")
        sig = request.headers.get("X-COOPEX-Signature", "")
        if not ts.isdigit() or len(ts) > 14 or len(sig) != 64:
            return jsonify(ok=False, error="unauthorized"), 403
        if abs(time.time() - int(ts)) > 45:
            return jsonify(ok=False, error="expired"), 403
        raw = request.get_data(cache=False)
        esperado = hmac.new(
            secret.encode(), b"coopex-gps-ticket-v1\n" + ts.encode() + b"\n" + raw,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, esperado):
            return jsonify(ok=False, error="unauthorized"), 403
        try:
            dados = json.loads(raw)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="invalid"), 400
        if dados != {"scope": "admin_map"}:
            return jsonify(ok=False, error="scope"), 403
        chave_gps = os.environ.get("COOPEX_GPS_MAP_SIGN_KEY", "")
        endpoint = os.environ.get("COOPEX_GPS_API_URL", "").rstrip("/")
        if not chave_gps or not endpoint.startswith("https://"):
            return jsonify(ok=False, error="gps_not_configured"), 503
        exp = int(time.time()) + 600
        payload = json.dumps(
            {"role": "admin_map", "exp": exp},
            sort_keys=True, separators=(",", ":"),
        ).encode()
        body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
        mac = hmac.new(chave_gps.encode(), body.encode("ascii"), hashlib.sha256).digest()
        sig_gps = base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")
        resp = jsonify(
            ok=True, url=endpoint + "/v1/positions",
            ticket=body + "." + sig_gps, expires_at=exp,
        )
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp
