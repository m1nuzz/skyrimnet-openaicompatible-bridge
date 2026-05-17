#!/usr/bin/env bash
set -euo pipefail

# The bridge is a flat-layout Python script in the repo root — there is no
# `bridge/` package despite earlier docs implying one.
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python server.py
