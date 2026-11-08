# Shared by the scripts in this folder: paths, and reading terraform.tfvars and the outputs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF="$ROOT/infra/terraform"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Needs $1 ($2)." >&2; exit 1; }; }

# A string from terraform.tfvars (tfvar project_id), or the default given as the second argument.
tfvar() {
  local v=""
  [ -f "$TF/terraform.tfvars" ] && v="$(sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"\(.*\)\".*/\1/p" "$TF/terraform.tfvars" | head -1)"
  echo "${v:-${2:-}}"
}

out() { terraform -chdir="$TF" output -raw "$1"; }

confirm() {
  local answer
  read -r -p "$1 [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]]
}
