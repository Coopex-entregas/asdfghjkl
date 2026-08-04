import secrets
from datetime import datetime

from flask import flash, jsonify, redirect, request, session, url_for
from sqlalchemy import func

DONE = False
MOD = None
DB = None
Arquivado = None

FINAL_STATUSES = {
    "entregue",
    "entregue ao cliente",
    "recebido",
    "recebida",
    "concluido",
    "concluida",
    "finalizado",
    "finalizada",
}


def _admin():
    return bool(session.get("is_admin"))


def _define_model(app_module):
    global Arquivado
    db = app_module.db

    class CooperadoArquivado(db.Model):
        __tablename__ = "cooperado_arquivado"

        cooperado_id = db.Column(
            db.Integer,
            db.ForeignKey("cooperado.id"),
            primary_key=True,
        )
        nome_snapshot = db.Column(db.String(120), nullable=False)
        arquivado_em = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        arquivado_por = db.Column(db.String(120), nullable=True)

    Arquivado = CooperadoArquivado


def _arquivado(cooperado_id):
    return Arquivado.query.filter_by(cooperado_id=int(cooperado_id)).first()


def _nome_admin():
    return (
        session.get("admin_user")
        or session.get("user_nome")
        or session.get("username")
        or "Administrador"
    )


def _status_final(valor):
    texto = str(valor or "").strip().lower()
    return texto in FINAL_STATUSES


def _retirar_da_fila(cooperado_id):
    removidos = 0
    modelo = getattr(MOD, "ListaEspera", None)
    if not modelo:
        return removidos

    itens = modelo.query.filter_by(cooperado_id=int(cooperado_id)).all()
    for item in itens:
        DB.session.delete(item)
        removidos += 1
    return removidos


def _marcar_offline(cooperado):
    cooperado.online = False
    cooperado.last_ping = None
    cooperado.app_token = None

    modelo = getattr(MOD, "LocalizacaoCooperado", None)
    if modelo:
        localizacao = modelo.query.filter_by(cooperado_id=cooperado.id).first()
        if localizacao:
            localizacao.online = False
            localizacao.atualizado_em = datetime.utcnow()


def _desatribuir_abertas(cooperado_id):
    modelo = getattr(MOD, "Entrega", None)
    if not modelo:
        return 0

    removidas = 0
    entregas = modelo.query.filter_by(cooperado_id=int(cooperado_id)).all()
    for entrega in entregas:
        if _status_final(getattr(entrega, "status", None)):
            continue
        entrega.cooperado_id = None
        if hasattr(entrega, "data_atribuida"):
            entrega.data_atribuida = None
        removidas += 1
    return removidas


def _retirar_da_escala_atual(cooperado_id, motivo):
    try:
        import escala_feature

        item_model = getattr(escala_feature, "Item", None)
        if not item_model:
            return 0

        hoje = datetime.now(getattr(MOD, "BRAZIL_TZ", None)).date()
        itens = item_model.query.filter(
            item_model.cooperado_id == int(cooperado_id),
            item_model.data >= hoje,
        ).all()

        for item in itens:
            item.cooperado_id = None
            item.status_match = "folga"
            item.candidatos_json = "[]"
            item.detalhe_match = f"Motivo: {motivo}"[:255]
        return len(itens)
    except Exception:
        return 0


def _emitir_fila():
    try:
        MOD.emitir_lista_espera()
    except Exception:
        pass


def _cadastrar_cooperado():
    if not _admin():
        return redirect(url_for("login"))

    Cooperado = MOD.Cooperado

    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        senha = request.form.get("senha") or ""

        if not nome or not senha:
            flash("Preencha o nome e a senha.")
            return redirect(url_for("cadastrar_cooperado"))

        existente = Cooperado.query.filter(
            func.lower(Cooperado.nome) == nome.lower()
        ).first()

        if existente:
            registro_arquivado = _arquivado(existente.id)
            if registro_arquivado:
                existente.nome = nome
                existente.set_senha(senha)
                existente.ativo = True
                existente.online = False
                existente.app_token = None
                DB.session.delete(registro_arquivado)
                DB.session.commit()
                flash(
                    "Cadastro histórico restaurado. As entregas antigas foram mantidas e o acesso foi reativado.",
                    "success",
                )
            else:
                flash("Já existe um cooperado com esse nome!")
            return redirect(url_for("cadastrar_cooperado"))

        novo = Cooperado(nome=nome, ativo=True, online=False)
        novo.set_senha(senha)
        DB.session.add(novo)
        DB.session.commit()
        flash("Cooperado cadastrado com sucesso!", "success")
        return redirect(url_for("cadastrar_cooperado"))

    ids_arquivados = [
        int(row[0])
        for row in DB.session.query(Arquivado.cooperado_id).all()
    ]
    query = Cooperado.query
    if ids_arquivados:
        query = query.filter(~Cooperado.id.in_(ids_arquivados))
    cooperados = query.order_by(Cooperado.ativo.desc(), Cooperado.nome.asc()).all()

    return MOD.render_template(
        "cadastrar_cooperado.html",
        cooperados=cooperados,
        arquivados_total=len(ids_arquivados),
    )


def _mudar_status(coop_id):
    if not _admin():
        return redirect(url_for("login"))

    cooperado = MOD.Cooperado.query.get_or_404(coop_id)
    if _arquivado(cooperado.id):
        flash("Esse cadastro foi excluído e está apenas no histórico.", "warning")
        return redirect(url_for("cadastrar_cooperado"))

    novo_status = request.form.get("novo_status")
    ativar = str(novo_status) == "1"
    cooperado.ativo = ativar

    fila = 0
    abertas = 0
    escala = 0
    if not ativar:
        _marcar_offline(cooperado)
        fila = _retirar_da_fila(cooperado.id)
        abertas = _desatribuir_abertas(cooperado.id)
        escala = _retirar_da_escala_atual(
            cooperado.id,
            "Cooperado desativado pelo administrador",
        )

    DB.session.commit()
    _emitir_fila()

    if ativar:
        flash(f"{cooperado.nome} foi reativado e poderá entrar novamente.", "success")
    else:
        flash(
            f"{cooperado.nome} foi desativado. Acesso bloqueado, {fila} registro(s) retirado(s) da fila, "
            f"{abertas} entrega(s) aberta(s) desatribuída(s) e {escala} escala(s) futura(s) retiradas.",
            "success",
        )
    return redirect(url_for("cadastrar_cooperado"))


def _excluir_cooperado(coop_id):
    if not _admin():
        return redirect(url_for("login"))

    cooperado = MOD.Cooperado.query.get_or_404(coop_id)
    if _arquivado(cooperado.id):
        flash("Esse cadastro já foi excluído. O histórico continua preservado.", "warning")
        return redirect(url_for("cadastrar_cooperado"))

    nome = cooperado.nome
    cooperado.ativo = False
    _marcar_offline(cooperado)

    # Troca a senha para impedir qualquer reutilização de uma sessão antiga.
    cooperado.set_senha(secrets.token_urlsafe(48))

    fila = _retirar_da_fila(cooperado.id)
    abertas = _desatribuir_abertas(cooperado.id)
    escala = _retirar_da_escala_atual(
        cooperado.id,
        "Cadastro do cooperado excluído; histórico preservado",
    )

    DB.session.add(
        Arquivado(
            cooperado_id=cooperado.id,
            nome_snapshot=nome,
            arquivado_por=_nome_admin(),
        )
    )
    DB.session.commit()
    _emitir_fila()

    flash(
        f"Cadastro de {nome} excluído do uso ativo. Ele não aparece mais no cadastro, não entra no sistema "
        f"e não pode receber novas entregas. O nome e as {len(getattr(cooperado, 'entregas', []) or [])} "
        f"entrega(s) históricas permanecem nos relatórios. Também foram retirados {fila} item(ns) da fila, "
        f"{abertas} entrega(s) aberta(s) e {escala} escala(s) futura(s).",
        "success",
    )
    return redirect(url_for("cadastrar_cooperado"))


def _api_ativos():
    if not _admin():
        return jsonify(ok=False, error="Não autorizado"), 401

    ids_arquivados = DB.session.query(Arquivado.cooperado_id)
    cooperados = MOD.Cooperado.query.filter(
        MOD.Cooperado.ativo.is_(True),
        ~MOD.Cooperado.id.in_(ids_arquivados),
    ).order_by(MOD.Cooperado.nome.asc()).all()

    return jsonify(
        ok=True,
        items=[{"id": int(item.id), "nome": item.nome} for item in cooperados],
    )


def _bloquear_sessao_inativa():
    if session.get("is_admin"):
        return None
    if session.get("tipo") != "cooperado":
        return None

    endpoint = request.endpoint or ""
    if endpoint in {"login", "logout", "static"}:
        return None

    user_id = session.get("user_id")
    try:
        cooperado = MOD.Cooperado.query.get(int(user_id)) if user_id is not None else None
    except Exception:
        cooperado = None

    bloqueado = (
        cooperado is None
        or not bool(getattr(cooperado, "ativo", False))
        or _arquivado(cooperado.id) is not None
    )
    if not bloqueado:
        return None

    session.clear()
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="Cadastro inativo ou excluído."), 403
    flash("Seu cadastro está inativo ou foi excluído. Procure a administração.", "error")
    return redirect(url_for("login"))


def install(app_module):
    global DONE, MOD, DB
    if DONE:
        return

    MOD = app_module
    DB = app_module.db
    _define_model(app_module)

    with app_module.app.app_context():
        DB.create_all()

    # Substitui as rotas antigas sem alterar o restante do app.py.
    app_module.app.view_functions["cadastrar_cooperado"] = _cadastrar_cooperado
    app_module.app.view_functions["mudar_status_cooperado"] = _mudar_status
    app_module.app.view_functions["excluir_cooperado"] = _excluir_cooperado

    app_module.app.add_url_rule(
        "/api/cooperados/ativos",
        "api_cooperados_ativos",
        _api_ativos,
        methods=["GET"],
    )
    app_module.app.before_request(_bloquear_sessao_inativa)
    DONE = True
