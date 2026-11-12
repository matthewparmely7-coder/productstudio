@echo off
setlocal
cd /d "%~dp0"
title Build ProductStudio

if not exist ".venv\ready" (
    echo Run run.cmd once first. It installs everything the build needs.
    pause
    exit /b 1
)
if not exist "models\isnet-general-use.onnx" (
    ".venv\Scripts\python.exe" download_model.py || goto fail
)
".venv\Scripts\python.exe" -m pip install "pyinstaller>=6" || goto fail
".venv\Scripts\python.exe" -m PyInstaller ProductStudio.spec --noconfirm || goto fail
echo.
echo Done. The app is in dist\ProductStudio\ProductStudio.exe
pause
exit /b 0

:fail
echo.
echo The build failed. The message above says why.
pause
exit /b 1
