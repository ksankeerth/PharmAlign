#!/usr/bin/env bash
# Puts the secret values in Secret Manager and makes the database logins, never in Terraform, its state, or the repository.
#
#     infra/scripts/secrets.sh          # every secret that has no value yet
#     infra/scripts/secrets.sh --all    # every secret again (new passwords; deploy.sh after it)
#
# It makes the database login "pharmalign" with a random password, and stores the connection strings. The read-only login
# pharmalign_agent is made by the first migration (infra/scripts/load-cloud-sql.sh), which then gives it the stored password.
# Old versions are destroyed. A running service reads a new value when it next starts.
source "$(dirname "$0")/common.sh"
need gcloud "https://cloud.google.com/sdk/docs/install"
need openssl "for random values"

PROJECT="$(tfvar project_id pharmalign-sk)"
INSTANCE="$(out sql_instance)"
CONN="$(out db_connection_name)"
DB=pharmalign

has_value() { [ -n "$(gcloud secrets versions list "$1" --project "$PROJECT" --filter=state=ENABLED --limit=1 --format='value(name)' 2>/dev/null)" ]; }

put() { # name value
  printf '%s' "$2" | gcloud secrets versions add "$1" --project "$PROJECT" --data-file=- --format='value(name)' >/dev/null
  local first=1 v
  for v in $(gcloud secrets versions list "$1" --project "$PROJECT" --filter='state!=DESTROYED' --sort-by=~createTime --format='value(name)'); do
    if [ $first = 1 ]; then first=0; continue; fi
    gcloud secrets versions destroy "$v" --secret "$1" --project "$PROJECT" --quiet >/dev/null
  done
  echo "  $1: set"
}

random() { openssl rand -hex 24; }

if [ "${1:-}" = "--all" ]; then FORCE=1; else FORCE=0; fi
want() { [ "$FORCE" = 1 ] || ! has_value "$1"; }

# The two database passwords and the two connection strings go together: a new password needs a new string.
if want pharmalign-db-password || want pharmalign-database-url; then
  pw="$(random)"
  if gcloud sql users list --instance "$INSTANCE" --project "$PROJECT" --format='value(name)' | grep -qx pharmalign; then
    gcloud sql users set-password pharmalign --instance "$INSTANCE" --project "$PROJECT" --password "$pw" >/dev/null
  else
    gcloud sql users create pharmalign --instance "$INSTANCE" --project "$PROJECT" --password "$pw" >/dev/null
  fi
  put pharmalign-db-password "$pw"
  put pharmalign-database-url "postgresql+asyncpg://pharmalign:${pw}@/${DB}?host=/cloudsql/${CONN}"
fi
if want pharmalign-agent-db-password || want pharmalign-agent-database-url; then
  apw="$(random)"
  put pharmalign-agent-db-password "$apw"
  put pharmalign-agent-database-url "postgresql+asyncpg://pharmalign_agent:${apw}@/${DB}?host=/cloudsql/${CONN}"
fi
want pharmalign-run-token-secret && put pharmalign-run-token-secret "$(random)$(random)"
want pharmalign-service-token && put pharmalign-service-token "$(random)$(random)"
echo "Done. If load-cloud-sql.sh has already run, run it again with --passwords-only to give pharmalign_agent the new password."
