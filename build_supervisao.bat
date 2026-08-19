@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo   SUPERVISAO - APLICATIVO WINDOWS
echo   JANELA PROPRIA / ONEDIR / SEM UPX
echo ===============================================
echo.

if not exist .venv (
  echo Criando ambiente virtual...
  py -m venv .venv
  if errorlevel 1 goto :erro
)

call .venv\Scripts\activate.bat
if errorlevel 1 goto :erro

echo Atualizando pip...
python -m pip install --upgrade pip
if errorlevel 1 goto :erro

echo Instalando dependencias do sistema...
pip install -r requirements.txt
if errorlevel 1 goto :erro

echo Instalando janela nativa do Supervisao...
pip install pywebview==6.2.1
if errorlevel 1 goto :erro

echo Instalando PyInstaller...
pip install --upgrade pyinstaller
if errorlevel 1 goto :erro

echo Limpando builds antigos...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo Gerando aplicativo...
pyinstaller --noconfirm --clean app.spec
if errorlevel 1 goto :erro

if exist "dist\Supervisao\Supervisao.exe" (
  echo.
  echo ===============================================
  echo PRONTO.
  echo Abra:
  echo dist\Supervisao\Supervisao.exe
  echo ===============================================
  start "" explorer.exe "dist\Supervisao"
  pause
  exit /b 0
)

:erro
echo.
echo ===============================================
echo ERRO AO GERAR O SUPERVISAO.
echo Copie a mensagem desta janela e envie.
echo ===============================================
pause
exit /b 1
