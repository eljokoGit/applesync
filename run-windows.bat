@echo off
setlocal EnableExtensions
rem ============================================================
rem  Lanceur Windows optionnel : cree l'environnement Python au
rem  premier lancement, puis demarre AppleSync.
rem
rem  Equivalent en ligne de commande :
rem      python -m venv .venv
rem      .venv\Scripts\python -m pip install .
rem      .venv\Scripts\python -m applesync
rem
rem  Ce script ne telecharge rien d'autre que les dependances
rem  Python declarees dans pyproject.toml.
rem ============================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creation de l'environnement Python...
    python -m venv .venv || (
        echo [ERREUR] Python 3.12+ est requis et doit etre dans le PATH.
        echo          https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

.venv\Scripts\python.exe -c "import applesync, PySide6, pymobiledevice3" >nul 2>&1
if errorlevel 1 (
    echo Installation des dependances ^(quelques minutes la premiere fois^)...
    .venv\Scripts\python.exe -m pip install . || (
        echo [ERREUR] Installation impossible. Verifiez la connexion reseau.
        pause
        exit /b 1
    )
)

start "AppleSync" .venv\Scripts\pythonw.exe -m applesync %*
exit /b 0
