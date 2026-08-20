# -*- coding: utf-8 -*-
"""
Supervisão — sincronização offline-first.

Objetivo:
- O EXE trabalha SEMPRE no SQLite local.
- O Render/PostgreSQL é sincronizado em segundo plano quando disponível.
- Se a internet cair ou o Render estiver em deploy, o app continua funcionando.
- Quando o servidor volta, a fila local é enviada e as mudanças remotas são baixadas.
- A primeira sincronização copia os dados do Render para o SQLite local.
- IDs inteiros são reservados no PostgreSQL em blocos para permitir novos cadastros
  offline sem colisão com os IDs criados pelo site.
- Mudanças são capturadas por triggers, portanto as rotas antigas do sistema não
  precisam ser reescritas uma por uma.

O módulo também configura o SQLite para desempenho local (WAL/cache/mmap).

Observação:
- Telemetria de alta frequência (GPS/trajetos) é deliberadamente excluída da
  replicação contínua para não transformar o app local em um coletor pesado.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, time as dt_time
from decimal import Decimal
from pathlib import Path

from flask import jsonify, request, session, send_file
from sqlalchemy import MetaData, Table, and_, delete, event, inspect, select, text
from sqlalchemy.sql.sqltypes import (
    BigInteger, Boolean, Date, DateTime, Float, Integer, LargeBinary,
    Numeric, Time,
)

LOG = logging.getLogger("supervisao.sync")

SYNC_VERSION = 1
REMOTE_DEFAULT = "https://escalas-2-1.onrender.com"
PULL_INTERVAL = 2.0
PULL_LIMIT = 500
BOOTSTRAP_PAGE = 800
ID_BLOCK_SIZE = 100000

SYNC_INTERNAL_PREFIX = "sync_"
EXCLUDED_EXACT = {
    "alembic_version",
    "localizacao_cooperado",
    "supervisao_live_version",
}
EXCLUDED_PARTS = (
    "trajeto",      # histórico/GPS pode gerar milhares de registros
    "localizacao",  # telemetria de alta frequência
)

COOPERADO_VOLATILE = {
    "last_lat",
    "last_lng",
    "last_ping",
    "online",
    "last_speed_kmh",
    "last_heading",
    "last_accuracy_m",
    "last_moving_at",
}


def _desktop() -> bool:
    return os.environ.get("SUPERVISAO_DESKTOP") == "1"


def _q_sqlite(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _safe_trigger_name(table: str, suffix: str) -> str:
    h = hashlib.sha1(table.encode("utf-8")).hexdigest()[:12]
    return f"sync_{h}_{suffix}"


def _table_excluded(name: str) -> bool:
    low = (name or "").lower()
    if low.startswith(SYNC_INTERNAL_PREFIX) or low.startswith("sqlite_"):
        return True
    if low in EXCLUDED_EXACT:
        return True
    return any(part in low for part in EXCLUDED_PARTS)


def _json_safe(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (datetime, date, dt_time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"__sync_bytes__": base64.b64encode(bytes(v)).decode("ascii")}
    if isinstance(v, dict):
        return {str(k): _json_safe(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return str(v)


def _parse_iso_datetime(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _coerce_server_value(column, value):
    """Converte JSON recebido do desktop para o tipo esperado pelo PostgreSQL."""
    if value is None:
        return None

    typ = column.type

    if isinstance(typ, LargeBinary):
        if isinstance(value, dict) and "__sync_bytes__" in value:
            return base64.b64decode(value["__sync_bytes__"])
        if isinstance(value, str) and value.startswith("__hex__:"):
            return bytes.fromhex(value[8:])
        if isinstance(value, str) and value.startswith("\\x"):
            return bytes.fromhex(value[2:])
        if isinstance(value, str):
            return value.encode("utf-8")
        return bytes(value)

    if isinstance(typ, Boolean):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "t", "yes", "sim")
        return bool(value)

    if isinstance(typ, (Integer, BigInteger)):
        if value == "":
            return None
        return int(value)

    if isinstance(typ, Float):
        if value == "":
            return None
        return float(value)

    if isinstance(typ, Numeric):
        if value == "":
            return None
        return Decimal(str(value))

    if isinstance(typ, DateTime):
        if isinstance(value, datetime):
            return value
        dtv = _parse_iso_datetime(value)
        if dtv is not None:
            # Modelos atuais usam DateTime naive em vários pontos.
            if getattr(typ, "timezone", False):
                return dtv
            return dtv.replace(tzinfo=None)
        return value

    if isinstance(typ, Date):
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except Exception:
            return value

    if isinstance(typ, Time):
        if isinstance(value, dt_time):
            return value
        try:
            return dt_time.fromisoformat(str(value))
        except Exception:
            return value

    # JSON/JSONB: quando o SQLite gravou uma string JSON, recupera o objeto.
    type_name = typ.__class__.__name__.lower()
    if "json" in type_name and isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value

    return value


def _sqlite_value(value, declared_type: str = ""):
    if isinstance(value, dict) and "__sync_bytes__" in value:
        return base64.b64decode(value["__sync_bytes__"])
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, str) and declared_type.upper().find("BLOB") >= 0:
        if value.startswith("\\x"):
            try:
                return bytes.fromhex(value[2:])
            except Exception:
                return value.encode("utf-8")
        if value.startswith("__hex__:"):
            try:
                return bytes.fromhex(value[8:])
            except Exception:
                return value.encode("utf-8")
    return value


def _syncable_tables(engine):
    insp = inspect(engine)
    names = []
    for name in insp.get_table_names():
        if _table_excluded(name):
            continue
        pk = (insp.get_pk_constraint(name) or {}).get("constrained_columns") or []
        if not pk:
            continue
        names.append(name)
    return sorted(names)


# ---------------------------------------------------------------------------
# SERVIDOR / RENDER
# ---------------------------------------------------------------------------

def _ensure_server_schema(app_module):
    db = app_module.db
    engine = db.engine
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sync_change_log (
                seq BIGSERIAL PRIMARY KEY,
                table_name TEXT NOT NULL,
                op CHAR(1) NOT NULL,
                row_json JSONB NOT NULL,
                origin_device TEXT NULL,
                origin_event TEXT NULL,
                changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_sync_change_log_table_seq
            ON sync_change_log(table_name, seq)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sync_applied_event (
                event_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sync_id_pool (
                instance_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                start_id BIGINT NOT NULL,
                end_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY(instance_id, table_name)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sync_conflict_log (
                id BIGSERIAL PRIMARY KEY,
                event_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                resolution TEXT NOT NULL,
                client_row JSONB NULL,
                server_row JSONB NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))

        # A origem é recebida via set_config() na mesma transação do push.
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION coopex_sync_log_change()
            RETURNS trigger AS $$
            DECLARE
                origin_dev TEXT;
                origin_evt TEXT;
            BEGIN
                IF TG_TABLE_NAME = 'cooperado' AND TG_OP = 'UPDATE' THEN
                    IF (
                        (to_jsonb(NEW)
                          - 'last_lat' - 'last_lng' - 'last_ping' - 'online'
                          - 'last_speed_kmh' - 'last_heading' - 'last_accuracy_m'
                          - 'last_moving_at')
                        =
                        (to_jsonb(OLD)
                          - 'last_lat' - 'last_lng' - 'last_ping' - 'online'
                          - 'last_speed_kmh' - 'last_heading' - 'last_accuracy_m'
                          - 'last_moving_at')
                    ) THEN
                        RETURN NEW;
                    END IF;
                END IF;

                origin_dev := NULLIF(current_setting('coopex.sync_origin', true), '');
                origin_evt := NULLIF(current_setting('coopex.sync_event', true), '');

                IF TG_OP = 'DELETE' THEN
                    INSERT INTO sync_change_log(table_name, op, row_json, origin_device, origin_event)
                    VALUES (TG_TABLE_NAME, 'D', to_jsonb(OLD), origin_dev, origin_evt);
                    RETURN OLD;
                ELSIF TG_OP = 'INSERT' THEN
                    INSERT INTO sync_change_log(table_name, op, row_json, origin_device, origin_event)
                    VALUES (TG_TABLE_NAME, 'I', to_jsonb(NEW), origin_dev, origin_evt);
                    RETURN NEW;
                ELSE
                    INSERT INTO sync_change_log(table_name, op, row_json, origin_device, origin_event)
                    VALUES (TG_TABLE_NAME, 'U', to_jsonb(NEW), origin_dev, origin_evt);
                    RETURN NEW;
                END IF;
            END;
            $$ LANGUAGE plpgsql
        """))

    # Cria os triggers fora do bloco acima para usar o schema já confirmado.
    insp = inspect(engine)
    preparer = engine.dialect.identifier_preparer
    for table_name in _syncable_tables(engine):
        # sync_* já foi excluído; telemetria também.
        trigger_name = "coopex_sync_" + hashlib.sha1(table_name.encode()).hexdigest()[:12]
        qt = preparer.quote(table_name)
        qtr = preparer.quote(trigger_name)
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {qtr} ON {qt}")
                conn.exec_driver_sql(
                    f"CREATE TRIGGER {qtr} "
                    f"AFTER INSERT OR UPDATE OR DELETE ON {qt} "
                    f"FOR EACH ROW EXECUTE FUNCTION coopex_sync_log_change()"
                )
        except Exception:
            LOG.exception("Não foi possível criar trigger de sync para %s", table_name)

    # Mantém uma janela ampla sem crescer indefinidamente.
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sync_change_log WHERE changed_at < now() - interval '60 days'"
            ))
            conn.execute(text(
                "DELETE FROM sync_applied_event WHERE applied_at < now() - interval '90 days'"
            ))
    except Exception:
        LOG.exception("Falha na limpeza do histórico de sincronização")


def _admin_required():
    return bool(session.get("is_admin"))


def _server_table_meta(engine, name):
    insp = inspect(engine)
    cols = insp.get_columns(name)
    pk = (insp.get_pk_constraint(name) or {}).get("constrained_columns") or []
    return {
        "name": name,
        "pk": pk,
        "columns": [c["name"] for c in cols],
    }


def _reserve_id_pool(engine, instance_id: str, table_name: str, block_size: int = ID_BLOCK_SIZE):
    """
    Reserva IDs no sequence do PostgreSQL. O site continua usando IDs acima do
    bloco reservado; o desktop pode inserir explicitamente IDs dentro do bloco.
    """
    insp = inspect(engine)
    pk_cols = (insp.get_pk_constraint(table_name) or {}).get("constrained_columns") or []
    if len(pk_cols) != 1:
        return None

    cols = {c["name"]: c for c in insp.get_columns(table_name)}
    pk_name = pk_cols[0]
    pk_info = cols.get(pk_name)
    if not pk_info or not isinstance(pk_info["type"], (Integer, BigInteger)):
        return None

    # Se já existe para esta instalação do SQLite, reutiliza.
    with engine.begin() as conn:
        found = conn.execute(text("""
            SELECT start_id, end_id
            FROM sync_id_pool
            WHERE instance_id=:instance_id AND table_name=:table_name
        """), {"instance_id": instance_id, "table_name": table_name}).first()
        if found:
            return {
                "table": table_name,
                "pk": pk_name,
                "start": int(found[0]),
                "end": int(found[1]),
            }

    preparer = engine.dialect.identifier_preparer
    qt = preparer.quote(table_name)

    # Bloqueio breve evita que um insert normal use um número no bloco reservado.
    with engine.begin() as conn:
        conn.exec_driver_sql(f"LOCK TABLE {qt} IN ACCESS EXCLUSIVE MODE")
        seq = conn.execute(
            text("SELECT pg_get_serial_sequence(:tbl, :pk)"),
            {"tbl": table_name, "pk": pk_name},
        ).scalar()
        if not seq:
            return None

        first = conn.execute(
            text("SELECT nextval(CAST(:seq AS regclass))"),
            {"seq": seq},
        ).scalar()
        first = int(first)
        end = first + int(block_size) - 1

        conn.execute(
            text("SELECT setval(CAST(:seq AS regclass), :last, true)"),
            {"seq": seq, "last": end},
        )
        conn.execute(text("""
            INSERT INTO sync_id_pool(instance_id, table_name, start_id, end_id)
            VALUES (:instance_id, :table_name, :start_id, :end_id)
            ON CONFLICT (instance_id, table_name)
            DO NOTHING
        """), {
            "instance_id": instance_id,
            "table_name": table_name,
            "start_id": first,
            "end_id": end,
        })

    return {"table": table_name, "pk": pk_name, "start": first, "end": end}


def _server_current_row(engine, table: Table, pk_values: dict):
    conds = [table.c[k] == v for k, v in pk_values.items() if k in table.c]
    if len(conds) != len(pk_values) or not conds:
        return None
    with engine.connect() as conn:
        row = conn.execute(select(table).where(and_(*conds))).first()
        return dict(row._mapping) if row else None


def _server_latest_competing_change(engine, table_name, pk_values, base_seq: int, device_id: str):
    pkjson = json.dumps(_json_safe(pk_values), ensure_ascii=False, separators=(",", ":"))
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT seq, changed_at, origin_device, row_json
            FROM sync_change_log
            WHERE table_name=:table_name
              AND seq > :base_seq
              AND row_json @> CAST(:pkjson AS jsonb)
              AND (origin_device IS NULL OR origin_device <> :device_id)
            ORDER BY seq DESC
            LIMIT 1
        """), {
            "table_name": table_name,
            "base_seq": int(base_seq or 0),
            "pkjson": pkjson,
            "device_id": device_id,
        }).first()
        return row


def _apply_server_event(app_module, ev: dict):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    db = app_module.db
    engine = db.engine

    event_id = str(ev.get("event_id") or "")
    device_id = str(ev.get("device_id") or "")
    table_name = str(ev.get("table_name") or "")
    op = str(ev.get("op") or "").upper()[:1]
    base_seq = int(ev.get("base_server_seq") or 0)
    client_changed = _parse_iso_datetime(ev.get("created_at"))
    row_data = ev.get("row") or {}

    if not event_id or not device_id or not table_name or op not in ("I", "U", "D"):
        return {"event_id": event_id, "status": "invalid"}
    if _table_excluded(table_name) or table_name not in _syncable_tables(engine):
        return {"event_id": event_id, "status": "ignored_table"}

    # Idempotência.
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sync_applied_event WHERE event_id=:e"),
            {"e": event_id},
        ).first()
        if exists:
            return {"event_id": event_id, "status": "duplicate"}

    md = MetaData()
    table = Table(table_name, md, autoload_with=engine)
    pk_cols = [c.name for c in table.primary_key.columns]
    if not pk_cols:
        return {"event_id": event_id, "status": "no_pk"}

    filtered = {}
    for k, v in row_data.items():
        if k in table.c:
            filtered[k] = _coerce_server_value(table.c[k], v)

    if not all(k in filtered for k in pk_cols):
        return {"event_id": event_id, "status": "missing_pk"}

    pk_values = {k: filtered[k] for k in pk_cols}

    # Concorrência: se outro supervisor alterou a mesma linha depois da base
    # conhecida pelo desktop, compara horário e preserva o mais novo.
    competing = _server_latest_competing_change(
        engine, table_name, _json_safe(pk_values), base_seq, device_id
    )
    if competing is not None:
        server_changed = competing[1]
        if server_changed is not None and getattr(server_changed, "tzinfo", None) is None:
            # Comparação tolerante quando o driver retorna naive.
            server_changed = server_changed.replace(tzinfo=None)
        if client_changed is not None and getattr(client_changed, "tzinfo", None):
            client_cmp = client_changed.replace(tzinfo=None)
        else:
            client_cmp = client_changed

        server_cmp = server_changed.replace(tzinfo=None) if server_changed else None
        remote_wins = (
            client_cmp is None
            or server_cmp is None
            or server_cmp >= client_cmp
        )
        if remote_wins:
            current = _server_current_row(engine, table, pk_values)
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO sync_conflict_log(
                        event_id, device_id, table_name, resolution, client_row, server_row
                    )
                    VALUES (
                        :event_id, :device_id, :table_name, 'remote_wins',
                        CAST(:client_row AS jsonb), CAST(:server_row AS jsonb)
                    )
                """), {
                    "event_id": event_id,
                    "device_id": device_id,
                    "table_name": table_name,
                    "client_row": json.dumps(_json_safe(row_data), ensure_ascii=False),
                    "server_row": json.dumps(_json_safe(current), ensure_ascii=False),
                })
                conn.execute(text("""
                    INSERT INTO sync_applied_event(event_id, device_id)
                    VALUES (:e, :d)
                    ON CONFLICT(event_id) DO NOTHING
                """), {"e": event_id, "d": device_id})
            return {"event_id": event_id, "status": "conflict_remote_wins"}

    with engine.begin() as conn:
        # A trigger grava a origem para não considerar a própria fila como conflito.
        conn.execute(
            text("SELECT set_config('coopex.sync_origin', :v, true)"),
            {"v": device_id},
        )
        conn.execute(
            text("SELECT set_config('coopex.sync_event', :v, true)"),
            {"v": event_id},
        )

        if op == "D":
            conds = [table.c[k] == pk_values[k] for k in pk_cols]
            conn.execute(delete(table).where(and_(*conds)))
        else:
            stmt = pg_insert(table).values(**filtered)
            update_values = {
                c.name: stmt.excluded[c.name]
                for c in table.columns
                if c.name in filtered and c.name not in pk_cols
            }
            if update_values:
                stmt = stmt.on_conflict_do_update(
                    index_elements=[table.c[k] for k in pk_cols],
                    set_=update_values,
                )
            else:
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=[table.c[k] for k in pk_cols]
                )
            conn.execute(stmt)

        conn.execute(text("""
            INSERT INTO sync_applied_event(event_id, device_id)
            VALUES (:e, :d)
            ON CONFLICT(event_id) DO NOTHING
        """), {"e": event_id, "d": device_id})

    if competing is not None:
        try:
            current = _server_current_row(engine, table, pk_values)
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO sync_conflict_log(
                        event_id, device_id, table_name, resolution, client_row, server_row
                    )
                    VALUES (
                        :event_id, :device_id, :table_name, 'local_wins',
                        CAST(:client_row AS jsonb), CAST(:server_row AS jsonb)
                    )
                """), {
                    "event_id": event_id,
                    "device_id": device_id,
                    "table_name": table_name,
                    "client_row": json.dumps(_json_safe(row_data), ensure_ascii=False),
                    "server_row": json.dumps(_json_safe(current), ensure_ascii=False),
                })
        except Exception:
            LOG.exception("Falha ao registrar conflito local_wins")

    return {"event_id": event_id, "status": "applied"}


def _install_server_routes(app_module):
    app = app_module.app
    db = app_module.db

    @app.get("/api/sync/hello")
    def sync_hello():
        if not _admin_required():
            return jsonify(ok=False, error="unauthorized"), 401
        try:
            with db.engine.connect() as conn:
                latest = conn.execute(text(
                    "SELECT COALESCE(MAX(seq),0) FROM sync_change_log"
                )).scalar()
                minimum = conn.execute(text(
                    "SELECT COALESCE(MIN(seq),0) FROM sync_change_log"
                )).scalar()
            return jsonify(
                ok=True,
                sync_version=SYNC_VERSION,
                latest_seq=int(latest or 0),
                min_seq=int(minimum or 0),
                server_time=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            )
        except Exception as exc:
            LOG.exception("sync_hello")
            return jsonify(ok=False, error=str(exc)), 500

    @app.get("/api/sync/bootstrap/meta")
    def sync_bootstrap_meta():
        if not _admin_required():
            return jsonify(ok=False, error="unauthorized"), 401
        instance_id = (request.args.get("instance_id") or "").strip()
        if not instance_id:
            return jsonify(ok=False, error="instance_id obrigatório"), 400

        tables = _syncable_tables(db.engine)
        meta = [_server_table_meta(db.engine, n) for n in tables]
        pools = []
        for name in tables:
            try:
                pool = _reserve_id_pool(db.engine, instance_id, name)
                if pool:
                    pools.append(pool)
            except Exception:
                LOG.exception("Falha ao reservar IDs para %s", name)

        with db.engine.connect() as conn:
            latest = conn.execute(text(
                "SELECT COALESCE(MAX(seq),0) FROM sync_change_log"
            )).scalar()

        return jsonify(
            ok=True,
            sync_version=SYNC_VERSION,
            snapshot_seq=int(latest or 0),
            tables=meta,
            id_pools=pools,
            page_size=BOOTSTRAP_PAGE,
        )

    @app.get("/api/sync/bootstrap/table/<path:table_name>")
    def sync_bootstrap_table(table_name):
        if not _admin_required():
            return jsonify(ok=False, error="unauthorized"), 401
        if table_name not in _syncable_tables(db.engine):
            return jsonify(ok=False, error="tabela não permitida"), 404

        try:
            offset = max(0, int(request.args.get("offset") or 0))
            limit = min(1500, max(1, int(request.args.get("limit") or BOOTSTRAP_PAGE)))
        except Exception:
            offset, limit = 0, BOOTSTRAP_PAGE

        md = MetaData()
        table = Table(table_name, md, autoload_with=db.engine)
        stmt = select(table)
        if table.primary_key.columns:
            stmt = stmt.order_by(*list(table.primary_key.columns))
        stmt = stmt.offset(offset).limit(limit)

        with db.engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()

        data = [_json_safe(dict(r._mapping)) for r in rows]
        return jsonify(
            ok=True,
            table=table_name,
            offset=offset,
            count=len(data),
            next_offset=(offset + len(data)) if len(data) == limit else None,
            rows=data,
        )

    @app.get("/api/sync/changes")
    def sync_changes():
        if not _admin_required():
            return jsonify(ok=False, error="unauthorized"), 401
        try:
            after = max(0, int(request.args.get("after") or 0))
            limit = min(1000, max(1, int(request.args.get("limit") or PULL_LIMIT)))
        except Exception:
            after, limit = 0, PULL_LIMIT

        with db.engine.connect() as conn:
            minimum = int(conn.execute(text(
                "SELECT COALESCE(MIN(seq),0) FROM sync_change_log"
            )).scalar() or 0)
            latest = int(conn.execute(text(
                "SELECT COALESCE(MAX(seq),0) FROM sync_change_log"
            )).scalar() or 0)
            rows = conn.execute(text("""
                SELECT seq, table_name, op, row_json, origin_device, origin_event, changed_at
                FROM sync_change_log
                WHERE seq > :after
                ORDER BY seq ASC
                LIMIT :limit
            """), {"after": after, "limit": limit}).fetchall()

        events = []
        for r in rows:
            events.append({
                "seq": int(r[0]),
                "table_name": r[1],
                "op": r[2],
                "row": _json_safe(r[3]),
                "origin_device": r[4],
                "origin_event": r[5],
                "changed_at": _json_safe(r[6]),
            })

        reset_required = bool(after and minimum and after < (minimum - 1))
        return jsonify(
            ok=True,
            min_seq=minimum,
            latest_seq=latest,
            reset_required=reset_required,
            events=events,
        )

    @app.post("/api/sync/push")
    def sync_push():
        if not _admin_required():
            return jsonify(ok=False, error="unauthorized"), 401
        payload = request.get_json(silent=True) or {}
        events = payload.get("events") or []
        if not isinstance(events, list):
            return jsonify(ok=False, error="events inválido"), 400
        events = events[:300]

        results = []
        for ev in events:
            try:
                results.append(_apply_server_event(app_module, ev or {}))
            except Exception as exc:
                LOG.exception("Falha aplicando evento %s", (ev or {}).get("event_id"))
                results.append({
                    "event_id": (ev or {}).get("event_id"),
                    "status": "error",
                    "error": str(exc),
                })

        return jsonify(ok=True, results=results)

    # Comprovantes/fotos: sincronização separada do banco.
    @app.get("/api/sync/comprovantes/manifest")
    def sync_comprovantes_manifest():
        if not _admin_required():
            return jsonify(ok=False, error="unauthorized"), 401
        loader = getattr(app_module, "_load_comprovante_index", None)
        if not loader:
            return jsonify(ok=True, items={})
        try:
            return jsonify(ok=True, items=_json_safe(loader() or {}))
        except Exception:
            LOG.exception("Manifesto de comprovantes")
            return jsonify(ok=False, error="manifesto indisponível"), 500

    @app.route("/api/sync/comprovantes/<int:entrega_id>", methods=["GET", "POST"])
    def sync_comprovante_file(entrega_id):
        if not _admin_required():
            return jsonify(ok=False, error="unauthorized"), 401

        if request.method == "POST":
            saver = getattr(app_module, "_salvar_comprovante", None)
            if not saver or "file" not in request.files:
                return jsonify(ok=False, error="arquivo ausente"), 400
            try:
                fn = saver(entrega_id, request.files["file"])
                return jsonify(ok=True, filename=fn)
            except Exception as exc:
                LOG.exception("Upload de comprovante")
                return jsonify(ok=False, error=str(exc)), 500

        loader = getattr(app_module, "_load_comprovante_index", None)
        directory = getattr(app_module, "COMPROVANTE_DIR", None)
        if not loader or not directory:
            return jsonify(ok=False, error="indisponível"), 404
        info = (loader() or {}).get(str(entrega_id))
        if not info or not info.get("filename"):
            return jsonify(ok=False, error="não encontrado"), 404
        path = os.path.join(directory, info["filename"])
        if not os.path.exists(path):
            return jsonify(ok=False, error="não encontrado"), 404
        return send_file(path, as_attachment=False)


# ---------------------------------------------------------------------------
# DESKTOP / SQLITE
# ---------------------------------------------------------------------------

def _local_db_path(app_module) -> Path:
    uri = str(app_module.app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    if not uri.startswith("sqlite:///"):
        raise RuntimeError("O modo desktop precisa usar SQLite local.")
    raw = uri[len("sqlite:///"):]
    return Path(raw).resolve()


def _sqlite_conn(db_path: Path):
    conn = sqlite3.connect(
        str(db_path),
        timeout=10.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _configure_sqlite_accelerator(app_module):
    """
    Acelerador local:
    WAL + cache de páginas em RAM + mmap + temp_store em RAM.
    """
    engine = app_module.db.engine
    if engine.dialect.name != "sqlite":
        return

    cache_mb = int(os.environ.get("SUPERVISAO_SQLITE_CACHE_MB") or 128)
    mmap_mb = int(os.environ.get("SUPERVISAO_SQLITE_MMAP_MB") or 256)
    cache_kib = max(32768, cache_mb * 1024)
    mmap_bytes = max(64, mmap_mb) * 1024 * 1024

    def apply_pragmas(dbapi_conn):
        cur = dbapi_conn.cursor()
        for sql in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA temp_store=MEMORY",
            f"PRAGMA cache_size=-{cache_kib}",
            f"PRAGMA mmap_size={mmap_bytes}",
            "PRAGMA busy_timeout=10000",
            "PRAGMA foreign_keys=ON",
            "PRAGMA wal_autocheckpoint=1000",
            "PRAGMA journal_size_limit=67108864",
        ):
            try:
                cur.execute(sql)
            except Exception:
                pass
        cur.close()

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, connection_record):
        apply_pragmas(dbapi_connection)

    try:
        raw = engine.raw_connection()
        try:
            apply_pragmas(raw)
        finally:
            raw.close()
    except Exception:
        LOG.exception("Falha aplicando acelerador SQLite")


def _ensure_local_schema(app_module):
    db_path = _local_db_path(app_module)
    with _sqlite_conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sync_state (
                id INTEGER PRIMARY KEY CHECK(id=1),
                device_id TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                bootstrap_done INTEGER NOT NULL DEFAULT 0,
                last_server_seq INTEGER NOT NULL DEFAULT 0,
                last_sync_at TEXT NULL,
                last_error TEXT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_runtime (
                id INTEGER PRIMARY KEY CHECK(id=1),
                suppress INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sync_outbox (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                table_name TEXT NOT NULL,
                op TEXT NOT NULL,
                row_json TEXT NOT NULL,
                base_server_seq INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_sync_outbox_seq ON sync_outbox(seq);

            CREATE TABLE IF NOT EXISTS sync_id_pool_local (
                table_name TEXT PRIMARY KEY,
                pk_name TEXT NOT NULL,
                next_id INTEGER NOT NULL,
                end_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_conflict_local (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                table_name TEXT,
                resolution TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute(
            "INSERT OR IGNORE INTO sync_runtime(id,suppress) VALUES(1,0)"
        )
        row = conn.execute("SELECT 1 FROM sync_state WHERE id=1").fetchone()
        if not row:
            device_id = str(uuid.uuid4())
            instance_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO sync_state(
                    id, device_id, instance_id, bootstrap_done, last_server_seq
                ) VALUES(1,?,?,0,0)
            """, (device_id, instance_id))
    return db_path


def _local_state(db_path: Path):
    with _sqlite_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM sync_state WHERE id=1").fetchone()
        return dict(row) if row else {}


def _set_local_state(db_path: Path, **values):
    if not values:
        return
    allowed = {
        "bootstrap_done", "last_server_seq", "last_sync_at", "last_error"
    }
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return
    parts = ", ".join(f"{k}=?" for k in values)
    with _sqlite_conn(db_path) as conn:
        conn.execute(
            f"UPDATE sync_state SET {parts} WHERE id=1",
            tuple(values.values()),
        )


def _set_suppress(db_path: Path, value: bool):
    with _sqlite_conn(db_path) as conn:
        conn.execute(
            "UPDATE sync_runtime SET suppress=? WHERE id=1",
            (1 if value else 0,),
        )


def _sqlite_table_info(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({_q_sqlite(table_name)})").fetchall()
    return [
        {
            "name": r["name"],
            "type": r["type"] or "",
            "pk": int(r["pk"] or 0),
        }
        for r in rows
    ]


def _row_json_expr(prefix: str, columns):
    args = []
    for col in columns:
        q = _q_sqlite(col)
        args.append("'" + col.replace("'", "''") + "'")
        args.append(
            f"CASE WHEN typeof({prefix}.{q})='blob' "
            f"THEN '__hex__:'||hex({prefix}.{q}) ELSE {prefix}.{q} END"
        )
    return "json_object(" + ",".join(args) + ")"


def _install_local_triggers(app_module, db_path: Path):
    engine = app_module.db.engine
    if engine.dialect.name != "sqlite":
        return

    tables = _syncable_tables(engine)
    with _sqlite_conn(db_path) as conn:
        for table_name in tables:
            info = _sqlite_table_info(conn, table_name)
            cols = [x["name"] for x in info]
            if not cols:
                continue

            qt = _q_sqlite(table_name)
            expr_new = _row_json_expr("NEW", cols)
            expr_old = _row_json_expr("OLD", cols)

            for suffix in ("ai", "au", "ad"):
                conn.execute(
                    f"DROP TRIGGER IF EXISTS {_q_sqlite(_safe_trigger_name(table_name, suffix))}"
                )

            cond = (
                "(SELECT suppress FROM sync_runtime WHERE id=1)=0 "
                "AND (SELECT bootstrap_done FROM sync_state WHERE id=1)=1"
            )
            table_lit = table_name.replace("'", "''")

            conn.executescript(f"""
                CREATE TRIGGER {_q_sqlite(_safe_trigger_name(table_name, 'ai'))}
                AFTER INSERT ON {qt}
                WHEN {cond}
                BEGIN
                    INSERT INTO sync_outbox(
                        event_id, table_name, op, row_json,
                        base_server_seq, created_at
                    )
                    VALUES(
                        lower(hex(randomblob(16))),
                        '{table_lit}',
                        'I',
                        {expr_new},
                        COALESCE((SELECT last_server_seq FROM sync_state WHERE id=1),0),
                        strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    );
                END;

                CREATE TRIGGER {_q_sqlite(_safe_trigger_name(table_name, 'au'))}
                AFTER UPDATE ON {qt}
                WHEN {cond}
                BEGIN
                    INSERT INTO sync_outbox(
                        event_id, table_name, op, row_json,
                        base_server_seq, created_at
                    )
                    VALUES(
                        lower(hex(randomblob(16))),
                        '{table_lit}',
                        'U',
                        {expr_new},
                        COALESCE((SELECT last_server_seq FROM sync_state WHERE id=1),0),
                        strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    );
                END;

                CREATE TRIGGER {_q_sqlite(_safe_trigger_name(table_name, 'ad'))}
                AFTER DELETE ON {qt}
                WHEN {cond}
                BEGIN
                    INSERT INTO sync_outbox(
                        event_id, table_name, op, row_json,
                        base_server_seq, created_at
                    )
                    VALUES(
                        lower(hex(randomblob(16))),
                        '{table_lit}',
                        'D',
                        {expr_old},
                        COALESCE((SELECT last_server_seq FROM sync_state WHERE id=1),0),
                        strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    );
                END;
            """)


def _allocate_from_pool(connection, table_name):
    row = connection.execute(text("""
        SELECT next_id, end_id
        FROM sync_id_pool_local
        WHERE table_name=:t
    """), {"t": table_name}).first()
    if not row:
        return None
    nxt, end = int(row[0]), int(row[1])
    if nxt > end:
        raise RuntimeError(
            f"Reserva de IDs esgotada para {table_name}. "
            f"Conecte o app à internet para renovar a sincronização."
        )
    connection.execute(text("""
        UPDATE sync_id_pool_local
        SET next_id=:new_next
        WHERE table_name=:t
    """), {"new_next": nxt + 1, "t": table_name})
    return nxt


def _install_local_id_allocator(app_module):
    if not _desktop():
        return
    db = app_module.db

    registry = getattr(db.Model, "registry", None)
    if not registry:
        return

    for mapper in list(registry.mappers):
        cls = mapper.class_
        table = mapper.local_table
        pk_cols = list(table.primary_key.columns)
        if len(pk_cols) != 1:
            continue
        pk_col = pk_cols[0]
        if not isinstance(pk_col.type, (Integer, BigInteger)):
            continue

        flag = "_supervisao_sync_id_allocator"
        if getattr(cls, flag, False):
            continue
        setattr(cls, flag, True)

        def make_listener(table_name, pk_key):
            def before_insert(mapper_, connection, target):
                try:
                    current = getattr(target, pk_key, None)
                    if current is not None:
                        return
                    new_id = _allocate_from_pool(connection, table_name)
                    if new_id is not None:
                        setattr(target, pk_key, new_id)
                except Exception:
                    LOG.exception("Falha alocando ID offline para %s", table_name)
                    raise
            return before_insert

        event.listen(
            cls,
            "before_insert",
            make_listener(table.name, pk_col.key),
            propagate=False,
        )


def _local_pending(db_path: Path):
    with _sqlite_conn(db_path) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM sync_outbox"
        ).fetchone()[0] or 0)


def _bump_live_version(db_path: Path):
    try:
        with _sqlite_conn(db_path) as conn:
            table = conn.execute("""
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='supervisao_live_version'
            """).fetchone()
            if table:
                conn.execute("""
                    UPDATE supervisao_live_version
                    SET versao=versao+1
                    WHERE id=1
                """)
    except Exception:
        pass


def _insert_rows_local(db_path: Path, table_name: str, rows):
    if not rows:
        return
    with _sqlite_conn(db_path) as conn:
        # Durante a cópia inicial as tabelas podem chegar em ordem diferente
        # da ordem das chaves estrangeiras. A integridade é reativada ao final.
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
        except Exception:
            pass

        info = _sqlite_table_info(conn, table_name)
        local_cols = {x["name"]: x["type"] for x in info}
        if not local_cols:
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                data = {
                    k: _sqlite_value(v, local_cols[k])
                    for k, v in row.items()
                    if k in local_cols
                }
                if not data:
                    continue
                cols = list(data.keys())
                sql = (
                    f"INSERT OR REPLACE INTO {_q_sqlite(table_name)} "
                    f"({','.join(_q_sqlite(c) for c in cols)}) "
                    f"VALUES ({','.join('?' for _ in cols)})"
                )
                conn.execute(sql, tuple(data[c] for c in cols))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            try:
                conn.execute("PRAGMA foreign_keys=ON")
            except Exception:
                pass


def _apply_local_change(db_path: Path, meta_map: dict, ev: dict):
    table_name = ev.get("table_name")
    if table_name not in meta_map:
        return
    row = ev.get("row") or {}
    pk_cols = meta_map[table_name].get("pk") or []
    if not pk_cols or not all(k in row for k in pk_cols):
        return

    with _sqlite_conn(db_path) as conn:
        info = _sqlite_table_info(conn, table_name)
        local_cols = {x["name"]: x["type"] for x in info}
        if not local_cols:
            return

        if ev.get("op") == "D":
            wh = " AND ".join(f"{_q_sqlite(k)}=?" for k in pk_cols if k in local_cols)
            vals = [
                _sqlite_value(row[k], local_cols[k])
                for k in pk_cols if k in local_cols
            ]
            if len(vals) == len(pk_cols):
                conn.execute(
                    f"DELETE FROM {_q_sqlite(table_name)} WHERE {wh}",
                    vals,
                )
            return

        data = {
            k: _sqlite_value(v, local_cols[k])
            for k, v in row.items()
            if k in local_cols
        }
        if not data:
            return
        cols = list(data.keys())
        sql = (
            f"INSERT OR REPLACE INTO {_q_sqlite(table_name)} "
            f"({','.join(_q_sqlite(c) for c in cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})"
        )
        conn.execute(sql, tuple(data[c] for c in cols))


def _apply_local_changes_batch(db_path: Path, meta_map: dict, events):
    """Aplica até centenas de mudanças usando uma única transação SQLite."""
    if not events:
        return
    with _sqlite_conn(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        info_cache = {}

        def table_info(name):
            if name not in info_cache:
                info = _sqlite_table_info(conn, name)
                info_cache[name] = {x["name"]: x["type"] for x in info}
            return info_cache[name]

        conn.execute("BEGIN IMMEDIATE")
        try:
            for ev in events:
                table_name = ev.get("table_name")
                if table_name not in meta_map:
                    continue
                row = ev.get("row") or {}
                pk_cols = meta_map[table_name].get("pk") or []
                if not pk_cols or not all(k in row for k in pk_cols):
                    continue

                local_cols = table_info(table_name)
                if not local_cols:
                    continue

                if ev.get("op") == "D":
                    if not all(k in local_cols for k in pk_cols):
                        continue
                    wh = " AND ".join(
                        f"{_q_sqlite(k)}=?" for k in pk_cols
                    )
                    vals = [
                        _sqlite_value(row[k], local_cols[k])
                        for k in pk_cols
                    ]
                    conn.execute(
                        f"DELETE FROM {_q_sqlite(table_name)} WHERE {wh}",
                        vals,
                    )
                    continue

                data = {
                    k: _sqlite_value(v, local_cols[k])
                    for k, v in row.items()
                    if k in local_cols
                }
                if not data:
                    continue
                cols = list(data.keys())
                sql = (
                    f"INSERT OR REPLACE INTO {_q_sqlite(table_name)} "
                    f"({','.join(_q_sqlite(c) for c in cols)}) "
                    f"VALUES ({','.join('?' for _ in cols)})"
                )
                conn.execute(sql, tuple(data[c] for c in cols))

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            try:
                conn.execute("PRAGMA foreign_keys=ON")
            except Exception:
                pass


class DesktopSyncWorker:
    def __init__(self, app_module, db_path: Path):
        self.app_module = app_module
        self.db_path = db_path
        self.base_url = (
            os.environ.get("SUPERVISAO_REMOTE_URL") or REMOTE_DEFAULT
        ).rstrip("/")
        self.credentials = None
        self.http = None
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.lock = threading.RLock()
        self.status = {
            "state": "waiting_login",
            "remote": self.base_url,
            "last_ok": None,
            "last_error": None,
        }
        self.meta_map = {}
        self.last_file_sync = 0.0

    def set_credentials(self, usuario: str, senha: str):
        with self.lock:
            self.credentials = (usuario, senha)
            self.http = None
            self.status["state"] = "connecting"
        self.wake_event.set()

    def _requests(self):
        import requests
        return requests

    def _ensure_http(self):
        requests = self._requests()
        with self.lock:
            creds = self.credentials
            current = self.http
        if not creds:
            return None

        if current is not None:
            try:
                r = current.get(
                    self.base_url + "/api/sync/hello",
                    timeout=(2.5, 7),
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    return current
            except Exception:
                pass

        s = requests.Session()
        s.headers.update({
            "User-Agent": "Supervisao-Desktop-Sync/1",
            "Accept": "application/json",
        })
        usuario, senha = creds
        try:
            r = s.post(
                self.base_url + "/login",
                data={"usuario": usuario, "senha": senha, "next": "/admin"},
                allow_redirects=False,
                timeout=(3.5, 30),
            )
            # A prova real é o endpoint protegido.
            h = s.get(
                self.base_url + "/api/sync/hello",
                timeout=(3.5, 15),
            )
            if h.status_code != 200:
                raise RuntimeError(
                    f"Servidor de sincronização respondeu HTTP {h.status_code}"
                )
            with self.lock:
                self.http = s
                self.status["state"] = "online"
                self.status["last_error"] = None
            return s
        except Exception as exc:
            with self.lock:
                self.http = None
                self.status["state"] = "offline"
                self.status["last_error"] = str(exc)
            _set_local_state(self.db_path, last_error=str(exc)[:1000])
            return None

    def _fetch_json(self, method, path, **kwargs):
        s = self._ensure_http()
        if s is None:
            return None
        try:
            fn = getattr(s, method.lower())
            r = fn(
                self.base_url + path,
                timeout=kwargs.pop("timeout", (3.5, 30)),
                **kwargs,
            )
            if r.status_code == 401:
                with self.lock:
                    self.http = None
                s = self._ensure_http()
                if s is None:
                    return None
                fn = getattr(s, method.lower())
                r = fn(
                    self.base_url + path,
                    timeout=kwargs.pop("timeout_retry", (3.5, 30)),
                    **kwargs,
                )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            with self.lock:
                self.status["state"] = "offline"
                self.status["last_error"] = str(exc)
            _set_local_state(self.db_path, last_error=str(exc)[:1000])
            return None

    def _bootstrap(self):
        state = _local_state(self.db_path)
        if int(state.get("bootstrap_done") or 0) == 1:
            return True

        instance_id = state.get("instance_id")
        meta = self._fetch_json(
            "get",
            "/api/sync/bootstrap/meta",
            params={"instance_id": instance_id},
            timeout=(3.5, 45),
        )
        if not meta or not meta.get("ok"):
            return False

        tables = meta.get("tables") or []
        self.meta_map = {x["name"]: x for x in tables if x.get("name")}

        # Importação é suprimida para não gerar outbox.
        _set_suppress(self.db_path, True)
        try:
            self.app_module.db.session.remove()
        except Exception:
            pass

        try:
            with _sqlite_conn(self.db_path) as conn:
                conn.execute("PRAGMA foreign_keys=OFF")
                for t in tables:
                    name = t["name"]
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,),
                    ).fetchone()
                    if exists:
                        conn.execute(f"DELETE FROM {_q_sqlite(name)}")
                conn.execute("DELETE FROM sync_id_pool_local")

                for pool in meta.get("id_pools") or []:
                    conn.execute("""
                        INSERT OR REPLACE INTO sync_id_pool_local(
                            table_name, pk_name, next_id, end_id
                        ) VALUES(?,?,?,?)
                    """, (
                        pool["table"],
                        pool["pk"],
                        int(pool["start"]),
                        int(pool["end"]),
                    ))

            for t in tables:
                name = t["name"]
                offset = 0
                while True:
                    page = self._fetch_json(
                        "get",
                        f"/api/sync/bootstrap/table/{name}",
                        params={"offset": offset, "limit": BOOTSTRAP_PAGE},
                        timeout=(3.5, 60),
                    )
                    if not page or not page.get("ok"):
                        raise RuntimeError(f"Falha baixando tabela {name}")
                    _insert_rows_local(self.db_path, name, page.get("rows") or [])
                    nxt = page.get("next_offset")
                    if nxt is None:
                        break
                    offset = int(nxt)

            _set_local_state(
                self.db_path,
                bootstrap_done=1,
                last_server_seq=int(meta.get("snapshot_seq") or 0),
                last_sync_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
                last_error=None,
            )
            _install_local_triggers(self.app_module, self.db_path)
            self._pull_all()
            self._sync_files(force=True)
            self._prewarm()
            _bump_live_version(self.db_path)
            with self.lock:
                self.status["state"] = "synced"
                self.status["last_ok"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            LOG.info("Bootstrap concluído")
            return True

        except Exception as exc:
            LOG.exception("Bootstrap falhou")
            _set_local_state(self.db_path, bootstrap_done=0, last_error=str(exc)[:1000])
            with self.lock:
                self.status["state"] = "offline"
                self.status["last_error"] = str(exc)
            return False
        finally:
            _set_suppress(self.db_path, False)

    def _outbox_batch(self, limit=150):
        state = _local_state(self.db_path)
        device_id = state.get("device_id")
        with _sqlite_conn(self.db_path) as conn:
            rows = conn.execute("""
                SELECT seq,event_id,table_name,op,row_json,
                       base_server_seq,created_at
                FROM sync_outbox
                ORDER BY seq ASC
                LIMIT ?
            """, (limit,)).fetchall()
        events = []
        for r in rows:
            try:
                row = json.loads(r["row_json"])
            except Exception:
                row = {}
            events.append({
                "_local_seq": int(r["seq"]),
                "event_id": r["event_id"],
                "device_id": device_id,
                "table_name": r["table_name"],
                "op": r["op"],
                "row": row,
                "base_server_seq": int(r["base_server_seq"] or 0),
                "created_at": r["created_at"],
            })
        return events

    def _push(self):
        events = self._outbox_batch()
        if not events:
            return True

        payload_events = [
            {k: v for k, v in ev.items() if k != "_local_seq"}
            for ev in events
        ]
        data = self._fetch_json(
            "post",
            "/api/sync/push",
            json={"events": payload_events},
            timeout=(3.5, 45),
        )
        if not data or not data.get("ok"):
            return False

        result_by_id = {
            str(x.get("event_id")): x
            for x in (data.get("results") or [])
        }
        accepted = {
            "applied",
            "duplicate",
            "conflict_remote_wins",
            "ignored_table",
        }

        with _sqlite_conn(self.db_path) as conn:
            for ev in events:
                result = result_by_id.get(str(ev["event_id"])) or {}
                st = result.get("status")
                if st in accepted:
                    conn.execute(
                        "DELETE FROM sync_outbox WHERE seq=?",
                        (ev["_local_seq"],),
                    )
                    if st == "conflict_remote_wins":
                        conn.execute("""
                            INSERT INTO sync_conflict_local(
                                event_id,table_name,resolution
                            ) VALUES(?,?,?)
                        """, (
                            ev["event_id"],
                            ev["table_name"],
                            "remote_wins",
                        ))
                else:
                    conn.execute("""
                        UPDATE sync_outbox
                        SET attempts=attempts+1,last_error=?
                        WHERE seq=?
                    """, (
                        str(result.get("error") or st or "erro")[:500],
                        ev["_local_seq"],
                    ))
        return True

    def _ensure_meta_map(self):
        if self.meta_map:
            return True
        state = _local_state(self.db_path)
        instance_id = state.get("instance_id")
        meta = self._fetch_json(
            "get",
            "/api/sync/bootstrap/meta",
            params={"instance_id": instance_id},
            timeout=(3.5, 30),
        )
        if not meta or not meta.get("ok"):
            return False
        self.meta_map = {
            x["name"]: x for x in (meta.get("tables") or []) if x.get("name")
        }
        # Não substitui pools existentes; apenas adiciona os que faltarem.
        with _sqlite_conn(self.db_path) as conn:
            for pool in meta.get("id_pools") or []:
                exists = conn.execute(
                    "SELECT 1 FROM sync_id_pool_local WHERE table_name=?",
                    (pool["table"],),
                ).fetchone()
                if not exists:
                    conn.execute("""
                        INSERT INTO sync_id_pool_local(
                            table_name,pk_name,next_id,end_id
                        ) VALUES(?,?,?,?)
                    """, (
                        pool["table"], pool["pk"],
                        int(pool["start"]), int(pool["end"]),
                    ))
        return True

    def _pull_once(self):
        state = _local_state(self.db_path)
        after = int(state.get("last_server_seq") or 0)
        data = self._fetch_json(
            "get",
            "/api/sync/changes",
            params={"after": after, "limit": PULL_LIMIT},
            timeout=(2.5, 20),
        )
        if not data or not data.get("ok"):
            return False

        if data.get("reset_required"):
            # Antes de rebootstrap, somente se toda a fila já foi enviada.
            if _local_pending(self.db_path) == 0:
                _set_local_state(
                    self.db_path,
                    bootstrap_done=0,
                    last_server_seq=0,
                )
                self.meta_map = {}
                return self._bootstrap()
            return False

        events = data.get("events") or []
        if not events:
            _set_local_state(
                self.db_path,
                last_sync_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
                last_error=None,
            )
            return True

        if not self._ensure_meta_map():
            return False

        _set_suppress(self.db_path, True)
        try:
            last = after
            _apply_local_changes_batch(self.db_path, self.meta_map, events)
            for ev in events:
                last = max(last, int(ev.get("seq") or 0))
            _set_local_state(
                self.db_path,
                last_server_seq=last,
                last_sync_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
                last_error=None,
            )
            _bump_live_version(self.db_path)
        finally:
            _set_suppress(self.db_path, False)
        return True

    def _pull_all(self, max_loops=200):
        for _ in range(max_loops):
            before = int(_local_state(self.db_path).get("last_server_seq") or 0)
            if not self._pull_once():
                return False
            after = int(_local_state(self.db_path).get("last_server_seq") or 0)
            if after == before:
                return True
        return True

    def _local_comprovante_index(self):
        loader = getattr(self.app_module, "_load_comprovante_index", None)
        if not loader:
            return {}
        try:
            return loader() or {}
        except Exception:
            return {}

    def _sync_files(self, force=False):
        now = time.time()
        if not force and (now - self.last_file_sync) < 30:
            return
        self.last_file_sync = now

        s = self._ensure_http()
        if s is None:
            return
        try:
            r = s.get(
                self.base_url + "/api/sync/comprovantes/manifest",
                timeout=(2.5, 15),
            )
            if r.status_code != 200:
                return
            remote = (r.json() or {}).get("items") or {}
            local = self._local_comprovante_index()
            directory = Path(getattr(self.app_module, "COMPROVANTE_DIR", ""))
            index_path = Path(getattr(self.app_module, "COMPROVANTE_INDEX", ""))
            if not str(directory):
                return
            directory.mkdir(parents=True, exist_ok=True)

            all_ids = set(remote) | set(local)
            changed_local_index = False

            def stamp(info):
                if not info:
                    return ""
                return str(info.get("uploaded_at") or "")

            for entrega_id in all_ids:
                ri = remote.get(entrega_id)
                li = local.get(entrega_id)
                rs, ls = stamp(ri), stamp(li)

                if li and (not ri or ls > rs):
                    fn = li.get("filename")
                    fp = directory / str(fn or "")
                    if fp.exists():
                        with fp.open("rb") as f:
                            up = s.post(
                                self.base_url + f"/api/sync/comprovantes/{int(entrega_id)}",
                                files={"file": (fp.name, f)},
                                timeout=(3.5, 30),
                            )
                            if up.status_code == 200:
                                continue

                if ri and (not li or rs > ls):
                    down = s.get(
                        self.base_url + f"/api/sync/comprovantes/{int(entrega_id)}",
                        timeout=(3.5, 30),
                    )
                    if down.status_code == 200:
                        fn = ri.get("filename") or f"entrega_{entrega_id}_sync.jpg"
                        fp = directory / fn
                        fp.write_bytes(down.content)
                        local[str(entrega_id)] = ri
                        changed_local_index = True

            if changed_local_index and index_path:
                tmp = Path(str(index_path) + ".tmp")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(
                    json.dumps(local, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                os.replace(tmp, index_path)

        except Exception:
            LOG.exception("Falha sincronizando comprovantes")

    def _prewarm(self):
        """Lê páginas mais usadas para aproveitar o cache em RAM do SQLite."""
        try:
            with _sqlite_conn(self.db_path) as conn:
                for table_name, order in (
                    ("entrega", "data_envio DESC"),
                    ("cooperado", "nome ASC"),
                    ("cliente", "nome ASC"),
                ):
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table_name,),
                    ).fetchone()
                    if not exists:
                        continue
                    try:
                        conn.execute(
                            f"SELECT * FROM {_q_sqlite(table_name)} "
                            f"ORDER BY {order} LIMIT 800"
                        ).fetchall()
                    except Exception:
                        conn.execute(
                            f"SELECT COUNT(*) FROM {_q_sqlite(table_name)}"
                        ).fetchone()
        except Exception:
            pass

    def sync_cycle(self):
        state = _local_state(self.db_path)
        if not self.credentials:
            with self.lock:
                self.status["state"] = (
                    "local_ready"
                    if int(state.get("bootstrap_done") or 0)
                    else "waiting_login"
                )
            return False

        if self._ensure_http() is None:
            return False

        if int(state.get("bootstrap_done") or 0) != 1:
            return self._bootstrap()

        # Envia primeiro: protege alterações feitas enquanto o Render caiu.
        self._push()
        self._pull_all(max_loops=10)
        self._sync_files()

        with self.lock:
            self.status["state"] = "synced"
            self.status["last_ok"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            self.status["last_error"] = None
        return True

    def bootstrap_blocking(self):
        """Usado no primeiro login para a tela /admin já abrir com os dados."""
        try:
            return self.sync_cycle()
        except Exception as exc:
            LOG.exception("bootstrap_blocking")
            with self.lock:
                self.status["last_error"] = str(exc)
            return False

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.sync_cycle()
            except Exception as exc:
                LOG.exception("Ciclo de sincronização")
                with self.lock:
                    self.status["state"] = "offline"
                    self.status["last_error"] = str(exc)
            self.wake_event.wait(PULL_INTERVAL)
            self.wake_event.clear()


def _patch_comprovante_storage(app_module):
    """
    No EXE, comprovantes não podem ficar dentro da pasta do build.
    Mantém tudo ao lado do SQLite, em LOCALAPPDATA.
    """
    try:
        db_path = _local_db_path(app_module)
        data_dir = db_path.parent
        comp_dir = data_dir / "comprovantes"
        comp_dir.mkdir(parents=True, exist_ok=True)
        app_module.COMPROVANTE_DIR = str(comp_dir)
        app_module.COMPROVANTE_INDEX = str(data_dir / "comprovantes_index.json")
    except Exception:
        LOG.exception("Falha configurando comprovantes persistentes")


def _install_desktop_hooks(app_module, worker: DesktopSyncWorker):
    app = app_module.app
    db_path = worker.db_path

    @app.after_request
    def _sync_capture_login(response):
        """
        Usa a mesma credencial digitada no app para autenticar o agente no Render.
        A senha fica apenas em memória durante esta execução.
        """
        try:
            if (
                request.path == "/login"
                and request.method == "POST"
                and session.get("is_admin")
            ):
                usuario = (request.form.get("usuario") or "").strip()
                senha = request.form.get("senha") or ""
                if usuario and senha:
                    # Login bem-sucedido deve entrar IMEDIATAMENTE no app.
                    # A cópia inicial/sincronização roda em segundo plano e nunca
                    # bloqueia o redirect para /admin.
                    worker.set_credentials(usuario, senha)
                    worker.wake_event.set()
        except Exception:
            LOG.exception("Falha capturando login para sync")
        return response

    @app.get("/api/sync/status")
    def _sync_status_local():
        if not session.get("is_admin"):
            return jsonify(ok=False, error="unauthorized"), 401
        state = _local_state(db_path)
        with worker.lock:
            st = dict(worker.status)
        return jsonify(
            ok=True,
            state=st.get("state"),
            remote=st.get("remote"),
            last_ok=st.get("last_ok"),
            last_error=st.get("last_error") or state.get("last_error"),
            bootstrap_done=bool(state.get("bootstrap_done")),
            pending=_local_pending(db_path),
            last_server_seq=int(state.get("last_server_seq") or 0),
        )


def install(app_module):
    app = app_module.app
    if app.extensions.get("supervisao_sync_installed"):
        return
    app.extensions["supervisao_sync_installed"] = True

    if _desktop():
        # Flask-SQLAlchemy 3 exige app_context para acessar db.engine.
        with app.app_context():
            db_path = _ensure_local_schema(app_module)
            _configure_sqlite_accelerator(app_module)
            _patch_comprovante_storage(app_module)
            _install_local_id_allocator(app_module)
            _install_local_triggers(app_module, db_path)

        worker = DesktopSyncWorker(app_module, db_path)
        app.extensions["supervisao_sync_worker"] = worker
        _install_desktop_hooks(app_module, worker)

        th = threading.Thread(
            target=worker.run,
            name="SupervisaoSyncWorker",
            daemon=True,
        )
        th.start()
        app.extensions["supervisao_sync_thread"] = th

        LOG.info("Sincronização desktop instalada em %s", db_path)
        return

    # Render/PostgreSQL.
    with app.app_context():
        is_postgres = app_module.db.engine.dialect.name == "postgresql"
        if is_postgres:
            _ensure_server_schema(app_module)

    if is_postgres:
        _install_server_routes(app_module)
        LOG.info("Sincronização servidor instalada.")
