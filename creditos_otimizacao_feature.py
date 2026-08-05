from functools import wraps
import re
import unicodedata

from flask import flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import case, func, text

DONE = False
MOD = None
DB = None
ORIGINAL_SYNC = None


def _norm(value):
    normalized = unicodedata.normalize("NFD", str(value or ""))
    normalized = "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    )
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _brl(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def _is_admin():
    return bool(session.get("is_admin"))


def _credit_client_exists(cliente_id):
    if not cliente_id:
        return False
    Credito = MOD.Credito
    return (
        DB.session.query(Credito.id)
        .filter(Credito.cliente_id == int(cliente_id))
        .limit(1)
        .first()
        is not None
    )


def _credit_payment(value):
    checker = getattr(MOD, "pagamento_usa_credito", None)
    if callable(checker):
        try:
            return bool(checker(value))
        except Exception:
            pass
    return _norm(value).startswith("credito")


def _request_payload():
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form


def _find_client(payload, existing_delivery=None):
    Cliente = MOD.Cliente

    raw_id = (
        payload.get("cliente_id")
        or payload.get("client_id")
        or (getattr(existing_delivery, "cliente_id", None) if existing_delivery else None)
        or session.get("cliente_id")
    )
    try:
        client_id = int(raw_id) if raw_id not in (None, "") else None
    except (TypeError, ValueError):
        client_id = None

    if client_id:
        client = Cliente.query.get(client_id)
        if client:
            return client

    name = (
        payload.get("cliente")
        or payload.get("cliente_nome")
        or payload.get("nome_cliente")
        or (getattr(existing_delivery, "cliente", None) if existing_delivery else None)
        or ""
    )
    name = str(name).strip()
    if not name:
        return None

    client = (
        Cliente.query
        .filter(func.lower(func.trim(Cliente.nome)) == name.lower())
        .first()
    )
    if client:
        return client

    wanted = _norm(name)
    for row in DB.session.query(Cliente.id, Cliente.nome).all():
        if _norm(row.nome) == wanted:
            return Cliente.query.get(row.id)
    return None


def _credit_error(message):
    if request.is_json or request.path.startswith("/api/"):
        return jsonify(ok=False, error=message, message=message), 400
    flash(message, "error")
    return redirect(request.referrer or url_for("creditos"))


def _guard_view(endpoint):
    original = MOD.app.view_functions.get(endpoint)
    if not original or getattr(original, "_coopex_credit_guard", False):
        return

    @wraps(original)
    def guarded(*args, **kwargs):
        if request.method in {"POST", "PUT", "PATCH"}:
            payload = _request_payload()
            delivery = None
            delivery_id = kwargs.get("id") or kwargs.get("entrega_id")
            if delivery_id and hasattr(MOD, "Entrega"):
                try:
                    delivery = MOD.Entrega.query.get(int(delivery_id))
                except Exception:
                    delivery = None

            payment = (
                payload.get("pagamento")
                or payload.get("forma_pagamento")
                or payload.get("payment")
                or (getattr(delivery, "pagamento", None) if delivery else None)
            )
            if _credit_payment(payment):
                client = _find_client(payload, delivery)
                if not client or not _credit_client_exists(client.id):
                    return _credit_error(
                        "Este cliente ainda não está habilitado para pagamento no crédito. "
                        "Registre primeiro um crédito na tela Créditos de Clientes."
                    )
        return original(*args, **kwargs)

    guarded._coopex_credit_guard = True
    MOD.app.view_functions[endpoint] = guarded


def _protect_credit_sync():
    global ORIGINAL_SYNC
    original = getattr(MOD, "sincronizar_credito_da_entrega", None)
    if not callable(original) or getattr(original, "_coopex_credit_guard", False):
        return
    ORIGINAL_SYNC = original

    @wraps(original)
    def protected(entrega_id, *args, **kwargs):
        delivery = MOD.Entrega.query.get(entrega_id)
        if delivery and _credit_payment(getattr(delivery, "pagamento", None)):
            finder = getattr(MOD, "_cliente_da_entrega_para_credito", None)
            client = finder(delivery) if callable(finder) else _find_client({}, delivery)
            if not client or not _credit_client_exists(client.id):
                raise ValueError(
                    "Cliente não habilitado para crédito. Registre primeiro um crédito para este cliente."
                )
        return original(entrega_id, *args, **kwargs)

    protected._coopex_credit_guard = True
    MOD.sincronizar_credito_da_entrega = protected


def _summary_rows():
    Cliente = MOD.Cliente
    Credito = MOD.Credito
    Movimento = MOD.CreditoMovimento

    credit_total = (
        DB.session.query(
            Credito.cliente_id.label("cliente_id"),
            func.coalesce(func.sum(Credito.valor_final), 0.0).label("creditos"),
        )
        .filter(Credito.cliente_id.isnot(None))
        .group_by(Credito.cliente_id)
        .subquery()
    )

    movement_total = (
        DB.session.query(
            Movimento.cliente_id.label("cliente_id"),
            func.coalesce(
                func.sum(case((Movimento.tipo == "credito", Movimento.valor), else_=0.0)),
                0.0,
            ).label("mov_creditos"),
            func.coalesce(
                func.sum(case((Movimento.tipo == "debito", Movimento.valor), else_=0.0)),
                0.0,
            ).label("debitos"),
            func.count(Movimento.id).label("movimentos"),
        )
        .group_by(Movimento.cliente_id)
        .subquery()
    )

    rows = (
        DB.session.query(
            Cliente.id,
            Cliente.nome,
            Cliente.saldo_atual,
            credit_total.c.creditos,
            movement_total.c.mov_creditos,
            movement_total.c.debitos,
            movement_total.c.movimentos,
        )
        .join(credit_total, credit_total.c.cliente_id == Cliente.id)
        .outerjoin(movement_total, movement_total.c.cliente_id == Cliente.id)
        .order_by(func.lower(Cliente.nome).asc())
        .all()
    )

    result = []
    for row in rows:
        original_credits = float(row.creditos or 0.0)
        movement_count = int(row.movimentos or 0)
        if movement_count:
            balance = float(row.mov_creditos or 0.0) - float(row.debitos or 0.0)
        else:
            balance = float(row.saldo_atual or original_credits or 0.0)
        consumption = original_credits - balance
        result.append(
            {
                "id": int(row.id),
                "nome": row.nome or f"Cliente #{row.id}",
                "nome_normalizado": _norm(row.nome),
                "saldo": balance,
                "total_creditos": original_credits,
                "total_consumos": consumption,
                "movimentos": movement_count,
            }
        )
    return result


def _creditos_fast():
    if not _is_admin():
        return redirect(url_for("login"))

    Cliente = MOD.Cliente
    selected_id = request.args.get("cliente_id", type=int)
    summaries = _summary_rows()
    summaries_by_id = {item["id"]: item for item in summaries}

    form_rows = (
        DB.session.query(Cliente.id, Cliente.nome, Cliente.saldo_atual)
        .order_by(func.lower(Cliente.nome).asc())
        .all()
    )
    clients_form = []
    for row in form_rows:
        summary = summaries_by_id.get(int(row.id))
        clients_form.append(
            {
                "id": int(row.id),
                "nome": row.nome or f"Cliente #{row.id}",
                "tem_credito": bool(summary),
                "saldo": summary["saldo"] if summary else float(row.saldo_atual or 0.0),
            }
        )

    total_balance = sum(item["saldo"] for item in summaries)
    total_credits = sum(item["total_creditos"] for item in summaries)
    total_consumption = sum(item["total_consumos"] for item in summaries)

    return render_template(
        "creditos_profissional.html",
        clientes_form=clients_form,
        clientes_lista=summaries,
        cliente_id=selected_id,
        total_saldo=total_balance,
        total_creditos=total_credits,
        total_consumos=total_consumption,
    )


def _format_datetime(value):
    if not value:
        return "—"
    converter = getattr(MOD, "to_brasilia", None)
    try:
        converted = converter(value) if callable(converter) else value
        return converted.strftime("%d/%m/%Y, %H:%M:%S")
    except Exception:
        return str(value)


def _client_credit_summary(cliente_id):
    Credito = MOD.Credito
    Movimento = MOD.CreditoMovimento

    original_credits = float(
        DB.session.query(func.coalesce(func.sum(Credito.valor_final), 0.0))
        .filter(Credito.cliente_id == cliente_id)
        .scalar()
        or 0.0
    )
    aggregate = (
        DB.session.query(
            func.coalesce(
                func.sum(case((Movimento.tipo == "credito", Movimento.valor), else_=0.0)),
                0.0,
            ),
            func.coalesce(
                func.sum(case((Movimento.tipo == "debito", Movimento.valor), else_=0.0)),
                0.0,
            ),
            func.count(Movimento.id),
        )
        .filter(Movimento.cliente_id == cliente_id)
        .one()
    )
    movement_credits = float(aggregate[0] or 0.0)
    debits = float(aggregate[1] or 0.0)
    movement_count = int(aggregate[2] or 0)
    balance = movement_credits - debits
    return {
        "saldo": balance,
        "creditos": original_credits,
        "consumos": original_credits - balance,
        "movimentos": movement_count,
    }


def _credit_history(cliente_id):
    if not _is_admin():
        return jsonify(ok=False, error="Não autorizado"), 401
    if not _credit_client_exists(cliente_id):
        return jsonify(ok=False, error="Cliente ainda não habilitado para crédito."), 404

    Cliente = MOD.Cliente
    Movimento = MOD.CreditoMovimento
    client = Cliente.query.get(cliente_id)
    if not client:
        return jsonify(ok=False, error="Cliente não encontrado."), 404

    limit = min(max(request.args.get("limit", type=int) or 60, 1), 100)
    offset = max(request.args.get("offset", type=int) or 0, 0)
    date_order = func.coalesce(Movimento.criado_em, Movimento.data)
    movements = (
        Movimento.query
        .filter(Movimento.cliente_id == cliente_id)
        .order_by(date_order.desc(), Movimento.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    has_more = len(movements) > limit
    movements = movements[:limit]

    items = []
    for movement in movements:
        movement_type = str(movement.tipo or "").lower()
        reference = str(movement.referencia or "").strip()
        is_debit = movement_type == "debito"
        if is_debit:
            type_text = "Consumo"
        elif "estorno" in _norm(reference):
            type_text = "Estorno"
        else:
            type_text = "Crédito"

        if movement.entrega_id:
            link_text = f"Entrega #{movement.entrega_id}"
        elif movement.credito_id:
            link_text = f"Crédito #{movement.credito_id}"
        else:
            link_text = "—"

        edit_url = None
        if movement.credito_id:
            try:
                edit_url = url_for("creditos_editar", credito_id=movement.credito_id)
            except Exception:
                edit_url = None

        items.append(
            {
                "id": int(movement.id),
                "data_texto": _format_datetime(movement.criado_em or movement.data),
                "tipo": movement_type,
                "tipo_texto": type_text,
                "referencia": reference or "—",
                "valor": float(movement.valor or 0.0),
                "vinculo": link_text,
                "editar_url": edit_url,
            }
        )

    return jsonify(
        ok=True,
        cliente={"id": client.id, "nome": client.nome},
        resumo=_client_credit_summary(cliente_id),
        items=items,
        has_more=has_more,
        next_offset=offset + len(items),
    )


def _credit_enabled(cliente_id):
    if not _is_admin():
        return jsonify(ok=False, error="Não autorizado"), 401
    client = MOD.Cliente.query.get(cliente_id)
    if not client:
        return jsonify(ok=False, error="Cliente não encontrado."), 404
    enabled = _credit_client_exists(cliente_id)
    summary = _client_credit_summary(cliente_id) if enabled else {
        "saldo": float(client.saldo_atual or 0.0),
        "creditos": 0.0,
        "consumos": 0.0,
        "movimentos": 0,
    }
    return jsonify(ok=True, habilitado=enabled, cliente_id=cliente_id, resumo=summary)


def _create_indexes():
    statements = [
        "CREATE INDEX IF NOT EXISTS idx_credito_cliente_criado ON credito (cliente_id, criado_em)",
        "CREATE INDEX IF NOT EXISTS idx_credmov_cliente_criado_id ON credito_movimento (cliente_id, criado_em, id)",
        "CREATE INDEX IF NOT EXISTS idx_credmov_cliente_tipo ON credito_movimento (cliente_id, tipo)",
    ]
    try:
        for statement in statements:
            DB.session.execute(text(statement))
        DB.session.commit()
    except Exception:
        DB.session.rollback()
        MOD.app.logger.warning(
            "Não foi possível criar todos os índices adicionais de crédito.",
            exc_info=True,
        )


def install(app_module):
    global DONE, MOD, DB
    if DONE:
        return

    MOD = app_module
    DB = app_module.db
    app_module.app.jinja_env.filters["brl"] = _brl

    with app_module.app.app_context():
        _create_indexes()

    app_module.app.view_functions["creditos"] = _creditos_fast
    app_module.app.add_url_rule(
        "/api/creditos/clientes/<int:cliente_id>/historico",
        "api_creditos_cliente_historico",
        _credit_history,
        methods=["GET"],
    )
    app_module.app.add_url_rule(
        "/api/creditos/clientes/<int:cliente_id>/habilitado",
        "api_creditos_cliente_habilitado",
        _credit_enabled,
        methods=["GET"],
    )

    for endpoint in (
        "cadastrar_entrega",
        "agendar_entrega",
        "editar_entrega",
        "api_pedidos_criar",
        "api_cliente_solicitar_entrega",
    ):
        _guard_view(endpoint)

    _protect_credit_sync()
    app_module.cliente_tem_credito = _credit_client_exists
    DONE = True
