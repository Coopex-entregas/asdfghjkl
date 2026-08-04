import base64
import json
import os
import re
import unicodedata
import zlib
from decimal import Decimal

from flask import jsonify, request, session
from sqlalchemy import text

DONE = False
MOD = None
DB = None
DATA = None
BAIRROS = []
ROTA_MAP = {}
ALIASES = {}
VERSION = None


def _norm(value):
    text_value = unicodedata.normalize("NFD", str(value or ""))
    text_value = "".join(
        char for char in text_value if unicodedata.category(char) != "Mn"
    )
    text_value = text_value.lower()
    text_value = re.sub(r"[^a-z0-9]+", " ", text_value)
    return re.sub(r"\s+", " ", text_value).strip()


def _admin():
    return bool(session.get("is_admin"))


def _load():
    global DATA, BAIRROS, ROTA_MAP, ALIASES, VERSION
    if DATA is not None:
        return

    path = os.path.join(os.path.dirname(__file__), "data", "rotas_coopex_2026.b85")
    with open(path, "r", encoding="utf-8") as file_handle:
        payload = "".join(file_handle.read().split())

    raw = zlib.decompress(base64.b85decode(payload.encode("ascii")))
    DATA = json.loads(raw.decode("utf-8"))
    VERSION = str(DATA.get("version") or "sem-versao")
    BAIRROS = list(DATA.get("bairros") or [])
    ROTA_MAP = {
        (_norm(origem), _norm(destino)): float(valor)
        for origem, destino, valor in (DATA.get("rotas") or [])
    }

    aliases = {}
    for bairro in BAIRROS:
        label = str(bairro.get("label") or "").strip()
        nome = str(bairro.get("nome") or "").strip()
        cidade = str(bairro.get("cidade") or "").strip()
        for alias in {label, nome, f"{nome}, {cidade}", f"{nome} {cidade}"}:
            key = _norm(alias)
            if key:
                aliases.setdefault(key, set()).add(label)
    ALIASES = aliases


def _resolve(value):
    _load()
    key = _norm(value)
    if not key:
        return None, []

    exact = sorted(ALIASES.get(key) or [])
    if len(exact) == 1:
        return exact[0], exact
    if len(exact) > 1:
        return None, exact

    candidates = []
    for bairro in BAIRROS:
        label = str(bairro.get("label") or "")
        label_key = _norm(label)
        name_key = _norm(bairro.get("nome") or "")
        if label_key.startswith(key) or name_key.startswith(key):
            candidates.append(label)
    candidates = sorted(set(candidates))
    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates[:12]


def _sync_prices():
    _load()
    model = getattr(MOD, "PrecoRota", None)
    if model is None:
        raise RuntimeError("Modelo PrecoRota não encontrado.")

    DB.session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS rotas_coopex_meta "
            "(chave VARCHAR(80) PRIMARY KEY, valor VARCHAR(255) NOT NULL)"
        )
    )
    current = DB.session.execute(
        text("SELECT valor FROM rotas_coopex_meta WHERE chave=:chave"),
        {"chave": "versao_bairros"},
    ).scalar()

    if current == VERSION:
        DB.session.commit()
        return

    model.query.delete(synchronize_session=False)
    has_active = hasattr(model, "ativo")
    batch = []
    for origem, destino, valor in DATA.get("rotas") or []:
        kwargs = {
            "origem": origem,
            "destino": destino,
            "valor": Decimal(str(valor)),
        }
        if has_active:
            kwargs["ativo"] = True
        batch.append(model(**kwargs))

    DB.session.add_all(batch)
    DB.session.execute(
        text("DELETE FROM rotas_coopex_meta WHERE chave=:chave"),
        {"chave": "versao_bairros"},
    )
    DB.session.execute(
        text("INSERT INTO rotas_coopex_meta (chave, valor) VALUES (:chave, :valor)"),
        {"chave": "versao_bairros", "valor": VERSION},
    )
    DB.session.commit()


def _suggestions():
    if not _admin():
        return jsonify(ok=False, error="Não autorizado"), 401

    _load()
    query = _norm(request.args.get("q") or "")
    limit = min(max(request.args.get("limit", type=int) or 10, 1), 20)
    if not query:
        return jsonify(ok=True, items=[])

    scored = []
    for bairro in BAIRROS:
        label = str(bairro.get("label") or "")
        name = str(bairro.get("nome") or "")
        city = str(bairro.get("cidade") or "")
        label_key = _norm(label)
        name_key = _norm(name)
        city_key = _norm(city)
        if not (query in label_key or query in name_key or query in city_key):
            continue

        if label_key == query or name_key == query:
            score = 0
        elif label_key.startswith(query) or name_key.startswith(query):
            score = 1
        else:
            score = 2
        scored.append(
            (score, label_key, {"label": label, "nome": name, "cidade": city})
        )

    scored.sort(key=lambda item: (item[0], item[1]))
    return jsonify(ok=True, items=[item[2] for item in scored[:limit]])


def _resolve_api():
    if not _admin():
        return jsonify(ok=False, error="Não autorizado"), 401

    value = request.args.get("q") or ""
    label, options = _resolve(value)
    if label:
        return jsonify(ok=True, label=label)
    if options:
        return jsonify(
            ok=False,
            ambiguous=True,
            error="Escolha o bairro correto.",
            options=options,
        ), 409
    return jsonify(ok=False, error="Bairro não cadastrado."), 404


def _price():
    if not _admin():
        return jsonify(ok=False, error="Não autorizado"), 401

    origin_raw = request.args.get("origem") or ""
    destination_raw = request.args.get("destino") or ""
    origin, origin_options = _resolve(origin_raw)
    destination, destination_options = _resolve(destination_raw)

    if not origin:
        return jsonify(
            ok=False,
            campo="origem",
            ambiguous=bool(origin_options),
            error=(
                "Escolha o bairro correto da coleta."
                if origin_options
                else "Bairro da coleta não cadastrado."
            ),
            options=origin_options,
        ), 409 if origin_options else 404

    if not destination:
        return jsonify(
            ok=False,
            campo="destino",
            ambiguous=bool(destination_options),
            error=(
                "Escolha o bairro correto da entrega."
                if destination_options
                else "Bairro da entrega não cadastrado."
            ),
            options=destination_options,
        ), 409 if destination_options else 404

    value = ROTA_MAP.get((_norm(origin), _norm(destination)))
    if value is None:
        value = ROTA_MAP.get((_norm(destination), _norm(origin)))

    if value is None:
        return jsonify(
            ok=False,
            origem=origin,
            destino=destination,
            error="Rota sem valor cadastrado.",
        ), 404

    return jsonify(
        ok=True,
        origem=origin,
        destino=destination,
        valor=float(value),
        versao=VERSION,
    )


def install(app_module):
    global DONE, MOD, DB
    if DONE:
        return

    MOD = app_module
    DB = app_module.db
    _load()

    with app_module.app.app_context():
        try:
            _sync_prices()
        except Exception:
            DB.session.rollback()
            app_module.app.logger.exception(
                "Falha ao atualizar a tabela de bairros COOPEX."
            )
            raise

    app_module.app.add_url_rule(
        "/api/bairros/sugestoes",
        "api_bairros_sugestoes",
        _suggestions,
        methods=["GET"],
    )
    app_module.app.add_url_rule(
        "/api/bairros/resolver",
        "api_bairros_resolver",
        _resolve_api,
        methods=["GET"],
    )
    app_module.app.add_url_rule(
        "/api/bairros/preco",
        "api_bairros_preco",
        _price,
        methods=["GET"],
    )
    DONE = True
