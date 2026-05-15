@echo off
title SkyrimNet Provider Bridge
echo [1/3] Checking for virtual environment...

if not exist .venv (
    echo No .venv found. Creating one with uv...
    uv venv
)

echo [2/3] Syncing dependencies from requirements.txt...
uv pip install -r requirements.txt

echo [3/3] Starting Bridge server...
echo.
uv run python server.py

pause
