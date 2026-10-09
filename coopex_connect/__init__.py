"""COOPEX Connect: intake + human approval. Install after app models/routes are defined."""
import hashlib
import hmac
import json
import os
import re
from datetime import datetime
from decimal import Decimal
from functools import wraps

from flask import Blueprint, abort, jsonify, render_template, request, session
from sqlalchemy import func


def normalize_phone(phone):
    digits = re.sub(r"\D", "", str(phone or ""))
    return digits if 10 <= len(digits) <= 15 else None


def install(host):
    app, db = host.app, host.db
    Cliente, Entrega = host.Cliente, host.Entrega
    if 'coopex_connect' in app.blueprints:
        return

    class ConnectDraft(db.Model):
        __tablename__ = 'connect_delivery_draft'
        id = db.Column(db.Integer, primary_key=True)
        message_id = db.Column(db.String(160), unique=True, nullable=False, index=True)
        sender = db.Column(db.String(24), nullable=False)
        cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
        cliente_nome = db.Column(db.String(120), nullable=True)
        raw_message = db.Column(db.Text, nullable=False)
        coleta_endereco = db.Column(db.String(255))
        entrega_endereco = db.Column(db.String(255))
        origem_bairro = db.Column(db.String(100))
        destino_bairro = db.Column(db.String(100))
        complemento = db.Column(db.String(200))
        valor_previsto = db.Column(db.Numeric(12, 2))
        pagamento = db.Column(db.String(50))
        status = db.Column(db.String(32), nullable=False, default='rascunho')
        pendencia = db.Column(db.Text)
        entrega_id = db.Column(db.Integer, db.ForeignKey('entrega.id'))
        criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        aprovado_em = db.Column(db.DateTime)
        aprovado_por = db.Column(db.String(100))

    bp = Blueprint('coopex_connect', __name__, template_folder='templates')
    from .whatsapp_feature import install as install_whatsapp
    process_whatsapp = install_whatsapp(app, db, bp)

    def admin_required(f):
        @wraps(f)
        def inner(*args, **kwargs):
            if not session.get('is_admin'):
                return jsonify(ok=False, error='Acesso administrativo necessário'), 403
            return f(*args, **kwargs)
        return inner

    def serialize(d):
        return dict(id=d.id, sender=d.sender, cliente=d.cliente_nome,
                    coleta=d.coleta_endereco, entrega=d.entrega_endereco,
                    origem=d.origem_bairro, destino=d.destino_bairro,
                    complemento=d.complemento, valor=float(d.valor_previsto) if d.valor_previsto is not None else None,
                    pagamento=d.pagamento, status=d.status, pendencia=d.pendencia,
                    entrega_id=d.entrega_id, texto=d.raw_message)

    def lookup_cliente(sender, nome):
        # Never associate by name alone: several people can have the same name.
        all_clients = Cliente.query.filter(Cliente.telefone.isnot(None)).all()
        matched = [c for c in all_clients if normalize_phone(c.telefone) == sender]
        if len(matched) > 1:
            return None, 'Número associado a mais de um cliente; conferir'
        if matched:
            return matched[0], None
        if not nome:
            return None, 'Perguntar nome do cliente'
        # Do not silently create a login or alter an existing account by name.
        c = Cliente(nome=nome[:100], telefone=sender)
        db.session.add(c)
        db.session.flush()
        return c, None

    def validate_and_price(d):
        problems = []
        if not d.cliente_id:
            problems.append('Cliente ainda não identificado')
        if not d.origem_bairro:
            problems.append('Informar bairro de coleta')
        if not d.destino_bairro:
            problems.append('Informar bairro de entrega')
        if not (d.entrega_endereco or d.destino_bairro):
            problems.append('Informar destino')
        if not d.pagamento:
            problems.append('Informar pagamento')
        d.valor_previsto = None
        if d.origem_bairro and d.destino_bairro:
            # Reuses actual production pricing (including its ceiling/rounding logic).
            cot = host._calcular_cotacao_entrega(
                {'bairro': d.origem_bairro, 'endereco': d.coleta_endereco or ''},
                {'bairro': d.destino_bairro, 'endereco': d.entrega_endereco or ''})
            if cot.get('valor_a_informar') or cot.get('preco') is None:
                problems.append('Rota sem valor na tabela: conferência manual')
            else:
                d.valor_previsto = Decimal(str(cot['preco']))
        d.pendencia = '; '.join(problems) or None
        d.status = 'incompleto' if problems else 'aguardando_aprovacao'
        return problems

    @bp.get('/admin/diagnostico')
    @admin_required
    def diagnostico():
        from sqlalchemy import inspect
        required = ('connect_delivery_draft', 'connect_whatsapp_message')
        try:
            inspector = inspect(db.engine)
            tables = {name: inspector.has_table(name) for name in required}
        except Exception:
            app.logger.exception('COOPEX CONNECT: verificacao de banco falhou')
            return jsonify(ok=False, error='Banco indisponivel'), 503
        return jsonify(
            ok=all(tables.values()),
            tabelas=tables,
            integracoes={
                'openai_configurada': bool(os.environ.get('OPENAI_API_KEY')),
                'meta_token_configurado': bool(os.environ.get('COOPEX_META_ACCESS_TOKEN')),
                'meta_app_secret_configurado': bool(os.environ.get('COOPEX_META_APP_SECRET')),
                'meta_verify_token_configurado': bool(os.environ.get('COOPEX_META_VERIFY_TOKEN'))
            },
            modo='atendimento_manual_sem_envio_automatico'
        )

    @bp.get('/admin')
    @admin_required
    def panel():
        items = ConnectDraft.query.order_by(ConnectDraft.id.desc()).limit(100).all()
        return render_template('connect_admin.html', items=[serialize(d) for d in items])

    @bp.post('/admin/rascunhos')
    @admin_required
    def make_draft():
        x = request.get_json(silent=True) or {}
        sender = normalize_phone(x.get('telefone'))
        message_id = str(x.get('message_id') or '').strip()
        if not sender or not message_id or not x.get('mensagem'):
            return jsonify(ok=False, error='telefone, message_id e mensagem obrigatórios'), 400
        existing = ConnectDraft.query.filter_by(message_id=message_id).first()
        if existing:
            return jsonify(ok=True, duplicado=True, draft=serialize(existing))
        nome = str(x.get('nome') or '').strip()
        cliente, pend = lookup_cliente(sender, nome)
        d = ConnectDraft(message_id=message_id[:160], sender=sender,
                         cliente_id=cliente.id if cliente else None,
                         cliente_nome=cliente.nome if cliente else nome[:120],
                         raw_message=str(x['mensagem'])[:10000],
                         coleta_endereco=str(x.get('coleta_endereco') or '')[:255],
                         entrega_endereco=str(x.get('entrega_endereco') or '')[:255],
                         origem_bairro=str(x.get('origem_bairro') or (cliente.bairro_origem if cliente else '') or '')[:100],
                         destino_bairro=str(x.get('destino_bairro') or '')[:100],
                         complemento=str(x.get('complemento') or '')[:200],
                         pagamento=str(x.get('pagamento') or '')[:50])
        validate_and_price(d)
        if pend:
            d.pendencia = '; '.join(filter(None, [d.pendencia, pend])); d.status = 'incompleto'
        db.session.add(d)
        db.session.commit()
        return jsonify(ok=True, draft=serialize(d)), 201

    @bp.post('/admin/rascunhos/<int:draft_id>/revisar')
    @admin_required
    def revise(draft_id):
        d = db.session.get(ConnectDraft, draft_id)
        if not d or d.status in ('aprovado', 'recusado'):
            return jsonify(ok=False, error='Rascunho não editável'), 409
        x = request.get_json(silent=True) or {}
        for key in ('coleta_endereco', 'entrega_endereco', 'origem_bairro', 'destino_bairro', 'complemento', 'pagamento'):
            if key in x:
                setattr(d, key, str(x[key] or '')[:255])
        validate_and_price(d)
        db.session.commit()
        return jsonify(ok=True, draft=serialize(d))

    @bp.post('/admin/rascunhos/<int:draft_id>/recusar')
    @admin_required
    def refuse(draft_id):
        d = db.session.get(ConnectDraft, draft_id)
        if not d or d.status == 'aprovado':
            return jsonify(ok=False, error='Não pode recusar'), 409
        d.status = 'recusado'
        d.aprovado_por = str(session.get('username') or 'admin')
        d.aprovado_em = datetime.utcnow()
        db.session.commit()
        return jsonify(ok=True, draft=serialize(d))

    @bp.post('/admin/rascunhos/<int:draft_id>/aprovar')
    @admin_required
    def approve(draft_id):
        # Lock the draft so two concurrent approvers cannot create two deliveries.
        d = db.session.query(ConnectDraft).filter_by(id=draft_id).with_for_update().first()
        if not d:
            return jsonify(ok=False, error='Não encontrado'), 404
        if d.status == 'aprovado':
            return jsonify(ok=True, duplicado=True, entrega_id=d.entrega_id)
        problems = validate_and_price(d)
        if problems:
            db.session.rollback()
            return jsonify(ok=False, error='Pendências', detalhes=problems), 409
        # Explicitly no cooperado assignment before approval or proof of payment.
        e = Entrega(cliente=d.cliente_nome, cliente_id=d.cliente_id,
                    bairro=d.destino_bairro, valor=float(d.valor_previsto),
                    pagamento=d.pagamento, status='pendente',
                    status_pagamento='pendente', data_envio=datetime.utcnow(),
                    cooperado_id=None)
        e.origem_json = json.dumps({'endereco': d.coleta_endereco, 'bairro': d.origem_bairro}, ensure_ascii=False)
        e.destino_json = json.dumps({'endereco': d.entrega_endereco, 'bairro': d.destino_bairro,
                                     'complemento': d.complemento}, ensure_ascii=False)
        db.session.add(e)
        db.session.flush()
        d.status = 'aprovado'; d.entrega_id = e.id
        d.aprovado_em = datetime.utcnow(); d.aprovado_por = str(session.get('username') or 'admin')
        db.session.commit()
        return jsonify(ok=True, entrega_id=e.id, status_pagamento=e.status_pagamento,
                       aviso='Pagamento ainda pendente; atribuição não realizada')

    @bp.get('/webhook')
    def verify_webhook():
        verify_token = os.environ.get('COOPEX_META_VERIFY_TOKEN')
        if verify_token and request.args.get('hub.mode') == 'subscribe' and hmac.compare_digest(
            request.args.get('hub.verify_token') or '', verify_token):
            return request.args.get('hub.challenge', ''), 200
        abort(403)

    @bp.post('/webhook')
    def receive_webhook():
        secret = os.environ.get('COOPEX_META_APP_SECRET')
        if not secret:
            abort(503)
        signature = request.headers.get('X-Hub-Signature-256', '')
        expected = 'sha256=' + hmac.new(secret.encode(), request.get_data(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            abort(403)
        # Safe initial version: receives authenticated webhooks but does not send messages,
        # interpret audio, register deliveries, or persist personal data until queue integration.
        try:
            payload = request.get_json(silent=True) or {}
            received = process_whatsapp(payload)
            return jsonify(ok=True, recebidas=received)
        except Exception:
            db.session.rollback()
            app.logger.exception('COOPEX Connect: erro ao processar webhook')
            return jsonify(ok=False), 503

    @bp.get('/manifest.webmanifest')
    def connect_manifest():
        return jsonify({
            "name": "COOPEX CONNECT",
            "short_name": "CONNECT",
            "start_url": "/coopex-connect/admin/conversas",
            "scope": "/coopex-connect/",
            "display": "standalone",
            "background_color": "#f4f7fd",
            "theme_color": "#1353cf",
            "icons": [{"src": "/coopex-connect/icon.svg",
                       "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}]
        }), 200, {"Content-Type": "application/manifest+json"}

    @bp.get('/icon.svg')
    def connect_icon():
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="192" height="192" viewBox="0 0 192 192">'
               '<rect width="192" height="192" rx="42" fill="#1353cf"/>'
               '<path d="M47 96l30 30 68-68" fill="none" stroke="#fff" stroke-width="15" '
               'stroke-linecap="round" stroke-linejoin="round"/></svg>')
        return app.response_class(svg, mimetype="image/svg+xml")

    app.register_blueprint(bp, url_prefix='/coopex-connect')
    app.logger.info('COOPEX Connect blueprint registered. Apply migration before using draft endpoints.')
