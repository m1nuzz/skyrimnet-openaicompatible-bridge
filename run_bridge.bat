@echo off
title SkyrimNet Provider Bridge

echo [0/3] Checking for existing instances on port 4000...
:: Use powershell for a more reliable port check and kill
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 4000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo [1/3] Checking for virtual environment...

if not exist .venv (
    echo No .venv found. Creating one with Python 3.12...
    uv venv --python 3.12
)

echo [2/3] Syncing dependencies from requirements.txt...
uv pip install -r requirements.txt

echo [3/3] Starting Bridge server...
echo.
uv run python server.py

if not defined NO_PAUSE pause
