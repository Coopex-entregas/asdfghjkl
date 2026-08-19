# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = Path(SPECPATH)

webview_hidden = collect_submodules('webview')
webview_datas = collect_data_files('webview')

# O pytz precisa dos arquivos de zoneinfo no EXE.
# Sem isso, pytz.timezone('America/Sao_Paulo') pode falhar no aplicativo empacotado.
pytz_datas = collect_data_files('pytz')

hidden = [
    'flask_socketio',
    'simple_websocket',
    'psycopg2',
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
    'pytz',
] + webview_hidden

datas = [
    (str(ROOT / 'templates'), 'templates'),
    (str(ROOT / 'static'), 'static'),
    (str(ROOT / 'data'), 'data'),
] + webview_datas + pytz_datas

a = Analysis(
    ['supervisao_desktop.py'],
    pathex=[str(ROOT)],
    binaries=[],
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
