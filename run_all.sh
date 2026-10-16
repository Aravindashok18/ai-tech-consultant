#!/usr/bin/env bash
# One command for the whole project: ./run_all.sh
#   Task 1 Knowledge Brain  http://localhost:8000   (chat + expert queue + /docs)
#   Task 2 Inbox Agent      http://localhost:8001
#   Task 3 Scope Agents     http://localhost:8002
set -euo pipefail
cd "$(dirname "$0")"
trap 'kill $(jobs -p) 2>/dev/null' EXIT
(cd task1-knowledge-brain && ./run.sh) & sleep 25
(cd task2-inbox-agent && ./run.sh) &
(cd task3-scope-agents && ./run.sh) &
wait
