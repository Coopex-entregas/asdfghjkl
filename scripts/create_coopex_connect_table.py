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

TABLE_NAMES = ("connect_delivery_draft", "connect_whatsapp_message")

def main():
    with app.app_context():
        for table_name in TABLE_NAMES:
            table = db.metadata.tables.get(table_name)
            if table is None:
                raise RuntimeError(f"Modelo ausente: {table_name}")
            if inspect(db.engine).has_table(table_name):
                print(f"OK: {table_name} já existe.")
                continue
            table.create(bind=db.engine, checkfirst=True)
            if not inspect(db.engine).has_table(table_name):
                raise RuntimeError(f"Falha na criação de {table_name}")
            print(f"OK: {table_name} criada, sem alterar tabelas existentes.")

if __name__ == "__main__":
    main()
