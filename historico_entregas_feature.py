from datetime import datetime

from sqlalchemy import func, text

DONE = False
VERSION = "historico_2025_ate_2026_06_pago_entregue_v1"


def _aplicar_correcao(app_module):
    db = app_module.db
    Entrega = app_module.Entrega

    db.session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS coopex_data_fix ("
            "chave VARCHAR(120) PRIMARY KEY, "
            "aplicado_em TIMESTAMP NOT NULL, "
            "registros INTEGER NOT NULL DEFAULT 0)"
        )
    )

    aplicado = db.session.execute(
        text("SELECT chave FROM coopex_data_fix WHERE chave=:chave"),
        {"chave": VERSION},
    ).scalar()
    if aplicado:
        db.session.commit()
        return 0

    inicio = datetime(2025, 1, 1)
    fim = datetime(2026, 7, 1)
    cancelados = ["cancelado", "cancelada", "cancelled"]

    query = Entrega.query.filter(
        Entrega.data_envio >= inicio,
        Entrega.data_envio < fim,
        ~func.lower(func.coalesce(Entrega.status, "")).in_(cancelados),
    )

    quantidade = query.count()
    query.update(
        {
            Entrega.status_pagamento: "pago",
            Entrega.status: "Entregue",
        },
        synchronize_session=False,
    )

    db.session.execute(
        text(
            "INSERT INTO coopex_data_fix (chave, aplicado_em, registros) "
            "VALUES (:chave, :aplicado_em, :registros)"
        ),
        {
            "chave": VERSION,
            "aplicado_em": datetime.utcnow(),
            "registros": int(quantidade),
        },
    )
    db.session.commit()
    return int(quantidade)


def install(app_module):
    global DONE
    if DONE:
        return

    with app_module.app.app_context():
        try:
            quantidade = _aplicar_correcao(app_module)
            if quantidade:
                app_module.app.logger.info(
                    "Histórico corrigido: %s entrega(s) de 2025 até junho/2026 marcadas como pagas e entregues.",
                    quantidade,
                )
        except Exception:
            app_module.db.session.rollback()
            app_module.app.logger.exception(
                "Falha ao corrigir o histórico de entregas de 2025 a junho de 2026."
            )
            raise

    DONE = True
