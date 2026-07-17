@echo off
rem Lanzador de la TUI de Oraqlo. Doble clic o: Oraqlo.bat [ruta\al\caso.json]
chcp 65001 >nul
cd /d "%~dp0"
python tui.py %*
if errorlevel 1 (
    echo.
    echo La TUI termino con error. Revisa el mensaje de arriba.
    pause
)
