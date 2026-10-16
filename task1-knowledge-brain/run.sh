#!/usr/bin/env bash
# One command: ./run.sh  -> creates venv, installs, runs the first crawl if memory is empty, serves on :8000
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-$(command -v python3.12 || command -v python3.13 || command -v python3)}
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -e ".[dev]"
[ -f .env ] || cp .env.example .env
if [ "$(.venv/bin/python -c 'from brain.core import Brain;b=Brain();print(b.db.execute("select count(*) from pages").fetchone()[0])')" = "0" ]; then
  echo "Memory is empty: running first crawl in the background (resumable, polite, ~1 page/sec)."
  .venv/bin/python -m brain crawl > data/crawl.log 2>&1 &
fi
exec .venv/bin/python -m brain serve
