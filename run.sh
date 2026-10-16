#!/usr/bin/env bash
# Starts the backend (http://127.0.0.1:8090) and the frontend (http://localhost:5173).
# First run sets up the Python venv and npm packages. Ctrl+C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

PYTHON="${PYTHON:-$(command -v python3.12 || command -v python3)}"
if [[ ! -d "$ROOT/backend/.venv" ]]; then
  echo "Setting up backend (first run)…"
  "$PYTHON" -m venv "$ROOT/backend/.venv"
  "$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt" playwright
fi
if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "Setting up frontend (first run)…"
  (cd "$ROOT/frontend" && npm install --silent)
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

(cd "$ROOT/backend" && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8090) &
(cd "$ROOT/frontend" && npx vite --port 5173 --strictPort) &

echo
echo "  Job Watcher → http://localhost:5173"
echo
wait
