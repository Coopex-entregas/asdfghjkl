from datetime import date, datetime

from flask import flash, redirect, request, session, url_for

DONE = False
MOD = None
SCALE = None
DB = None


def _admin():
    return bool(session.get("is_admin"))


def _redirect_back():
    return redirect(request.referrer or url_for("escala"))


def _meta_recount():
    meta = SCALE.Importacao.query.filter_by(id=1).first()
    if meta:
        SCALE.recount(meta)


def _cooperado(cooperado_id):
    if not cooperado_id:
        return None
    return MOD.Cooperado.query.get(int(cooperado_id))


def _parse_common_form(require_cooperado=False):
    data_raw = (request.form.get("data") or "").strip()
    contrato = (request.form.get("contrato") or "").strip()
    turno = (request.form.get("turno") or "").strip()
    horario = (request.form.get("horario") or "").strip()
    motivo = (request.form.get("motivo") or "").strip()
    cooperado_id = request.form.get("cooperado_id", type=int)

    try:
        data_escala = date.fromisoformat(data_raw)
    except Exception as exc:
        raise ValueError("Informe uma data válida.") from exc

    if not contrato:
        raise ValueError("Informe o contrato.")
    if not horario:
        raise ValueError("Informe o horário.")
    if not motivo:
        raise ValueError("Informe o motivo do ajuste.")
    if require_cooperado and not cooperado_id:
        raise ValueError("Selecione o cooperado.")

    cooperado = _cooperado(cooperado_id)
    if cooperado_id and not cooperado:
        raise ValueError("Cooperado não encontrado.")

    intervalos, estimado = SCALE.parse_hours(horario, turno)
    return {
        "data": data_escala,
        "contrato": contrato[:160],
        "turno": turno[:30],
        "horario": horario[:120],
        "motivo": motivo[:220],
        "cooperado": cooperado,
        "intervalos": intervalos,
        "estimado": bool(estimado),
    }


def _limpar_ajuste_antigo(item_id):
    ajuste_model = getattr(SCALE, "Ajuste", None)
    if not ajuste_model:
        return
    ajuste = ajuste_model.query.filter_by(item_id=item_id).first()
    if ajuste:
        DB.session.delete(ajuste)


def _adicionar():
    if not _admin():
        return redirect(url_for("login"))

    try:
        dados = _parse_common_form(require_cooperado=True)
        cooperado = dados["cooperado"]
        item = SCALE.Item(
            data=dados["data"],
            qtd="manual",
            turno=dados["turno"],
            horario_texto=dados["horario"],
            intervalos_json=__import__("json").dumps(dados["intervalos"], ensure_ascii=False),
            horario_estimado=dados["estimado"],
            contrato=dados["contrato"],
            nome_planilha=(cooperado.nome or "")[:160],
            nome_norm=SCALE.norm(cooperado.nome)[:160],
            cooperado_id=cooperado.id,
            status_match="manual",
            candidatos_json="[]",
            detalhe_match=("Motivo: " + dados["motivo"])[:255],
            importado_em=datetime.utcnow(),
        )
        DB.session.add(item)
        DB.session.flush()
        _meta_recount()
        DB.session.commit()
        SCALE.clear_cache()
        flash(f"{cooperado.nome} foi adicionado à escala de {dados['contrato']}.", "success")
    except Exception as exc:
        DB.session.rollback()
        flash(f"Não foi possível adicionar à escala: {exc}", "error")

    return _redirect_back()


def _editar(item_id):
    if not _admin():
        return redirect(url_for("login"))

    item = SCALE.Item.query.get_or_404(item_id)
    try:
        dados = _parse_common_form(require_cooperado=False)
        cooperado = dados["cooperado"]
        _limpar_ajuste_antigo(item.id)

        item.data = dados["data"]
        item.contrato = dados["contrato"]
        item.turno = dados["turno"]
        item.horario_texto = dados["horario"]
        item.intervalos_json = __import__("json").dumps(dados["intervalos"], ensure_ascii=False)
        item.horario_estimado = dados["estimado"]
        item.cooperado_id = cooperado.id if cooperado else None
        item.status_match = "ajustado" if cooperado else "folga"
        item.candidatos_json = "[]"
        item.detalhe_match = ("Motivo: " + dados["motivo"])[:255]
        item.importado_em = datetime.utcnow()

        if cooperado:
            item.nome_planilha = (cooperado.nome or "")[:160]
            item.nome_norm = SCALE.norm(cooperado.nome)[:160]

        _meta_recount()
        DB.session.commit()
        SCALE.clear_cache()
        if cooperado:
            flash(f"Escala alterada para {cooperado.nome} em {dados['contrato']}.", "success")
        else:
            flash("O registro foi retirado da escala e marcado como folga/sem substituto.", "success")
    except Exception as exc:
        DB.session.rollback()
        flash(f"Não foi possível alterar a escala: {exc}", "error")

    return _redirect_back()


def _remover(item_id):
    if not _admin():
        return redirect(url_for("login"))

    item = SCALE.Item.query.get_or_404(item_id)
    nome = item.cooperado.nome if item.cooperado else item.nome_planilha
    try:
        _limpar_ajuste_antigo(item.id)
        DB.session.delete(item)
        _meta_recount()
        DB.session.commit()
        SCALE.clear_cache()
        flash(f"{nome} foi removido da escala atual.", "success")
    except Exception as exc:
        DB.session.rollback()
        flash(f"Não foi possível remover da escala: {exc}", "error")
    return _redirect_back()


def install(app_module, escala_feature):
    global DONE, MOD, SCALE, DB
    if DONE:
        return

    MOD = app_module
    SCALE = escala_feature
    DB = app_module.db

    app = app_module.app
    app.add_url_rule("/escala/adicionar", "escala_adicionar", _adicionar, methods=["POST"])
    app.add_url_rule("/escala/editar/<int:item_id>", "escala_editar", _editar, methods=["POST"])
    app.add_url_rule("/escala/remover/<int:item_id>", "escala_remover", _remover, methods=["POST"])
    DONE = True
