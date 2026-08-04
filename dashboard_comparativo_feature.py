import calendar
import threading
import time
from datetime import datetime

import pytz
from flask import jsonify, request, session
from sqlalchemy import Integer, cast, extract, func

DONE = False
MOD = None
DB = None
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 60
_CACHE_MAX = 50
MIN_YEAR = 2025

MESES = [
    "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
    "Jul", "Ago", "Set", "Out", "Nov", "Dez",
]


def _admin():
    return bool(session.get("is_admin"))


def _float(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _pct(atual, anterior):
    atual = _float(atual)
    anterior = _float(anterior)
    if abs(anterior) < 0.000001:
        return None
    return round(((atual - anterior) / anterior) * 100.0, 1)


def _local_timezone():
    return getattr(MOD, "BRAZIL_TZ", pytz.timezone("America/Sao_Paulo"))


def _local_to_utc_naive(value):
    tz = _local_timezone()
    if value.tzinfo is None:
        value = tz.localize(value)
    return value.astimezone(pytz.UTC).replace(tzinfo=None)


def _same_date_previous_year(value):
    year = value.year - 1
    day = min(value.day, calendar.monthrange(year, value.month)[1])
    return value.replace(year=year, day=day)


def _base_query():
    Entrega = MOD.Entrega
    query = Entrega.query

    cooperado_id = (request.args.get("cooperado_id") or "todos").strip()
    if cooperado_id.isdigit():
        query = query.filter(Entrega.cooperado_id == int(cooperado_id))

    status_pagamento = (request.args.get("status_pagamento") or "todos").strip().lower()
    if status_pagamento in {"pago", "pendente"}:
        query = query.filter(
            func.lower(func.coalesce(Entrega.status_pagamento, "")) == status_pagamento
        )

    cliente = (request.args.get("cliente") or "").strip()
    if cliente:
        query = query.filter(Entrega.cliente.ilike(f"%{cliente}%"))

    grupo_id = (request.args.get("grupo_id") or "todos").strip()
    if grupo_id.isdigit() and hasattr(MOD, "GrupoCliente"):
        grupo = MOD.GrupoCliente.query.get(int(grupo_id))
        nomes = [
            str(nome or "").strip().lower()
            for nome in (grupo.nomes_lista() if grupo else [])
            if str(nome or "").strip()
        ]
        if nomes:
            query = query.filter(func.lower(Entrega.cliente).in_(nomes))

    origem = (request.args.get("origem") or "").strip()
    if origem:
        query = query.filter(Entrega.origem_json.ilike(f"%{origem}%"))

    destino = (request.args.get("destino") or "").strip()
    if destino:
        query = query.filter(Entrega.destino_json.ilike(f"%{destino}%"))

    return query


def _cache_key(ano):
    filtros = (
        request.args.get("cooperado_id") or "todos",
        request.args.get("grupo_id") or "todos",
        request.args.get("cliente") or "",
        request.args.get("status_pagamento") or "todos",
        request.args.get("origem") or "",
        request.args.get("destino") or "",
    )
    return (int(ano),) + tuple(str(x).strip().lower() for x in filtros)


def _cache_get(key):
    now = time.time()
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        if now - item[0] > _CACHE_TTL:
            _CACHE.pop(key, None)
            return None
        return item[1]


def _cache_set(key, value):
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            oldest = min(_CACHE, key=lambda item: _CACHE[item][0])
            _CACHE.pop(oldest, None)
        _CACHE[key] = (time.time(), value)


def _aggregate_range(query, start_utc, end_utc):
    Entrega = MOD.Entrega
    row = query.filter(
        Entrega.data_envio >= start_utc,
        Entrega.data_envio < end_utc,
    ).with_entities(
        func.count(Entrega.id),
        func.coalesce(func.sum(Entrega.valor), 0),
    ).first()
    qtd = int((row[0] if row else 0) or 0)
    valor = _float(row[1] if row else 0)
    return {
        "qtd": qtd,
        "valor": round(valor, 2),
        "ticket": round(valor / qtd, 2) if qtd else 0.0,
    }


def _zero_summary():
    return {"qtd": 0, "valor": 0.0, "ticket": 0.0}


def _build_payload(ano):
    Entrega = MOD.Entrega
    query = _base_query()
    comparacao_disponivel = ano > MIN_YEAR
    ano_anterior = ano - 1 if comparacao_disponivel else None

    year_expr = cast(extract("year", Entrega.data_envio), Integer)
    month_expr = cast(extract("month", Entrega.data_envio), Integer)

    anos_consulta = [ano]
    if ano_anterior is not None:
        anos_consulta.append(ano_anterior)

    monthly_rows = query.filter(
        year_expr.in_(anos_consulta)
    ).with_entities(
        year_expr.label("ano"),
        month_expr.label("mes"),
        func.count(Entrega.id).label("qtd"),
        func.coalesce(func.sum(Entrega.valor), 0).label("valor"),
    ).group_by(year_expr, month_expr).order_by(year_expr, month_expr).all()

    monthly_map = {}
    for row in monthly_rows:
        monthly_map[(int(row.ano), int(row.mes))] = {
            "qtd": int(row.qtd or 0),
            "valor": _float(row.valor),
        }

    meses = []
    for mes in range(1, 13):
        atual = monthly_map.get((ano, mes), {"qtd": 0, "valor": 0.0})
        anterior = (
            monthly_map.get((ano_anterior, mes), {"qtd": 0, "valor": 0.0})
            if ano_anterior is not None
            else {"qtd": 0, "valor": 0.0}
        )
        valor_pct = _pct(atual["valor"], anterior["valor"]) if comparacao_disponivel else None
        qtd_pct = _pct(atual["qtd"], anterior["qtd"]) if comparacao_disponivel else None

        if not comparacao_disponivel:
            melhor = str(ano) if atual["valor"] else "Sem dados"
        elif atual["valor"] > anterior["valor"]:
            melhor = str(ano)
        elif anterior["valor"] > atual["valor"]:
            melhor = str(ano_anterior)
        elif atual["valor"] or anterior["valor"]:
            melhor = "Empate"
        else:
            melhor = "Sem dados"

        meses.append({
            "mes": mes,
            "label": MESES[mes - 1],
            "atual_qtd": atual["qtd"],
            "anterior_qtd": anterior["qtd"],
            "atual_valor": round(atual["valor"], 2),
            "anterior_valor": round(anterior["valor"], 2),
            "valor_pct": valor_pct,
            "qtd_pct": qtd_pct,
            "melhor": melhor,
        })

    annual_rows = query.filter(
        year_expr >= MIN_YEAR
    ).with_entities(
        year_expr.label("ano"),
        func.count(Entrega.id).label("qtd"),
        func.coalesce(func.sum(Entrega.valor), 0).label("valor"),
    ).group_by(year_expr).order_by(year_expr.desc()).all()

    now_local = datetime.now(_local_timezone())
    anos = []
    for row in annual_rows:
        row_year = int(row.ano)
        if row_year < MIN_YEAR:
            continue
        qtd = int(row.qtd or 0)
        valor = _float(row.valor)
        anos.append({
            "ano": row_year,
            "qtd": qtd,
            "valor": round(valor, 2),
            "ticket": round(valor / qtd, 2) if qtd else 0.0,
            "parcial": row_year == now_local.year,
        })

    anos_disponiveis = sorted(
        set([ano, MIN_YEAR] + [item["ano"] for item in anos if item["ano"] >= MIN_YEAR]),
        reverse=True,
    )

    if ano == now_local.year:
        start_current_local = now_local.replace(
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end_current_local = now_local
        corte_label = f"01/01 a {now_local.strftime('%d/%m')}"
        comparacao_tipo = "Mesmo período" if comparacao_disponivel else "Primeiro ano do sistema"
    else:
        start_current_local = _local_timezone().localize(datetime(ano, 1, 1))
        end_current_local = _local_timezone().localize(datetime(ano + 1, 1, 1))
        corte_label = "Ano completo"
        comparacao_tipo = "Ano fechado" if comparacao_disponivel else "Primeiro ano do sistema"

    atual_acumulado = _aggregate_range(
        query,
        _local_to_utc_naive(start_current_local),
        _local_to_utc_naive(end_current_local),
    )

    if comparacao_disponivel:
        if ano == now_local.year:
            start_previous_local = start_current_local.replace(year=ano_anterior)
            end_previous_local = _same_date_previous_year(now_local)
        else:
            start_previous_local = _local_timezone().localize(datetime(ano_anterior, 1, 1))
            end_previous_local = _local_timezone().localize(datetime(ano, 1, 1))
        anterior_acumulado = _aggregate_range(
            query,
            _local_to_utc_naive(start_previous_local),
            _local_to_utc_naive(end_previous_local),
        )
    else:
        anterior_acumulado = _zero_summary()

    valor_diff = (
        round(atual_acumulado["valor"] - anterior_acumulado["valor"], 2)
        if comparacao_disponivel
        else 0.0
    )
    qtd_diff = (
        atual_acumulado["qtd"] - anterior_acumulado["qtd"]
        if comparacao_disponivel
        else 0
    )
    valor_pct = (
        _pct(atual_acumulado["valor"], anterior_acumulado["valor"])
        if comparacao_disponivel
        else None
    )
    qtd_pct = (
        _pct(atual_acumulado["qtd"], anterior_acumulado["qtd"])
        if comparacao_disponivel
        else None
    )

    meses_com_dados = [item for item in meses if item["atual_valor"] > 0]
    melhor_mes_atual = max(
        meses_com_dados,
        key=lambda item: item["atual_valor"],
        default=None,
    )
    melhor_ano = max(anos, key=lambda item: item["valor"], default=None)

    return {
        "ok": True,
        "ano": ano,
        "ano_inicial": MIN_YEAR,
        "ano_anterior": ano_anterior,
        "comparacao_disponivel": comparacao_disponivel,
        "comparacao_tipo": comparacao_tipo,
        "corte_label": corte_label,
        "atual": atual_acumulado,
        "anterior": anterior_acumulado,
        "diferenca": {
            "valor": valor_diff,
            "qtd": qtd_diff,
            "valor_pct": valor_pct,
            "qtd_pct": qtd_pct,
        },
        "melhor_mes": melhor_mes_atual,
        "melhor_ano": melhor_ano,
        "meses": meses,
        "anos": anos,
        "anos_disponiveis": anos_disponiveis,
        "gerado_em": now_local.strftime("%d/%m/%Y %H:%M"),
    }


def _api():
    if not _admin():
        return jsonify(ok=False, error="Não autorizado"), 401

    now_local = datetime.now(_local_timezone())
    ano = request.args.get("ano", type=int) or now_local.year
    if ano < MIN_YEAR or ano > now_local.year + 1:
        return jsonify(
            ok=False,
            error=f"Ano inválido. O sistema possui dados a partir de {MIN_YEAR}.",
        ), 400

    key = _cache_key(ano)
    cached = _cache_get(key)
    if cached is not None:
        return jsonify(cached)

    try:
        payload = _build_payload(ano)
        _cache_set(key, payload)
        return jsonify(payload)
    except Exception as exc:
        DB.session.rollback()
        MOD.app.logger.exception("Falha no comparativo anual do dashboard.")
        return jsonify(ok=False, error=f"Não foi possível gerar o comparativo: {exc}"), 500


def install(app_module):
    global DONE, MOD, DB
    if DONE:
        return

    MOD = app_module
    DB = app_module.db
    app_module.app.add_url_rule(
        "/api/dashboard/comparativo-anual",
        "api_dashboard_comparativo_anual",
        _api,
        methods=["GET"],
    )
    DONE = True
