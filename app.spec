# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / 'templates'), 'templates'),
    (str(ROOT / 'static'), 'static'),
    (str(ROOT / 'data'), 'data'),
]
binaries = []
hidden = [
    'flask_socketio',
    'simple_websocket',
    'psycopg2',
    'psycopg2.extensions',
    'engineio.async_drivers.threading',
    'engineio.async_drivers._websocket_wsgi',
    'escala_feature',
    'escala_api_session_fix',
    'escala_ajustes',
    'escala_troca',
    'agendamento_feature',
    'rotas_bairros_feature',
    'dashboard_comparativo_feature',
    'cooperado_arquivamento_feature',
    'historico_entregas_feature',
    'creditos_otimizacao_feature',
    'pagamentos_normalizacao_feature',
    'supervisao_live_feature',
    'sync_feature',
    'requests',
]

for pacote in (
    'webview',
    'pytz',
    'holidays',
    'requests',
    'urllib3',
    'certifi',
    'charset_normalizer',
    'idna',
):
    try:
        d, b, h = collect_all(pacote)
        datas += d
        binaries += b
        hidden += h
    except Exception:
        pass

for pacote in ('openpyxl', 'xlsxwriter'):
    try:
        hidden += collect_submodules(pacote)
    except Exception:
        pass

hidden += collect_submodules('engineio.async_drivers')
hidden += collect_submodules('socketio')
hidden = list(dict.fromkeys(hidden))

a = Analysis(
    ['supervisao_desktop.py'],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'cefpython3', 'gtk',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Supervisao',
    icon=str(ROOT / 'static' / 'supervisao.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Supervisao',
)
