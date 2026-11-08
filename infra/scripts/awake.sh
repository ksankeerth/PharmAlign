#!/usr/bin/env bash
# The money switch.
#
#     infra/scripts/awake.sh off   # stop the database (the site does not work until you turn it on again)
#     infra/scripts/awake.sh on    # start it again (it takes a few minutes)
#     infra/scripts/awake.sh       # say which it is
#
# Writes infra/terraform/awake.auto.tfvars, then deploy.sh plans the change and asks before applying it.
# While it is off the site does not work: the web page may open, but every screen fails because the server cannot reach its database.
# Storage and the database's IP address are still charged while it is off. Nothing is lost, and everything is back when it is on.
source "$(dirname "$0")/common.sh"

FILE="$TF/awake.auto.tfvars"
case "${1:-}" in
  on)  echo "awake = true" > "$FILE" ;;
  off) echo "awake = false" > "$FILE" ;;
  "")  if [ -f "$FILE" ]; then cat "$FILE"; else echo "awake = true (the default)"; fi; exit 0 ;;
  *)   echo "infra/scripts/awake.sh on|off" >&2; exit 1 ;;
esac
"$(dirname "$0")/deploy.sh"
