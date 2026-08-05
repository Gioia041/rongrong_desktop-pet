@echo off
setlocal
cd /d "%~dp0"

where pyw >nul 2>nul
if not errorlevel 1 (
    start "" pyw main.py
    exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw main.py
    exit /b 0
)

rem Codex desktop may provide a user-local Python runtime without adding it to PATH.
set "CODEX_PYTHONW=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if exist "%CODEX_PYTHONW%" (
    start "" "%CODEX_PYTHONW%" "%~dp0main.py"
    exit /b 0
)

echo Python 3.10 or newer was not found.
echo Install Python and make sure it is available on PATH, then run this file again.
pause
