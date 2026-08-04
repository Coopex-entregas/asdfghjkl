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


def _corrigir_serializacao_escala(escala_feature):
    """Remove estruturas internas não serializáveis dos candidatos da escala."""
    original = escala_feature.match_name
    if getattr(original, "_coopex_json_safe", False):
        return

    def match_name_json_safe(name, ctx):
        cooperado_id, status, candidatos, detalhe = original(name, ctx)
        candidatos_seguros = []

        for candidato in candidatos or []:
            if not isinstance(candidato, dict):
                continue

            item = {
                "id": candidato.get("id"),
                "nome": candidato.get("nome") or "",
            }

            if candidato.get("score") is not None:
                item["score"] = candidato.get("score")
            if candidato.get("motivo"):
                item["motivo"] = candidato.get("motivo")

            candidatos_seguros.append(item)

        return cooperado_id, status, candidatos_seguros, detalhe

    match_name_json_safe._coopex_json_safe = True
    escala_feature.match_name = match_name_json_safe


def post_worker_init(worker):
    """Instala os recursos separados depois que o Flask carrega app:app."""
    try:
        import app as app_module
        import escala_feature
        import escala_ajustes
        import escala_troca
        import agendamento_feature

        _corrigir_serializacao_escala(escala_feature)
        escala_feature.install(app_module)
        escala_ajustes.install(app_module, escala_feature)
        escala_troca.install(app_module, escala_feature)
        agendamento_feature.install(app_module)
        worker.log.info(
            "Escala semanal, ajustes, trocas e agendamentos COOPEX instalados."
        )
    except Exception:
        worker.log.exception("Falha ao instalar os recursos adicionais da COOPEX.")
        raise
