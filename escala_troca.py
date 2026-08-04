import uuid
from datetime import datetime

from flask import flash, jsonify, redirect, request, session, url_for

DONE = False
MOD = None
SCALE = None
DB = None


def _admin():
    return bool(session.get("is_admin"))


def _redirect_back():
    return redirect(request.referrer or url_for("escala"))


def _grupo_turno(item):
    texto = SCALE.norm(f"{item.turno or ''} {item.horario_texto or ''}")

    if any(palavra in texto for palavra in ("noite", "noturno", "madrugada")):
        return "noite"
    if any(
        palavra in texto
        for palavra in ("dia", "manha", "tarde", "comercial", "meio")
    ):
        return "dia"

    try:
        intervalos = item.intervalos()
        inicio = str(intervalos[0].get("inicio") or "")
        hora = int(inicio.split(":", 1)[0])
        return "noite" if hora >= 17 or hora < 5 else "dia"
    except Exception:
        pass

    return SCALE.norm(item.turno or "") or str(item.intervalos_json or "[]")


def _turno_compativel(a, b):
    if a.data != b.data:
        return False
    return _grupo_turno(a) == _grupo_turno(b)


def _ajuste_ativo(item_id):
    return SCALE.Ajuste.query.filter_by(item_id=int(item_id)).first()


def _candidatos(item_id):
    if not _admin():
        return jsonify(ok=False, error="Não autorizado"), 401

    item = SCALE.Item.query.get_or_404(item_id)
    if not item.cooperado_id:
        return jsonify(ok=False, error="A escala escolhida está sem cooperado."), 400

    query = SCALE.Item.query.filter(
        SCALE.Item.id != item.id,
        SCALE.Item.data == item.data,
        SCALE.Item.cooperado_id.isnot(None),
    ).order_by(SCALE.Item.contrato, SCALE.Item.horario_texto, SCALE.Item.nome_planilha)

    candidatos = []
    for outro in query.all():
        if not _turno_compativel(item, outro):
            continue
        if int(outro.cooperado_id) == int(item.cooperado_id):
            continue
        if _ajuste_ativo(outro.id):
            continue
        candidatos.append(
            {
                "id": int(outro.id),
                "cooperado_id": int(outro.cooperado_id),
                "cooperado": outro.cooperado.nome if outro.cooperado else outro.nome_planilha,
                "contrato": outro.contrato,
                "turno": outro.turno or "",
                "horario": SCALE.label(outro),
                "data": outro.data.strftime("%d/%m/%Y"),
            }
        )

    return jsonify(
        ok=True,
        origem={
            "id": int(item.id),
            "cooperado_id": int(item.cooperado_id),
            "cooperado": item.cooperado.nome if item.cooperado else item.nome_planilha,
            "contrato": item.contrato,
            "turno": item.turno or "",
            "horario": SCALE.label(item),
            "data": item.data.strftime("%d/%m/%Y"),
        },
        itens=candidatos,
    )


def _trocar():
    if not _admin():
        return redirect(url_for("login"))

    item_a_id = request.form.get("item_a_id", type=int)
    item_b_id = request.form.get("item_b_id", type=int)
    motivo = (request.form.get("motivo") or "").strip()

    if not item_a_id or not item_b_id or item_a_id == item_b_id:
        flash("Escolha duas escalas diferentes para realizar a troca.", "error")
        return _redirect_back()
    if len(motivo) < 3:
        flash("Informe o motivo da troca.", "error")
        return _redirect_back()

    a = SCALE.Item.query.get_or_404(item_a_id)
    b = SCALE.Item.query.get_or_404(item_b_id)

    if not a.cooperado_id or not b.cooperado_id:
        flash("As duas escalas precisam ter cooperados atribuídos.", "error")
        return _redirect_back()
    if not _turno_compativel(a, b):
        flash("A troca só pode ser feita entre escalas do mesmo dia e do mesmo turno.", "error")
        return _redirect_back()
    if int(a.cooperado_id) == int(b.cooperado_id):
        flash("Escolha outro cooperado para a troca.", "error")
        return _redirect_back()
    if _ajuste_ativo(a.id) or _ajuste_ativo(b.id):
        flash("Uma das escalas já possui alteração. Desfaça ou ajuste essa alteração antes de trocar.", "error")
        return _redirect_back()

    coop_a_id = int(a.cooperado_id)
    coop_b_id = int(b.cooperado_id)
    nome_a = a.cooperado.nome if a.cooperado else a.nome_planilha
    nome_b = b.cooperado.nome if b.cooperado else b.nome_planilha
    token = uuid.uuid4().hex[:10]
    motivo_db = f"TROCA:{token}|{motivo[:220]}"

    try:
        DB.session.add(
            SCALE.Ajuste(
                item_id=a.id,
                cooperado_original_id=coop_a_id,
                cooperado_novo_id=coop_b_id,
                motivo=motivo_db[:255],
                alterado_em=datetime.utcnow(),
            )
        )
        DB.session.add(
            SCALE.Ajuste(
                item_id=b.id,
                cooperado_original_id=coop_b_id,
                cooperado_novo_id=coop_a_id,
                motivo=motivo_db[:255],
                alterado_em=datetime.utcnow(),
            )
        )

        a.cooperado_id = coop_b_id
        b.cooperado_id = coop_a_id
        a.status_match = "troca"
        b.status_match = "troca"
        a.detalhe_match = f"Troca com {nome_b}. Motivo: {motivo}"[:255]
        b.detalhe_match = f"Troca com {nome_a}. Motivo: {motivo}"[:255]

        DB.session.commit()
        SCALE.clear_cache()
        flash(
            f"Troca realizada: {nome_a} assumiu {b.contrato} e {nome_b} assumiu {a.contrato}.",
            "success",
        )
    except Exception as exc:
        DB.session.rollback()
        flash(f"Não foi possível realizar a troca: {exc}", "error")

    return _redirect_back()


def _desfazer(item_id):
    if not _admin():
        return redirect(url_for("login"))

    ajuste = _ajuste_ativo(item_id)
    if not ajuste or not str(ajuste.motivo or "").startswith("TROCA:"):
        flash("Esta escala não possui uma troca ativa.", "warning")
        return _redirect_back()

    token = str(ajuste.motivo).split("|", 1)[0]
    pares = SCALE.Ajuste.query.filter(SCALE.Ajuste.motivo.like(token + "|%")).all()
    if len(pares) != 2:
        flash("Não foi possível localizar as duas partes desta troca.", "error")
        return _redirect_back()

    try:
        for par in pares:
            item = SCALE.Item.query.get(par.item_id)
            if item:
                item.cooperado_id = par.cooperado_original_id
                item.status_match = "confirmado" if par.cooperado_original_id else "nao_encontrado"
                item.detalhe_match = "Troca desfeita pelo administrador."
            DB.session.delete(par)
        DB.session.commit()
        SCALE.clear_cache()
        flash("A troca das duas escalas foi desfeita.", "success")
    except Exception as exc:
        DB.session.rollback()
        flash(f"Não foi possível desfazer a troca: {exc}", "error")

    return _redirect_back()


def install(app_module, escala_feature):
    global DONE, MOD, SCALE, DB
    if DONE:
        return

    MOD = app_module
    SCALE = escala_feature
    DB = app_module.db
    app = app_module.app

    app.add_url_rule(
        "/api/escala/troca-candidatos/<int:item_id>",
        "api_escala_troca_candidatos",
        _candidatos,
        methods=["GET"],
    )
    app.add_url_rule(
        "/escala/trocar-dupla",
        "escala_trocar_dupla",
        _trocar,
        methods=["POST"],
    )
    app.add_url_rule(
        "/escala/desfazer-troca/<int:item_id>",
        "escala_desfazer_troca",
        _desfazer,
        methods=["POST"],
    )
    DONE = True
