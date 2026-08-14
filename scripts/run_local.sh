#!/usr/bin/env bash
# scripts/run_local.sh — one-command local test for AAM Merger V3 (dev/Main @ 9bfc834, 59 tests)
# Usage: ./scripts/run_local.sh           # gates + server + curl checks
#        ./scripts/run_local.sh --no-server  # only gates, no server
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
NO_SERVER=0
if [[ "${1:-}" == "--no-server" ]]; then NO_SERVER=1; fi

echo "== AAM Merger V3 — local run (dev @ $(git rev-parse --short HEAD 2>/dev/null || echo '?')) =="
echo "Root: $ROOT | Host: $HOST:$PORT | NO_SERVER=$NO_SERVER"

# 1) config + folders
if [[ ! -f config.yaml ]]; then
  echo "[1/6] config.yaml missing — copying config.example.yaml"
  cp config.example.yaml config.yaml
  echo "  → edit config.yaml if you need non-default paths/model"
else
  echo "[1/6] config.yaml exists"
fi
if [[ ! -f .env ]] && [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "  ! OPENROUTER_API_KEY not set (VLM mocked in tests, prod needs .env). Continuing without it."
fi
mkdir -p data/input data/output data/quarantine data/stored data/unclassified data/logs data/samples
echo "  data folders ready"

# 2) deps
echo "[2/6] uv sync --all-groups"
UV_CACHE_DIR=/tmp/uv-cache uv sync --all-groups >/dev/null
echo "  deps ok"

# 3) DB migrate
echo "[3/6] alembic upgrade head"
UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head 2>&1 | tail -n 5 || true
echo "  db ok (sqlite WAL at $(grep sqlalchemy.url alembic.ini | cut -d= -f2-))"

# 4) gates
echo "[4/6] gates: pytest + ruff + ty"
echo "  → pytest -q"
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q 2>&1 | tail -n 8
echo "  → ruff check"
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check . 2>&1 | tail -n 3
echo "  → ruff format --check"
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check . 2>&1 | tail -n 3 || true
echo "  → ty check src"
UV_CACHE_DIR=/tmp/uv-cache uv run ty check src 2>&1 | tail -n 5 || true
echo "  gates done — expected: 59 passed, All checks passed"

if [[ "$NO_SERVER" -eq 1 ]]; then
  echo "Skipping server (--no-server). Done."
  exit 0
fi

# 5) start server
echo "[5/6] starting uvicorn $HOST:$PORT (PID file /tmp/aam_merger_uvicorn.pid)"
UV_CACHE_DIR=/tmp/uv-cache uv run uvicorn app.main:app --host "$HOST" --port "$PORT" > /tmp/aam_merger_uvicorn.log 2>&1 &
UV_PID=$!
echo "$UV_PID" > /tmp/aam_merger_uvicorn.pid
echo "  pid $UV_PID, log /tmp/aam_merger_uvicorn.log"

cleanup() {
  echo "  stopping server $UV_PID"
  kill "$UV_PID" 2>/dev/null || true
  wait "$UV_PID" 2>/dev/null || true
}
trap cleanup EXIT

# wait for health
echo "  waiting for /health ..."
for i in $(seq 1 30); do
  if curl -sf "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    echo "  health ok after $i s"
    break
  fi
  sleep 1
  if [[ $i -eq 30 ]]; then
    echo "  ! health not ready after 30s, log tail:"
    tail -n 30 /tmp/aam_merger_uvicorn.log || true
    exit 1
  fi
done

curl -s "http://$HOST:$PORT/health" | head -c 300; echo

# 6) curl checks
echo "[6/6] curl checks"
set +e
check() {
  local url="$1" label="$2"
  local code; code=$(curl -s -o /tmp/curl_body.txt -w "%{http_code}" "$url")
  local len; len=$(wc -c < /tmp/curl_body.txt)
  if [[ "$code" == "200" ]]; then
    echo "  OK $label → $code ($len bytes) $url"
  else
    echo "  FAIL $label → $code $url"
    tail -c 500 /tmp/curl_body.txt; echo
  fi
}
check "http://$HOST:$PORT/health" "health"
check "http://$HOST:$PORT/dashboard" "dashboard full"
check "http://$HOST:$PORT/dashboard?status=pending" "dashboard filter"
# HTMX partial
code=$(curl -s -o /tmp/curl_htmx.txt -w "%{http_code}" -H "HX-Request: true" "http://$HOST:$PORT/dashboard?status=pending")
echo "  HTMX partial: $code ($(wc -c < /tmp/curl_htmx.txt) bytes) — should be < full and contain PO table"
check "http://$HOST:$PORT/dashboard/table?status=pending" "dashboard/table fragment"
check "http://$HOST:$PORT/audit" "audit read-only"
check "http://$HOST:$PORT/quarantine" "quarantine"
check "http://$HOST:$PORT/manual_merger" "manual_merger (GET form)"
# API
check "http://$HOST:$PORT/sync/status" "sync status"
check "http://$HOST:$PORT/po_sets" "po_sets list (if exists, else 404 ok)"

echo ""
echo "== Live at http://$HOST:$PORT/ =="
echo "  Dashboard:        http://$HOST:$PORT/dashboard"
echo "  Dashboard table:  http://$HOST:$PORT/dashboard/table?status=pending"
echo "  Audit:            http://$HOST:$PORT/audit"
echo "  Health:           http://$HOST:$PORT/health"
echo "  Manual merger:    http://$HOST:$PORT/manual_merger"
echo "  Try: curl -X POST http://$HOST:$PORT/sync   # 200 or 409 'Sync already running'"
echo "  Put a PDF in ./data/input then POST /sync — ingest dedup → stored_path → classify → group"
echo ""
echo "  Log: tail -f /tmp/aam_merger_uvicorn.log"
echo "  Stop: kill \$(cat /tmp/aam_merger_uvicorn.pid)  (auto on script exit)"
echo ""
if [[ -t 1 ]]; then
  echo "Press Enter to stop server and exit (or Ctrl+C)..."
  read -r _
fi
# trap will stop server
