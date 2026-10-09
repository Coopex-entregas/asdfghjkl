"""Migração aditiva COOPEX CONNECT: cria apenas a tabela de rascunhos.

Execução manual, após backup e validação em ambiente de testes:
    python scripts/create_coopex_connect_table.py

Não executa db.create_all e não apaga/altera tabelas existentes.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app, db
from sqlalchemy import inspect

TABLE_NAME = "connect_delivery_draft"

def main():
    with app.app_context():
        table = db.metadata.tables.get(TABLE_NAME)
        if table is None:
            raise RuntimeError("COOPEX CONNECT não registrado em app.py")
        if inspect(db.engine).has_table(TABLE_NAME):
            print("OK: tabela já existe; nada alterado.")
            return
        table.create(bind=db.engine, checkfirst=True)
        if not inspect(db.engine).has_table(TABLE_NAME):
            raise RuntimeError("Falha ao verificar tabela criada.")
        print("OK: tabela criada sem alterar tabelas existentes.")

if __name__ == "__main__":
    main()
