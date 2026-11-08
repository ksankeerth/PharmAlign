#!/bin/bash
# The golden-set stack, set up like the cloud: the server on 8310 against a database copy (default golden_ka_a, port 5546) with Gemini
# embeddings and no reranker, and this agent on 8311 on Vertex. Own pid file and logs, so it never clashes with stack.sh.
#   eval/golden_stack.sh start [on|off]   start both; on/off sets PHARMALIGN_KA_PIPELINE (default off)
#   eval/golden_stack.sh stop             stop both, by the PIDs this script saved, and only if they are still these uvicorns
#   eval/golden_stack.sh status
# It needs GOLDEN_DB to be a copy you made; it never touches ports 8000, 5180 or 5544, never uses docker, and never stops a process it didn't start.
set -euo pipefail
A=$(cd "$(dirname "$0")/.." && pwd)                # agents/knowledge-assistant
W=$(cd "$A/../.." && pwd)                            # the worktree
M=${PHARMALIGN_MAIN_TREE:-$W}   # only for its server venv
RUNS=$A/eval/runs
PIDS=$RUNS/golden-stack.pids
DB=${GOLDEN_DB:-golden_ka_a}
mkdir -p "$RUNS"

alive() { [ -n "${1:-}" ] && ps -p "$1" -o command= 2>/dev/null | grep -Eq "uvicorn.*--port $2|mcp_tap.py --listen $2"; }

start() {
  local pipeline=${1:-off}
  [ -s "$PIDS" ] && { echo "already started (see $PIDS). Run stop first."; exit 1; }
  for p in 8310 8311 8320; do
    if lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1; then echo "port $p is in use by something else. Not starting."; exit 1; fi
  done
  (
    export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONPATH=$W/server
    export PHARMALIGN_ENV=simulation PHARMALIGN_AGENT_MODE=http PHARMALIGN_MCP_ENABLED=true PHARMALIGN_WORKER_ENABLED=false
    export PHARMALIGN_DATABASE_URL=postgresql+asyncpg://pharmalign:pharmalign@localhost:5546/$DB
    export PHARMALIGN_EMBEDDER=gemini-embedding-001 PHARMALIGN_RERANKER=off GOOGLE_CLOUD_PROJECT=pharmalign-sk GOOGLE_CLOUD_LOCATION=me-central1
    export PHARMALIGN_AGENT_URL_KNOWLEDGE_ASSISTANT=http://127.0.0.1:8311 PHARMALIGN_CONNECTOR_BASE_URL=http://127.0.0.1:8310/mock
    cd "$W/server" && exec "$M/server/.venv/bin/python" -m uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8310
  ) >"$RUNS/golden-server.log" 2>&1 &
  local server=$!
  # a logging pass-through on 8320 between the agent and the server, so the ADK path's searches can be read
  ( cd "$A" && exec .venv/bin/python eval/mcp_tap.py --listen 8320 --target http://127.0.0.1:8310 --log "$RUNS/golden-mcp.jsonl" ) >"$RUNS/golden-tap.log" 2>&1 &
  local tap=$!
  (
    export PHARMALIGN_MCP_URL=http://127.0.0.1:8320/mcp PHARMALIGN_KA_PIPELINE=$pipeline PHARMALIGN_KA_TURN_LOG=${GOLDEN_TURN_LOG:-$RUNS/golden-turns.jsonl}
    export PHARMALIGN_OTEL_TO_CLOUD=false PHARMALIGN_MODEL_MODE=vertex GOOGLE_GENAI_USE_ENTERPRISE=true GOOGLE_CLOUD_PROJECT=pharmalign-sk GOOGLE_CLOUD_LOCATION=global
    # In eval runs a 429 should affect its own turn, not the next six.
    export PHARMALIGN_KA_COOLDOWN_SECONDS=${PHARMALIGN_KA_COOLDOWN_SECONDS:-10}
    cd "$A" && exec .venv/bin/python -m uvicorn app.fast_api_app:app --host 127.0.0.1 --port 8311
  ) >"$RUNS/golden-agent.log" 2>&1 &
  local agent=$!
  printf '%s 8310\n%s 8311\n%s 8320\n' "$server" "$agent" "$tap" >"$PIDS"
  echo "server pid $server (8310), agent pid $agent (8311), pipeline $pipeline. Logs in $RUNS."
  for i in $(seq 1 90); do
    if curl -sf http://127.0.0.1:8310/health >/dev/null 2>&1 && curl -sf http://127.0.0.1:8311/health >/dev/null 2>&1; then
      echo "both healthy after ${i}s"
      # Load the embedder and reranker before the first timed turn: a cold search takes longer than a turn should.
      local t0=$SECONDS
      curl -sf -o /dev/null -X POST http://127.0.0.1:8310/api/v1/kb-search -H 'Authorization: Bearer p-sam' -H 'Content-Type: application/json' \
        -d '{"query": "marketing approval validity", "top_k": 3}' && echo "search warmed up in $((SECONDS - t0))s" || echo "warm-up search failed"
      return 0
    fi
    alive "$server" 8310 && alive "$agent" 8311 || { echo "a process exited. See the logs."; stop; exit 1; }
    sleep 1
  done
  echo "not healthy after 90s. See the logs."; exit 1
}

stop() {
  [ -s "$PIDS" ] || { echo "nothing started by this script"; return 0; }
  while read -r pid port; do
    if alive "$pid" "$port"; then kill "$pid" && echo "stopped $pid ($port)"; else echo "$pid ($port) not running"; fi
  done <"$PIDS"
  rm -f "$PIDS"
}

status() {
  [ -s "$PIDS" ] || { echo "not started"; return 0; }
  while read -r pid port; do alive "$pid" "$port" && echo "$pid ($port) running" || echo "$pid ($port) not running"; done <"$PIDS"
}

case "${1:-}" in
  start) start "${2:-off}" ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start [on|off] | stop | status"; exit 2 ;;
esac
