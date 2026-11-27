@echo off
rem Avvio manuale (senza attività pianificata): lancia il watchdog senza finestra console.
setlocal
set SCRIPT_DIR=%~dp0
start "" pythonw "%SCRIPT_DIR%watchdog.py" %*
endlocal
