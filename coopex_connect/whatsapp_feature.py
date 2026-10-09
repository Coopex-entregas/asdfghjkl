"""COOPEX CONNECT - WhatsApp Cloud API: inbox de texto e envio manual."""
import json
import os
import secrets
import urllib.error
import urllib.request
from datetime import datetime
from flask import jsonify, request, session

def _send(number_id, phone, body):
    token = os.environ.get("COOPEX_META_ACCESS_TOKEN", "").strip()
    version = os.environ.get("COOPEX_META_GRAPH_VERSION", "v23.0")
    if not token or not number_id.isdigit() or not version.replace("v", "").replace(".", "").isdigit():
        return False
    url = f"https://graph.facebook.com/{version}/{number_id}/messages"
    payload = {"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": body}}
    req = urllib.request.Request(url, json.dumps(payload).encode("utf-8"), {
        "Authorization": "Bearer " + token, "Content-Type": "application/json"
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return 200 <= res.status < 300
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False

def install(app, db, bp):
    class ConnectWhatsAppMessage(db.Model):
        __tablename__ = "connect_whatsapp_message"
        id = db.Column(db.Integer, primary_key=True)
        external_id = db.Column(db.String(180), unique=True, nullable=False, index=True)
        phone_number_id = db.Column(db.String(80), nullable=False, index=True)
        phone = db.Column(db.String(25), nullable=False, index=True)
        direction = db.Column(db.String(5), nullable=False)
        body = db.Column(db.Text, nullable=False)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @bp.get("/admin/whatsapp")
    def whatsapp_inbox():
        if not session.get("is_admin"):
            return jsonify(ok=False, error="Acesso negado"), 403
        rows = ConnectWhatsAppMessage.query.order_by(ConnectWhatsAppMessage.id.desc()).limit(200).all()
        return jsonify(ok=True, mensagens=[{"id": m.id, "telefone": m.phone,
            "numero_whatsapp": m.phone_number_id, "direcao": m.direction,
            "texto": m.body, "criado_em": m.created_at.isoformat() + "Z"} for m in rows])

    @bp.post("/admin/whatsapp/responder")
    def whatsapp_reply():
        if not session.get("is_admin"):
            return jsonify(ok=False, error="Acesso negado"), 403
        payload = request.get_json(silent=True) or {}
        phone = "".join(ch for ch in str(payload.get("telefone") or "") if ch.isdigit())
        number_id = str(payload.get("numero_whatsapp") or "")
        body = str(payload.get("mensagem") or "").strip()
        if not (10 <= len(phone) <= 15 and number_id.isdigit() and 1 <= len(body) <= 2000):
            return jsonify(ok=False, error="Dados inválidos"), 400
        if not ConnectWhatsAppMessage.query.filter_by(phone=phone, phone_number_id=number_id).first():
            return jsonify(ok=False, error="Conversa não encontrada"), 404
        if not _send(number_id, phone, body):
            return jsonify(ok=False, error="Envio não confirmado pela Meta"), 502
        db.session.add(ConnectWhatsAppMessage(external_id="manual-"+secrets.token_hex(16),
            phone_number_id=number_id, phone=phone, direction="out", body=body))
        db.session.commit()
        return jsonify(ok=True)

    def receive(payload):
        added = 0
        for entry in (payload.get("entry") or [])[:10]:
            for change in (entry.get("changes") or [])[:10]:
                value = change.get("value") or {}
                number_id = str((value.get("metadata") or {}).get("phone_number_id") or "")
                if not number_id.isdigit():
                    continue
                for msg in (value.get("messages") or [])[:30]:
                    if msg.get("type") != "text":
                        continue
                    message_id = str(msg.get("id") or "")[:180]
                    phone = "".join(ch for ch in str(msg.get("from") or "") if ch.isdigit())
                    body = str((msg.get("text") or {}).get("body") or "").strip()[:4000]
                    if not (message_id and 10 <= len(phone) <= 15 and body):
                        continue
                    if ConnectWhatsAppMessage.query.filter_by(external_id=message_id).first():
                        continue
                    db.session.add(ConnectWhatsAppMessage(external_id=message_id,
                        phone_number_id=number_id, phone=phone, direction="in", body=body))
                    try:
                        db.session.commit()
                        added += 1
                    except Exception:
                        db.session.rollback()
        return added
    return receive
