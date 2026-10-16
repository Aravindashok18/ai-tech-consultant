#!/usr/bin/env bash
# Needs Task 1 on KB_URL. Serves on :8002
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-$(command -v python3.12 || command -v python3)}
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -e ".[dev]"
[ -f .env ] || cp .env.example .env
exec .venv/bin/python -m scope serve
