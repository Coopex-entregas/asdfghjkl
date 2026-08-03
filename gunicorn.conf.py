"""Configuração leve do Gunicorn para o painel COOPEX.

O arquivo também instala a escala semanal somente depois que o app Flask está
carregado no worker. Assim o recurso fica separado do app.py principal.
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


builtins._bairro_rota_display = _bairro_rota_display
builtins._bairro_rota_key = _bairro_rota_key

workers = 1
threads = 4
timeout = 120
keepalive = 5
preload_app = False


def post_worker_init(worker):
    """Instala a escala após o Gunicorn carregar app:app."""
    try:
        import app as app_module
        from escala_feature import install
        install(app_module)
        worker.log.info("Escala semanal COOPEX instalada.")
    except Exception:
        worker.log.exception("Falha ao instalar a escala semanal COOPEX.")
        raise
