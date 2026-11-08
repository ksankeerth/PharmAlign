#!/usr/bin/env bash
# Puts the data into the Cloud SQL database from this Mac, through the Cloud SQL Auth Proxy. Nothing runs inside Cloud Run.
#
#   infra/scripts/load-cloud-sql.sh [--dump FILE] [--keep-bge] [--seed] [--passwords-only]
#
# Default: restore the dump given with --dump or PHARMALIGN_DUMP (a pg_dump of a local database with the UAE demo and the whole library, with Gemini vectors), run the migrations to head, and set the
# pharmalign_agent password from Secret Manager. The dump also holds bge-m3 vectors, which the cloud does not use, so they are
# dropped (--keep-bge keeps them).
# --seed: no dump. Run the migrations, then load the demo seed with the library, embedding with Gemini (slow: it parses every document).
#         scripts/embed_library.py (in server/) gives vectors to chunks that have none, if only some are missing.
#
# --passwords-only: change nothing but the pharmalign_agent password, to the one now in Secret Manager (after secrets.sh --all).
#
# Needs: terraform applied (base is enough) and infra/scripts/secrets.sh run; the database awake (infra/scripts/awake.sh on); gcloud logged in (gcloud auth login, and application-default login); cloud-sql-proxy
# (brew install cloud-sql-proxy); docker (psql and pg_restore run in the pgvector/pgvector:pg16 image, so no Postgres install);
# server/.venv (cd server && uv sync) for alembic. The database must be empty: it refuses to load into one that has tables.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$(pwd)

DUMP="${PHARMALIGN_DUMP:-}" KEEP_BGE=0 SEED=0 PASSWORDS_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dump) DUMP="$2"; shift ;;
    --keep-bge) KEEP_BGE=1 ;;
    --seed) SEED=1 ;;
    --passwords-only) PASSWORDS_ONLY=1 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

PROJECT_ID=${PROJECT_ID:-pharmalign-sk}
PORT=${PROXY_PORT:-5560}
DB=pharmalign USER=pharmalign
TF="terraform -chdir=infra/terraform"
PG_IMAGE=pgvector/pgvector:pg16

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1. $2" >&2; exit 1; }; }
need gcloud "Install the Google Cloud CLI."
need docker "Start Docker (Rancher Desktop)."
need cloud-sql-proxy "Install it: brew install cloud-sql-proxy"
[ -x server/.venv/bin/alembic ] || { echo "Missing server/.venv. Run: cd server && uv sync --extra gemini" >&2; exit 1; }
[ "$SEED" = 1 ] || [ "$PASSWORDS_ONLY" = 1 ] || [ -f "$DUMP" ] || { echo "No dump at ${DUMP:-(none)}: give one with --dump FILE or PHARMALIGN_DUMP" >&2; exit 1; }

CONN=$($TF output -raw db_connection_name)
secret() { gcloud secrets versions access latest --secret "$1" --project "$PROJECT_ID"; }
DB_PW=$(secret pharmalign-db-password)
AGENT_PW=$(secret pharmalign-agent-db-password)

echo "Starting the Cloud SQL Auth Proxy for $CONN on port $PORT"
# 0.0.0.0 so the docker containers can reach it as host.docker.internal. The database still needs its password and your Google login.
cloud-sql-proxy "$CONN" --address 0.0.0.0 --port "$PORT" >/tmp/cloud-sql-proxy.log 2>&1 &
PROXY=$!
trap 'kill $PROXY 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null && break
  sleep 1
done

psql_run() {  # reads SQL on stdin; -v pw=... is for ALTER/CREATE ROLE
  docker run --rm -i -e PGPASSWORD="$DB_PW" "$PG_IMAGE" psql -h host.docker.internal -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -q -At "$@"
}

if [ "$PASSWORDS_ONLY" = 1 ]; then
  psql_run -v pw="$AGENT_PW" <<'SQL'
ALTER ROLE pharmalign_agent PASSWORD :'pw';
SQL
  echo "The pharmalign_agent password is set."
  exit 0
fi

echo "Checking that the database is empty"
TABLES=$(echo "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'" | psql_run)
if [ "$TABLES" != 0 ]; then
  echo "The database already has $TABLES tables. This script loads into an empty database only." >&2
  echo "To start over, delete and recreate the database 'pharmalign' on the instance (gcloud sql databases delete/create), then run it again." >&2
  exit 1
fi

echo "Creating the vector extension and the pharmalign_agent login"
psql_run -v pw="$AGENT_PW" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pharmalign_agent') THEN
    CREATE ROLE pharmalign_agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END $$;
SQL

DBURL="postgresql+asyncpg://$USER:$DB_PW@127.0.0.1:$PORT/$DB"

if [ "$SEED" = 1 ]; then
  echo "Running the migrations"
  (cd server && PHARMALIGN_DATABASE_URL="$DBURL" .venv/bin/alembic upgrade head)
  echo "Loading the demo seed and the library (this takes a long time)"
  (cd server && PHARMALIGN_ENV=simulation PHARMALIGN_DATABASE_URL="$DBURL" PHARMALIGN_EMBEDDER=gemini-embedding-001 \
     GOOGLE_CLOUD_PROJECT="$PROJECT_ID" GOOGLE_CLOUD_LOCATION=me-central1 .venv/bin/python -I -m app.seed --with-library)
else
  echo "Restoring $DUMP (about two minutes; warnings about extensions are normal)"
  docker run --rm -i -e PGPASSWORD="$DB_PW" "$PG_IMAGE" pg_restore -h host.docker.internal -p "$PORT" -U "$USER" -d "$DB" --no-owner < "$DUMP" || echo "pg_restore reported problems. The checks below say whether the data is whole."
  echo "Running the migrations to head"
  (cd server && PHARMALIGN_DATABASE_URL="$DBURL" .venv/bin/alembic upgrade head)
fi

echo "Setting the pharmalign_agent password from Secret Manager"
psql_run -v pw="$AGENT_PW" <<'SQL'
ALTER ROLE pharmalign_agent PASSWORD :'pw';
SQL

if [ "$KEEP_BGE" = 0 ]; then
  echo "Dropping the bge-m3 vectors (the cloud server searches with Gemini only)"
  echo "DELETE FROM chunk_embedding WHERE model = 'bge-m3'" | psql_run
fi
echo "VACUUM ANALYZE" | psql_run

echo "What the database holds now:"
psql_run <<'SQL'
SELECT 'alembic ' || version_num FROM alembic_version;
SELECT 'workspaces ' || string_agg(id, ',' ORDER BY id) FROM workspace;
SELECT 'people ' || count(*) FROM person;
SELECT 'cases ' || count(*) FROM "case";
SELECT 'documents ' || count(*) FROM document;
SELECT 'chunks ' || count(*) FROM chunk;
SELECT 'vectors ' || model || ' ' || count(*) FROM chunk_embedding GROUP BY model;
SQL
echo "Done. Open: $($TF output -raw server_url)"
