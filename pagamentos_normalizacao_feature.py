import re
import unicodedata

from flask import request
from sqlalchemy import text

DONE = False
VERSION = "pagamentos_canonicos_v1"


def _norm(value):
    normalized = unicodedata.normalize("NFD", str(value or ""))
    normalized = "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    )
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _canonical(value):
    key = _norm(value)
    if not key:
        return value

    if key in {
        "credito",
        "credito auto",
        "credito automatico",
        "credito saldo cliente",
        "credito automatico saldo cliente",
        "saldo cliente",
    }:
        return "CREDITO_AUTO"

    if key in {
        "pix cooperativa",
        "pix da cooperativa",
        "pix coopex",
        "pix da coopex",
    }:
        return "Pix (Cooperativa)"

    if key == "pix":
        return "Pix"
    if key == "dinheiro":
        return "Dinheiro"
    if key == "comanda":
        return "Comanda"

    return str(value or "").strip()


def _normalize_existing(app_module):
    db = app_module.db
    Entrega = app_module.Entrega

    db.session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS pagamentos_normalizacao_meta "
            "(chave VARCHAR(80) PRIMARY KEY, valor VARCHAR(255) NOT NULL)"
        )
    )
    current = db.session.execute(
        text("SELECT valor FROM pagamentos_normalizacao_meta WHERE chave=:chave"),
        {"chave": "versao"},
    ).scalar()
    if current == VERSION:
        db.session.commit()
        return

    values = [
        row[0]
        for row in db.session.query(Entrega.pagamento)
        .filter(Entrega.pagamento.isnot(None))
        .distinct()
        .all()
        if row[0] is not None
    ]

    for original in values:
        canonical = _canonical(original)
        if canonical and canonical != original:
            (
                Entrega.query
                .filter(Entrega.pagamento == original)
                .update({Entrega.pagamento: canonical}, synchronize_session=False)
            )

    db.session.execute(
        text("DELETE FROM pagamentos_normalizacao_meta WHERE chave=:chave"),
        {"chave": "versao"},
    )
    db.session.execute(
        text(
            "INSERT INTO pagamentos_normalizacao_meta (chave, valor) "
            "VALUES (:chave, :valor)"
        ),
        {"chave": "versao", "valor": VERSION},
    )
    db.session.commit()


def _inject_admin_filter_fix(response):
    try:
        if request.endpoint != "admin":
            return response
        if response.status_code != 200 or response.mimetype != "text/html":
            return response

        html = response.get_data(as_text=True)
        asset = (
            '<script defer src="/static/js/admin_payment_filter_patch.js'
            '?v=20260805-1"></script>'
        )
        if asset not in html:
            if "</body>" in html:
                html = html.replace("</body>", asset + "</body>", 1)
            else:
                html += asset
            response.set_data(html)
            response.headers.pop("Content-Length", None)
    except Exception:
        pass
    return response


def install(app_module):
    global DONE
    if DONE:
        return

    with app_module.app.app_context():
        try:
            _normalize_existing(app_module)
        except Exception:
            app_module.db.session.rollback()
            app_module.app.logger.exception(
                "Falha ao normalizar as formas de pagamento existentes."
            )
            raise

    app_module.app.after_request(_inject_admin_filter_fix)
    DONE = True
