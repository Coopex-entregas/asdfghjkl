# -*- coding: utf-8 -*-
r"""
Supervisão Desktop — offline-first com sincronização.

- Janela própria Windows via pywebview/WebView2.
- SEMPRE usa SQLite local.
- SQLite fica em %LOCALAPPDATA%\Supervisao\data\supervisao.sqlite3.
- Sincroniza com o Render através de sync_feature.py quando houver internet.
- Se o Render estiver fora/deploy, o app continua funcionando no SQLite.
- Ajusta cache/mmap do SQLite conforme a memória RAM do computador.
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
REMOTE_URL_DEFAULT = "https://escalas-2-1.onrender.com"


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
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("supervisao")


def _message_box(title: str, message: str, error: bool = False):
    try:
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, str(message), str(title), flags)
    except Exception:
        pass


def _single_instance():
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.windll.kernel32.GetLastError() == 183:
            _message_box(APP_NAME, "O Supervisão já está aberto.")
            return None
        return handle
    except Exception:
        return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ram_total_gb() -> float:
    """Lê RAM do Windows sem depender de psutil."""
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return st.ullTotalPhys / (1024 ** 3)
    except Exception:
        pass
    return 8.0


def _configure_memory_accelerator():
    ram = _ram_total_gb()
    if ram >= 16:
        cache_mb, mmap_mb = 256, 512
    elif ram >= 8:
        cache_mb, mmap_mb = 128, 256
    else:
        cache_mb, mmap_mb = 64, 128

    os.environ["SUPERVISAO_SQLITE_CACHE_MB"] = str(cache_mb)
    os.environ["SUPERVISAO_SQLITE_MMAP_MB"] = str(mmap_mb)
    log.info(
        "Acelerador de memória: RAM=%.1fGB cache=%sMB mmap=%sMB",
        ram, cache_mb, mmap_mb,
    )


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


def _prepare_environment(port: int, self_test: bool = False):
    builtins._bairro_rota_display = _bairro_rota_display
    builtins._bairro_rota_key = _bairro_rota_key

    os.environ["PORT"] = str(port)
    os.environ["SUPERVISAO_DESKTOP"] = "1"
    os.environ["SUPERVISAO_OFFLINE"] = "1"

    # Chamadas ao Flask local nunca devem passar por proxy.
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"

    os.environ.setdefault("SUPERVISAO_REMOTE_URL", REMOTE_URL_DEFAULT)

    _configure_memory_accelerator()

    nome = "supervisao_selftest.sqlite3" if self_test else "supervisao.sqlite3"
    db_path = (USER_DIR / "data" / nome).resolve().as_posix()

    # IMPORTANTE: força SQLite. Não usa setdefault.
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    return Path(db_path)


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


def _attach_flask_logs(app_module):
    try:
        handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        handler.setLevel(logging.INFO)
        for logger_obj in (app_module.app.logger, logging.getLogger("werkzeug")):
            logger_obj.setLevel(logging.INFO)
            if not any(
                isinstance(h, logging.FileHandler)
                and getattr(h, "baseFilename", "") == str(LOG_FILE)
                for h in logger_obj.handlers
            ):
                logger_obj.addHandler(handler)
    except Exception:
        log.exception("Falha configurando logs Flask")


def _init_core_database(app_module):
    with app_module.app.app_context():
        app_module.db.create_all()
        app_module.db.session.commit()
    log.info(
        "Banco local principal inicializado: %s",
        app_module.app.config.get("SQLALCHEMY_DATABASE_URI"),
    )


def _install_features(app_module):
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

    # Tabelas que módulos adicionais registraram.
    with app_module.app.app_context():
        app_module.db.create_all()
        app_module.db.session.commit()

    # A sincronização deve entrar por último para enxergar todas as tabelas.
    try:
        import sync_feature
        sync_feature.install(app_module)
    except Exception:
        log.exception("Falha ao instalar sync_feature")
        raise


def _wait_server(url: str, timeout: float = 30.0):
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


def _self_test(app_module, test_db: Path) -> int:
    try:
        import pytz
        import holidays

        pytz.timezone("America/Sao_Paulo")
        holidays.Brazil(years=[2026])

        _attach_flask_logs(app_module)
        _init_core_database(app_module)
        _install_features(app_module)

        client = app_module.app.test_client()
        if client.get("/healthz").status_code != 200:
            raise RuntimeError("/healthz falhou")
        if client.get("/readyz").status_code != 200:
            raise RuntimeError("/readyz falhou")

        with client.session_transaction() as sess:
            sess["is_admin"] = True
            sess["is_master"] = True
            sess["admin_user"] = "coopex"

        r = client.get("/admin")
        if r.status_code != 200:
            raise RuntimeError(
                f"/admin retornou {r.status_code}: {r.get_data(as_text=True)[:1000]}"
            )

        log.info("SELF-TEST OK")
        return 0
    except Exception:
        log.exception("SELF-TEST FALHOU")
        return 10
    finally:
        try:
            app_module.db.session.remove()
            app_module.db.engine.dispose()
        except Exception:
            pass
        try:
            if test_db.exists():
                test_db.unlink()
            for suf in ("-wal", "-shm"):
                p = Path(str(test_db) + suf)
                if p.exists():
                    p.unlink()
        except Exception:
            pass


def main():
    self_test = "--self-test" in sys.argv

    if self_test:
        try:
            db_path = _prepare_environment(0, self_test=True)
            import app as app_module
            return _self_test(app_module, db_path)
        except Exception:
            log.exception("Falha ao iniciar self-test")
            return 11

    mutex = _single_instance()
    if mutex is None:
        return 0

    try:
        port = _free_port()
        _prepare_environment(port)

        import app as app_module
        _attach_flask_logs(app_module)

        _init_core_database(app_module)
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

        try:
            webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
            webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
        except Exception:
            pass

        webview.create_window(
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

        webview.start(gui="edgechromium", debug=False, private_mode=False)
        return 0

    except Exception as exc:
        log.exception("Falha ao iniciar Supervisão Desktop")
        _message_box(
            "Supervisão - erro ao iniciar",
            f"O Supervisão não conseguiu abrir.\n\n"
            f"{exc}\n\n"
            f"Relatório:\n{LOG_FILE}",
            error=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
