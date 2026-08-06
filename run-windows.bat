@echo off
setlocal EnableExtensions
rem ============================================================
rem  Optional Windows launcher: creates the Python environment on
rem  first run, then starts AppleSync.
rem
rem  Command-line equivalent:
rem      python -m venv .venv
rem      .venv\Scripts\python -m pip install .
rem      .venv\Scripts\python -m applesync
rem
rem  This script downloads nothing beyond the Python dependencies
rem  declared in pyproject.toml.
rem ============================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python environment...
    python -m venv .venv || (
        echo [ERROR] Python 3.12+ is required and must be on the PATH.
        echo         https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

.venv\Scripts\python.exe -c "import applesync, PySide6, pymobiledevice3" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies ^(a few minutes on the first run^)...
    .venv\Scripts\python.exe -m pip install . || (
        echo [ERROR] Installation failed. Check the network connection.
        pause
        exit /b 1
    )
)

start "AppleSync" .venv\Scripts\pythonw.exe -m applesync %*
exit /b 0
