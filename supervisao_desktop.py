# -*- coding: utf-8 -*-
"""
Supervisão Desktop
Abre o sistema Flask em uma janela nativa do Windows via pywebview.
Sem navegador, sem barra de endereço e sem terminal.
"""

import builtins
import ctypes
import logging
import os
import re
import socket
import sys
import threading
import time
import unicodedata
import urllib.request
from pathlib import Path

APP_NAME = "Supervisão"
MUTEX_NAME = "Local\\COOPEX_SUPERVISAO_DESKTOP_2026"


def _user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / "Supervisao"
    path.mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(parents=True, exist_ok=True)
    (path / "data").mkdir(parents=True, exist_ok=True)
    return path


USER_DIR = _user_data_dir()
LOG_FILE = USER_DIR / "logs" / "supervisao.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("supervisao")


def _message_box(title: str, message: str, error: bool = False):
    try:
        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, str(message), str(title), flags)
    except Exception:
        pass


def _single_instance():
    """Evita abrir duas instâncias locais usando o mesmo banco."""
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        last_error = ctypes.windll.kernel32.GetLastError()
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            _message_box(APP_NAME, "O Supervisão já está aberto.")
            return None
        return handle
    except Exception:
        return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


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


def _prepare_environment(port: int):
    builtins._bairro_rota_display = _bairro_rota_display
    builtins._bairro_rota_key = _bairro_rota_key

    os.environ["PORT"] = str(port)
    os.environ["SUPERVISAO_DESKTOP"] = "1"

    # Banco local persistente. A sincronização com o Render será feita por camada própria.
    db_path = (USER_DIR / "data" / "supervisao.sqlite3").resolve().as_posix()
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{db_path}")


def _corrigir_serializacao_escala(escala_feature):
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


def _install_features(app_module):
    """Replica no desktop os módulos que o Render instala via gunicorn.conf.py."""
    import escala_feature
    import escala_api_session_fix
    import escala_ajustes
    import escala_troca
    import agendamento_feature
    import rotas_bairros_feature
    import dashboard_comparativo_feature
    import cooperado_arquivamento_feature
    import historico_entregas_feature
    import creditos_otimizacao_feature
    import pagamentos_normalizacao_feature

    _corrigir_serializacao_escala(escala_feature)
    escala_feature.install(app_module)
    escala_api_session_fix.install(app_module, escala_feature)
    escala_ajustes.install(app_module, escala_feature)
    escala_troca.install(app_module, escala_feature)
    agendamento_feature.install(app_module)
    rotas_bairros_feature.install(app_module)
    dashboard_comparativo_feature.install(app_module)
    cooperado_arquivamento_feature.install(app_module)
    historico_entregas_feature.install(app_module)
    creditos_otimizacao_feature.install(app_module)
    pagamentos_normalizacao_feature.install(app_module)

    try:
        import supervisao_live_feature
        supervisao_live_feature.install(app_module)
    except Exception:
        log.exception("Falha ao instalar supervisao_live_feature")


def _wait_server(url: str, timeout: float = 25.0):
    deadline = time.time() + timeout
    last_exc = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=1.0) as r:
                if r.status == 200:
                    return True
        except Exception as exc:
            last_exc = exc
            time.sleep(0.20)
    if last_exc:
        raise RuntimeError(f"Servidor local não respondeu: {last_exc}")
    raise RuntimeError("Servidor local não respondeu dentro do prazo.")


def _run_server(app_module, port: int):
    try:
        app_module.socketio.run(
            app_module.app,
            host="127.0.0.1",
            port=port,
            debug=False,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        )
    except Exception:
        log.exception("Falha no servidor local")


def main():
    mutex = _single_instance()
    if mutex is None:
        return 0

    try:
        port = _free_port()
        _prepare_environment(port)

        import app as app_module
        _install_features(app_module)

        server_thread = threading.Thread(
            target=_run_server,
            args=(app_module, port),
            name="SupervisaoLocalServer",
            daemon=True,
        )
        server_thread.start()

        base_url = f"http://127.0.0.1:{port}"
        _wait_server(base_url)

        import webview

        # Mantém links externos no navegador padrão, mas o próprio sistema fica na janela.
        try:
            webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
            webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
        except Exception:
            pass

        window = webview.create_window(
            APP_NAME,
            url=base_url,
            width=1440,
            height=900,
            min_size=(1024, 680),
            resizable=True,
            fullscreen=False,
            confirm_close=False,
            background_color="#ffffff",
            text_select=True,
        )

        # Edge Chromium / WebView2: janela de app real, sem barra de endereço.
        webview.start(gui="edgechromium", debug=False, private_mode=False)
        return 0

    except Exception as exc:
        log.exception("Falha ao iniciar Supervisão Desktop")
        _message_box(
            "Supervisão - erro ao iniciar",
            f"O Supervisão não conseguiu abrir.\n\n"
            f"{exc}\n\n"
            f"Foi criado um relatório em:\n{LOG_FILE}",
            error=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
