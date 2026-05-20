"""Configuração leve do Gunicorn para o painel COOPEX.

O Render normalmente inicia o Flask via Gunicorn. Este arquivo é lido antes do
app.py ser carregado e registra helpers globais usados pelo dashboard.

Correção aplicada:
- evita NameError: _bairro_rota_display is not defined
- evita NameError: _bairro_rota_key is not defined
"""

import builtins
import re
import unicodedata


def _coopex_strip_accents(texto):
    texto = str(texto or "")
    return "".join(
        ch for ch in unicodedata.normalize("NFD", texto)
        if unicodedata.category(ch) != "Mn"
    )


def _bairro_rota_display(valor):
    txt = str(valor or "").strip()
    if not txt:
        return ""
    txt = re.sub(r"\s+", " ", txt)
    if txt.isupper() or txt.islower():
        return txt[:1].upper() + txt[1:].lower()
    return txt


def _bairro_rota_key(valor):
    txt = _coopex_strip_accents(valor or "").lower()
    txt = re.sub(r"[^a-z0-9\s]", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


# Deixa os nomes disponíveis para qualquer módulo carregado pelo Gunicorn.
builtins._bairro_rota_display = _bairro_rota_display
builtins._bairro_rota_key = _bairro_rota_key


# Configs seguras e leves para Render.
workers = 1
threads = 4
timeout = 120
keepalive = 5
preload_app = False
