@echo off
setlocal
title AUTOHAWK - Car Deal Scanner
color 0A

echo.
echo ============================================================
echo  AUTOHAWK - AI Car Deal Scanner
echo ============================================================
echo.

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    echo During installation tick: Add python.exe to PATH
    echo.
    pause
    exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.11+ is required.
    python --version
    echo.
    pause
    exit /b 1
)

echo Python:
python --version
echo.

if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo Created .env from .env.example
    )
)

if not exist "output" mkdir "output"
if not exist "logs" mkdir "logs"
if not exist "database" mkdir "database"

echo Checking dependencies...
python -c "import playwright, openpyxl, pandas, sqlalchemy, dotenv, openai, anthropic, requests, PIL; from google import genai" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Dependency installation failed.
        pause
        exit /b 1
    )
)

echo Checking Playwright browser...
python -m playwright install chromium
if errorlevel 1 (
    echo.
    echo ERROR: Playwright Chromium setup failed.
    pause
    exit /b 1
)

echo.
echo Starting AUTOHAWK scanner.
echo Output: output\deals.xlsx
echo Logs:   logs\autohawk.log
echo Stop:   Ctrl+C
echo.

python main.py

echo.
echo AUTOHAWK stopped.
pause
