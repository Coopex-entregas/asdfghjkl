@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   SUPERVISAO - BUILD OFFLINE-FIRST + SINCRONIZACAO
echo ============================================================
echo.

if not exist "app.py" (
  echo ERRO: execute este build dentro de C:\Sistemas\asdfghjkl
  echo app.py nao encontrado.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo [1/9] Criando ambiente virtual...
  py -m venv .venv
  if errorlevel 1 goto :erro
) else (
  echo [1/9] Ambiente virtual encontrado.
)

call .venv\Scripts\activate.bat
if errorlevel 1 goto :erro

echo [2/9] Atualizando pip...
python -m pip install --upgrade pip
if errorlevel 1 goto :erro

echo [3/9] Instalando dependencias...
pip install -r requirements.txt
if errorlevel 1 goto :erro

echo [4/9] Instalando aplicativo Windows...
pip install --upgrade pyinstaller
if errorlevel 1 goto :erro
pip install --upgrade pywebview==6.2.1 requests==2.32.3
if errorlevel 1 goto :erro

echo [5/9] Revalidando pytz e holidays...
pip install --upgrade --force-reinstall holidays==0.77 pytz==2025.2
if errorlevel 1 goto :erro
python -c "import pytz,holidays,requests; pytz.timezone('America/Sao_Paulo'); holidays.Brazil(years=[2026]); print('Dependencias dinamicas OK')"
if errorlevel 1 goto :erro

echo [6/9] Validando arquivos Python...
python -m py_compile supervisao_desktop.py sync_feature.py supervisao_live_feature.py
if errorlevel 1 goto :erro

echo [7/9] Limpando build anterior...
powershell -NoProfile -Command "Get-Process Supervisao -ErrorAction SilentlyContinue | Stop-Process -Force"
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo [8/9] Gerando Supervisao.exe...
pyinstaller --noconfirm --clean app.spec
if errorlevel 1 goto :erro

if not exist "dist\Supervisao\Supervisao.exe" (
  echo ERRO: executavel nao criado.
  goto :erro
)

echo [9/9] Testando o EXE e a tela /admin...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Start-Process -FilePath '.\dist\Supervisao\Supervisao.exe' -ArgumentList '--self-test' -Wait -PassThru; exit $p.ExitCode"
if errorlevel 1 goto :erroteste

echo.
echo ============================================================
echo   PRONTO - APP GERADO E TESTADO
echo ============================================================
echo.
echo EXE:
echo   %CD%\dist\Supervisao\Supervisao.exe
echo.
echo BANCO LOCAL:
echo   %%LOCALAPPDATA%%\Supervisao\data\supervisao.sqlite3
echo.
echo O primeiro login com internet faz a copia inicial do Render.
echo Depois disso, o SQLite continua funcionando mesmo sem internet.
echo.
start "" explorer.exe "dist\Supervisao"
pause
exit /b 0

:erroteste
echo.
echo ============================================================
echo   O EXE FOI GERADO, MAS O SELF-TEST FALHOU
echo ============================================================
echo Veja:
echo   %%LOCALAPPDATA%%\Supervisao\logs\supervisao.log
echo.
pause
exit /b 2

:erro
echo.
echo ============================================================
echo   ERRO NO BUILD
echo ============================================================
echo Nao use o executavel antigo como se fosse esta versao.
echo.
pause
exit /b 1
