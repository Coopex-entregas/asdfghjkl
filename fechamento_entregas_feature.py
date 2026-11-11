"""Fechamento automático de entregas antigas da Supervisão COOPEX.

À virada de cada dia em Natal/RN, encerra entregas de cooperados com
data_envio anterior ao início do dia local. Não altera status_pagamento,
não altera canceladas, nem substitui recebedores informados.
"""
import logging
import threading
from datetime import datetime, time
import pytz
from sqlalchemy import text

NATAL_TZ = pytz.timezone("America/Fortaleza")
_LOG = logging.getLogger(__name__)
_WAIT_SECONDS = 30


def _inicio_hoje_utc_naive():
    hoje_local = datetime.now(NATAL_TZ).date()
    inicio_local = NATAL_TZ.localize(datetime.combine(hoje_local, time.min))
    return hoje_local, inicio_local.astimezone(pytz.utc).replace(tzinfo=None)


def fechar_pendencias_antigas(app_module):
    """Fecha no banco apenas entregas de cooperados anteriores a hoje.

    Executa uma única instrução SQL atômica e informa quantos registros
    foram atualizados. Retorno seguro para execução repetida/reinicialização.
    """
    hoje, cutoff = _inicio_hoje_utc_naive()
    sql = text("""
        UPDATE entrega
        SET
            status = 'recebido',
            status_corrida = 'finalizada',
            recebido_por = CASE
                WHEN recebido_por IS NULL OR btrim(recebido_por) = ''
                    THEN 'Não informado'
                ELSE recebido_por
            END
        WHERE cooperado_id IS NOT NULL
          AND data_envio < :cutoff
          AND lower(coalesce(status, '')) NOT IN ('cancelado', 'cancelada')
          AND (
              lower(coalesce(status, '')) NOT IN
                  ('recebido', 'entregue', 'finalizada', 'finalizado')
              OR lower(coalesce(status_corrida, '')) != 'finalizada'
              OR recebido_por IS NULL OR btrim(recebido_por) = ''
          )
    """)
    with app_module.app.app_context():
        # Transação única: se falhar, nenhuma entrega será parcialmente alterada.
        with app_module.db.engine.begin() as conn:
            resultado = conn.execute(sql, {"cutoff": cutoff})
            alteradas = int(resultado.rowcount or 0)
            if alteradas:
                # Invalida cache da Supervisão e do painel cooperado.
                conn.execute(text("""
                    UPDATE supervisao_live_version
                    SET versao = versao + 1
                    WHERE id = 1
                """))
        if alteradas:
            _LOG.info(
                "COOPEX: fechamento do dia %s aplicado a %s entregas antigas.",
                hoje.isoformat(), alteradas
            )
        return alteradas


def install(app_module):
    """Inicia verificação barata, uma vez por dia, no worker Gunicorn.

    Se o worker reiniciar ou a aplicação estiver indisponível à meia-noite,
    aplica o fechamento anterior ao iniciar. Em dias normais verifica a data
    local a cada 30 segundos, sem consultar o banco a cada intervalo.
    """
    app = app_module.app
    if app.extensions.get("coopex_fechamento_meia_noite"):
        return
    app.extensions["coopex_fechamento_meia_noite"] = True

    def worker():
        ultima_data_concluida = None
        while True:
            hoje = datetime.now(NATAL_TZ).date()
            if hoje != ultima_data_concluida:
                try:
                    fechar_pendencias_antigas(app_module)
                    ultima_data_concluida = hoje
                except Exception:
                    _LOG.exception(
                        "COOPEX: falha no fechamento automático; nova tentativa em %ss",
                        _WAIT_SECONDS
                    )
            threading.Event().wait(_WAIT_SECONDS)

    thread = threading.Thread(
        target=worker,
        name="coopex-fechamento-entregas-meia-noite",
        daemon=True,
    )
    thread.start()
