from datetime import datetime, timedelta

from flask import jsonify, request, session
from sqlalchemy import event

DONE = False
MOD = None
DB = None
Meta = None


def _admin():
    return bool(session.get("is_admin"))


def _define_model(app_module):
    global Meta
    db = app_module.db

    class EntregaAgendadaMeta(db.Model):
        __tablename__ = "entrega_agendada_meta"
        entrega_id = db.Column(db.Integer, primary_key=True)
        agendada_para = db.Column(db.DateTime, nullable=False, index=True)
        criada_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    Meta = EntregaAgendadaMeta


def _registrar_apos_inserir(mapper, connection, target):
    data_envio = getattr(target, "data_envio", None)
    entrega_id = getattr(target, "id", None)
    if not entrega_id or not data_envio:
        return

    # Entregas comuns são criadas com data_envio próxima ao horário atual.
    # A margem evita classificá-las como agendadas por diferença de segundos.
    if data_envio <= datetime.utcnow() + timedelta(minutes=2):
        return

    tabela = Meta.__table__
    existe = connection.execute(
        tabela.select().with_only_columns(tabela.c.entrega_id).where(
            tabela.c.entrega_id == int(entrega_id)
        )
    ).first()
    if not existe:
        connection.execute(
            tabela.insert().values(
                entrega_id=int(entrega_id),
                agendada_para=data_envio,
                criada_em=datetime.utcnow(),
            )
        )


def _backfill_futuras():
    limite = datetime.utcnow() + timedelta(minutes=2)
    existentes = {int(x[0]) for x in DB.session.query(Meta.entrega_id).all()}
    futuras = MOD.Entrega.query.filter(MOD.Entrega.data_envio > limite).all()
    novas = []
    for entrega in futuras:
        if int(entrega.id) in existentes:
            continue
        novas.append(Meta(entrega_id=entrega.id, agendada_para=entrega.data_envio))
    if novas:
        DB.session.add_all(novas)
        DB.session.commit()


def _api():
    if not _admin():
        return jsonify(ok=False, error="Não autorizado"), 401

    ids_raw = (request.args.get("ids") or "").strip()
    ids = []
    for parte in ids_raw.split(","):
        try:
            ids.append(int(parte))
        except Exception:
            pass

    query = Meta.query
    if ids:
        query = query.filter(Meta.entrega_id.in_(ids[:500]))
    else:
        query = query.order_by(Meta.criada_em.desc()).limit(500)

    itens = {
        str(item.entrega_id): item.agendada_para.isoformat()
        for item in query.all()
    }
    return jsonify(ok=True, agendamentos=itens)


def install(app_module):
    global DONE, MOD, DB
    if DONE:
        return

    MOD = app_module
    DB = app_module.db
    _define_model(app_module)

    with app_module.app.app_context():
        DB.create_all()
        _backfill_futuras()

    if not event.contains(app_module.Entrega, "after_insert", _registrar_apos_inserir):
        event.listen(app_module.Entrega, "after_insert", _registrar_apos_inserir)

    app_module.app.add_url_rule(
        "/api/admin/agendamentos",
        "api_admin_agendamentos",
        _api,
        methods=["GET"],
    )
    DONE = True
