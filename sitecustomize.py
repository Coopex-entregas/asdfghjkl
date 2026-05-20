"""Correções globais leves para compatibilidade do painel COOPEX.

Este arquivo é carregado automaticamente pelo Python na inicialização.
Ele evita erro 500 quando alguma versão do app.py chama helpers de bairro
antes deles existirem no escopo global do módulo.
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


# Fallback via builtins: quando app.py não tiver esses nomes no globals(),
# o Python procura em builtins e encontra aqui.
builtins._bairro_rota_display = _bairro_rota_display
builtins._bairro_rota_key = _bairro_rota_key
