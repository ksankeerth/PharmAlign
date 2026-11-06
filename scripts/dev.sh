#!/usr/bin/env bash
# One command for the whole local setup: database, server, the 8 agents and the web UI.
#   scripts/dev.sh start             start whatever is not running, then print the addresses
#   scripts/dev.sh stop              stop the web UI, agents and server (the database keeps running)
#   scripts/dev.sh stop --all        also stop the database
#   scripts/dev.sh restart           stop, then start
#   scripts/dev.sh status            what is running
#   scripts/dev.sh logs [name]       follow a log: server, web, or an agent name
# Options for start and restart:
#   --reseed       empty the database and load the demo data again (with the real law text when found)
#   --no-agents    don't start the agents: the server answers from recordings (replay)
#   --no-web       don't start the web UI
# The database is seeded on the first start, when it is empty. The agents use the scripted model (no key).
set -euo pipefail
unset VIRTUAL_ENV
cd "$(dirname "$0")/.."
ROOT=$(pwd)
# Arabic search: read Arabic PDFs from their text layer, and rerank Arabic questions. The reranker needs the bge-m3 extra.
export PHARMALIGN_ARABIC_TEXT_LAYER="${PHARMALIGN_ARABIC_TEXT_LAYER:-true}" PHARMALIGN_RERANKER="${PHARMALIGN_RERANKER:-bge-reranker-v2-m3}"
RUN_DIR="${PHARMALIGN_RUN_DIR:-$ROOT/.run}"
SERVER_PORT=8000
WEB_PORT=5180
AGENTS=(dossier-reader checklist-assessor sameness challenger coordinator reliance-watch gap-analyst knowledge-assistant)
AGENT_PORT_BASE=8101

cmd="${1:-start}"
[ $# -gt 0 ] && shift
RESEED=0 AGENTS_ON=1 WEB_ON=1 STOP_DB=0
for arg in "$@"; do
  case "$arg" in
    --reseed) RESEED=1 ;;
    --no-agents) AGENTS_ON=0 ;;
    --no-web) WEB_ON=0 ;;
    --all) STOP_DB=1 ;;
    *) [ "$cmd" = "logs" ] || { echo "Unknown option: $arg" >&2; exit 2; } ;;
  esac
done

mkdir -p "$RUN_DIR"

kill_tree() {
  local pid=$1 child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do kill_tree "$child"; done
  kill "$pid" 2>/dev/null || true
}

alive() { [ -f "$RUN_DIR/$1.pid" ] && kill -0 "$(cat "$RUN_DIR/$1.pid")" 2>/dev/null; }

stop_one() {
  if alive "$1"; then kill_tree "$(cat "$RUN_DIR/$1.pid")"; echo "  stopped $1"; fi
  rm -f "$RUN_DIR/$1.pid"
}

wait_for() { # name url seconds
  local i
  for ((i = 0; i < $3; i++)); do
    curl -fs "$2" >/dev/null 2>&1 && return 0
    alive "$1" || { echo "$1 stopped while starting; see $RUN_DIR/$1.log" >&2; return 1; }
    sleep 1
  done
  echo "$1 did not answer at $2 in $3 s; see $RUN_DIR/$1.log" >&2
  return 1
}

port_busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

agent_env() {
  local i name var
  for i in "${!AGENTS[@]}"; do
    name=${AGENTS[$i]}
    var=$(echo "$name" | tr 'a-z-' 'A-Z_')
    printf 'PHARMALIGN_AGENT_URL_%s=http://127.0.0.1:%d\n' "$var" $((AGENT_PORT_BASE + i))
  done
}

do_stop() {
  echo "Stopping..."
  stop_one web
  PHARMALIGN_RUN_DIR="$RUN_DIR/agents" scripts/run-local-agents.sh stop
  stop_one server
  if [ "$STOP_DB" = 1 ]; then docker compose stop db >/dev/null && echo "  stopped db"; fi
}

do_start() {
  echo "Database..."
  docker compose up -d --wait db >/dev/null

  # Seeding happens while the server is down, so it never reads half-loaded data.
  if [ "$RESEED" = 1 ]; then stop_one web; PHARMALIGN_RUN_DIR="$RUN_DIR/agents" scripts/run-local-agents.sh stop; stop_one server; fi
  (cd server && PHARMALIGN_ENV=simulation .venv/bin/alembic upgrade head >/dev/null)
  people=$(docker compose exec -T db psql -U pharmalign -d pharmalign -tAc "select count(*) from person" | tr -d '[:space:]')
  if [ "$RESEED" = 1 ] || [ "${people:-0}" = 0 ]; then
    if alive server; then echo "The database is empty but the server is running. Run: scripts/dev.sh restart --reseed" >&2; exit 1; fi
    echo "Loading the demo data..."
    (cd server && PHARMALIGN_ENV=simulation .venv/bin/python -m app.seed --reset --with-library)
  fi

  if alive server; then
    echo "Server already running."
  else
    port_busy "$SERVER_PORT" && { echo "Port $SERVER_PORT is in use by something else." >&2; exit 1; }
    echo "Server..."
    # shellcheck disable=SC2046
    (cd server && exec env PHARMALIGN_ENV=simulation \
        $( [ "$AGENTS_ON" = 1 ] && { echo PHARMALIGN_AGENT_MODE=http; agent_env; } ) \
        .venv/bin/uvicorn --factory app.main:create_app --host 127.0.0.1 --port "$SERVER_PORT" \
        >"$RUN_DIR/server.log" 2>&1) &
    echo $! >"$RUN_DIR/server.pid"
    wait_for server "http://127.0.0.1:$SERVER_PORT/health" 90 || { stop_one server; exit 1; }
  fi

  if [ "$AGENTS_ON" = 1 ]; then
    if curl -fs "http://127.0.0.1:$AGENT_PORT_BASE/health" >/dev/null 2>&1; then
      echo "Agents already running."
    else
      PHARMALIGN_RUN_DIR="$RUN_DIR/agents" scripts/run-local-agents.sh start || { do_stop; exit 1; }
    fi
  fi

  if [ "$WEB_ON" = 1 ]; then
    if alive web; then
      echo "Web already running."
    else
      port_busy "$WEB_PORT" && { echo "Port $WEB_PORT is in use by something else." >&2; exit 1; }
      [ -d web/node_modules ] || (cd web && npm install)
      echo "Web UI..."
      (cd web && exec ./node_modules/.bin/vite --host 127.0.0.1 >"$RUN_DIR/web.log" 2>&1) &
      echo $! >"$RUN_DIR/web.pid"
      wait_for web "http://127.0.0.1:$WEB_PORT/" 60 || { stop_one web; exit 1; }
    fi
  fi
  show_status
}

show_status() {
  local i name port
  echo
  printf '  %-20s %-8s %s\n' db "$(docker compose ps --status running --services 2>/dev/null | grep -qx db && echo up || echo down)" "localhost:5544"
  printf '  %-20s %-8s %s\n' server "$(curl -fs "http://127.0.0.1:$SERVER_PORT/health" >/dev/null 2>&1 && echo up || echo down)" "http://127.0.0.1:$SERVER_PORT"
  printf '  %-20s %-8s %s\n' web "$(curl -fs "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1 && echo up || echo down)" "http://127.0.0.1:$WEB_PORT"
  for i in "${!AGENTS[@]}"; do
    name=${AGENTS[$i]}; port=$((AGENT_PORT_BASE + i))
    printf '  %-20s %-8s %s\n' "$name" "$(curl -fs "http://127.0.0.1:$port/health" >/dev/null 2>&1 && echo up || echo down)" "http://127.0.0.1:$port"
  done
  echo
}

case "$cmd" in
  start) do_start ;;
  stop) do_stop ;;
  restart) do_stop; do_start ;;
  status) show_status ;;
  logs)
    target="${1:-server}"
    if [ -f "$RUN_DIR/$target.log" ]; then tail -f "$RUN_DIR/$target.log"; else tail -f "$RUN_DIR/agents/$target.log"; fi ;;
  *) sed -n 2,13p "$0"; exit 2 ;;
esac
