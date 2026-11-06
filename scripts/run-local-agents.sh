#!/usr/bin/env bash
# Starts the 8 agents as local processes on ports 8101 to 8108, with the scripted model (no key) and telemetry off.
# They read through the server's MCP tools at http://127.0.0.1:8000/mcp. Stop them with Ctrl-C, or `scripts/run-local-agents.sh stop`.
#   scripts/run-local-agents.sh          start and wait
#   scripts/run-local-agents.sh start    start in the background
#   scripts/run-local-agents.sh stop
set -euo pipefail
unset VIRTUAL_ENV
cd "$(dirname "$0")/.."
ROOT=$(pwd)
RUN_DIR="${PHARMALIGN_RUN_DIR:-$ROOT/.run/agents}"
AGENTS=(dossier-reader checklist-assessor sameness challenger coordinator reliance-watch gap-analyst knowledge-assistant)
PORT_BASE=8101

stop_all() {
  for pidfile in "$RUN_DIR"/*.pid; do
    [ -e "$pidfile" ] || continue
    kill "$(cat "$pidfile")" 2>/dev/null || true
    rm -f "$pidfile"
  done
}

if [ "${1:-}" = "stop" ]; then stop_all; exit 0; fi

mkdir -p "$RUN_DIR"
export PHARMALIGN_MODEL_MODE=scripted
export PHARMALIGN_OTEL_TO_CLOUD=false
export PHARMALIGN_MCP_URL="${PHARMALIGN_MCP_URL:-http://127.0.0.1:8000/mcp}"
export PHARMALIGN_DEMO_DIR="$ROOT/demo"

stop_all
for i in "${!AGENTS[@]}"; do
  name=${AGENTS[$i]}
  port=$((PORT_BASE + i))
  (cd "agents/$name" && exec uv run --quiet uvicorn app.fast_api_app:app --host 127.0.0.1 --port "$port" >"$RUN_DIR/$name.log" 2>&1) &
  echo $! >"$RUN_DIR/$name.pid"
done

echo "Waiting for the agents..."
for i in "${!AGENTS[@]}"; do
  port=$((PORT_BASE + i))
  for _ in $(seq 1 120); do
    curl -fs "http://127.0.0.1:$port/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fs "http://127.0.0.1:$port/health" >/dev/null || { echo "${AGENTS[$i]} did not start; see $RUN_DIR/${AGENTS[$i]}.log" >&2; stop_all; exit 1; }
  echo "  ${AGENTS[$i]} on $port"
done

if [ "${1:-}" = "start" ]; then echo "Agents are up."; exit 0; fi
trap stop_all EXIT INT TERM
wait
