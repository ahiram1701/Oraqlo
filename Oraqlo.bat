@echo off
rem Modo DESARROLLO: arranca Oraqlo desde este checkout y abre el navegador.
rem Para el uso diario instala con scripts\install-windows.ps1 (se actualiza solo).
chcp 65001 >nul
cd /d "%~dp0"
start "" http://127.0.0.1:8765
python -m uv run oraqlo serve %*
if errorlevel 1 (
    echo.
    echo Oraqlo termino con error. Revisa el mensaje de arriba.
    pause
)
