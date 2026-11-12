@echo off
setlocal
cd /d "%~dp0"
title ProductStudio

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 (
    echo Python 3.10 or newer is required. Get it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" in the installer.
    goto fail
)

if not exist ".venv\ready" (
    echo Setting up. This happens only once and takes a few minutes.
    %PY% -m venv .venv || goto fail
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto fail
    echo ok> ".venv\ready"
)

if not exist "models\isnet-general-use.onnx" (
    ".venv\Scripts\python.exe" download_model.py || goto fail
)

start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:fail
echo.
echo ProductStudio could not start. The message above says why.
pause
exit /b 1
