from flask import jsonify, session

DONE = False


def install(app_module, escala_feature):
    """Evita lazy-load de Cooperado em objetos de escala guardados no cache."""
    global DONE
    if DONE:
        return

    app = app_module.app
    db = app_module.db
    Cooperado = app_module.Cooperado

    def api_escala_agora_segura():
        if not session.get("is_admin"):
            return jsonify(ok=False, error="Não autorizado"), 401

        contexto = escala_feature.context_now()
        itens_escala = list(contexto.get("itens") or [])
        cooperado_ids = sorted(
            {
                int(item.cooperado_id)
                for item in itens_escala
                if getattr(item, "cooperado_id", None)
            }
        )

        nomes = {}
        if cooperado_ids:
            nomes = {
                int(cooperado_id): nome
                for cooperado_id, nome in (
                    db.session.query(Cooperado.id, Cooperado.nome)
                    .filter(Cooperado.id.in_(cooperado_ids))
                    .all()
                )
            }

        itens = []
        for item in itens_escala:
            cooperado_id = int(item.cooperado_id)
            itens.append(
                {
                    "cooperado_id": cooperado_id,
                    "nome": nomes.get(cooperado_id)
                    or getattr(item, "nome_planilha", ""),
                    "contrato": getattr(item, "contrato", ""),
                    "horario": escala_feature.label(item),
                }
            )

        return jsonify(
            ok=True,
            restricao_ativa=bool(contexto.get("restricao_ativa")),
            agora=contexto["agora"].isoformat(),
            cooperados_ids=sorted(contexto.get("ids") or []),
            itens=itens,
        )

    app.view_functions["api_escala_agora"] = api_escala_agora_segura
    DONE = True
