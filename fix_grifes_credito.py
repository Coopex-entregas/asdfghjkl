from app import app, db
from sqlalchemy import text

CLIENTE = "%Grifes%"
DATA_INICIO = "2026-05-26"

with app.app_context():
    cli = db.session.execute(text("""
        SELECT id, nome, saldo_atual
        FROM cliente
        WHERE nome ILIKE :cliente
        ORDER BY id
        LIMIT 1
    """), {"cliente": CLIENTE}).mappings().first()

    if not cli:
        print("Cliente Grifes não encontrado.")
        raise SystemExit(1)

    print(f"Cliente: {cli['id']} - {cli['nome']}")
    print(f"Saldo antes: R$ {cli['saldo_atual']}")

    resultado = db.session.execute(text("""
        WITH ent AS (
            SELECT
                e.id,
                e.valor,
                e.data_envio,
                COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN cm.tipo = 'debito' THEN cm.valor
                            WHEN cm.tipo = 'credito' AND cm.referencia ILIKE '%Estorno%' THEN -cm.valor
                            ELSE 0
                        END
                    )
                    FROM credito_movimento cm
                    WHERE cm.cliente_id = :cliente_id
                    AND (
                        cm.entrega_id = e.id
                        OR cm.referencia ILIKE ('%Entrega #' || e.id::text || '%')
                    )
                ), 0) AS ja_descontado
            FROM entrega e
            WHERE
                (e.cliente_id = :cliente_id OR e.cliente ILIKE :cliente)
                AND e.data_envio >= :data_inicio
                AND (
                    e.pagamento ILIKE '%credito%'
                    OR e.pagamento ILIKE '%crédito%'
                    OR e.pagamento ILIKE '%CREDITO_AUTO%'
                )
        ), atualiza AS (
            UPDATE entrega e
            SET cliente_id = :cliente_id,
                credito_usado = e.valor,
                status_pagamento = 'pago'
            FROM ent
            WHERE e.id = ent.id
            RETURNING e.id
        ), insere AS (
            INSERT INTO credito_movimento
                (cliente_id, entrega_id, tipo, valor, data, criado_em, referencia)
            SELECT
                :cliente_id,
                ent.id,
                'debito',
                ent.valor - ent.ja_descontado,
                ent.data_envio,
                ent.data_envio,
                'Entrega #' || ent.id::text
            FROM ent
            WHERE ent.valor - ent.ja_descontado > 0.009
            RETURNING valor
        )
        SELECT COUNT(*) AS qtd, COALESCE(SUM(valor), 0) AS total
        FROM insere
    """), {
        "cliente_id": cli["id"],
        "cliente": CLIENTE,
        "data_inicio": DATA_INICIO,
    }).mappings().first()

    saldo = db.session.execute(text("""
        WITH calc AS (
            SELECT COALESCE(SUM(
                CASE
                    WHEN tipo = 'credito' THEN valor
                    WHEN tipo = 'debito' THEN -valor
                    ELSE 0
                END
            ), 0) AS saldo
            FROM credito_movimento
            WHERE cliente_id = :cliente_id
        )
        UPDATE cliente
        SET saldo_atual = (SELECT saldo FROM calc)
        WHERE id = :cliente_id
        RETURNING saldo_atual
    """), {"cliente_id": cli["id"]}).scalar()

    db.session.commit()

    print(f"Movimentos criados: {resultado['qtd']}")
    print(f"Total descontado agora: R$ {resultado['total']}")
    print(f"Saldo final: R$ {saldo}")
