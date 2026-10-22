@echo off
title Quant Trading System
cd /d %~dp0
echo ==========================================
echo   Quant Trading System (Web Version)
echo ==========================================
echo.
echo Checking dependencies...
python -c "import fastapi, uvicorn, pandas, numpy, sklearn, requests" 2>nul
if errorlevel 1 (
    echo First run: installing dependencies, please wait a few minutes...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependency install failed. Please run manually:
        echo     pip install -r requirements.txt
        pause
        exit /b 1
    )
)
echo Starting server...
python start.py
if errorlevel 1 pause
