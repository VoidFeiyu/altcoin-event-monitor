@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Set up the virtual environment and install requirements as described in README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m klinebot.alpha_web --open
if errorlevel 1 (
    pause
    exit /b 1
)
