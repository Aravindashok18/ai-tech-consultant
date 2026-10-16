#!/usr/bin/env bash
# Needs Task 1 running on KB_URL (default http://localhost:8000). Serves the inbox on :8001.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-$(command -v python3.12 || command -v python3)}
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -e ".[dev]"
[ -f .env ] || cp .env.example .env
exec .venv/bin/python -m inbox
