#!/usr/bin/env bash
# Plans the change, shows it, and applies it only when you say yes.
#
#     infra/scripts/deploy.sh base   # first time: APIs, registry, accounts, empty secrets, the database (no services yet)
#     infra/scripts/deploy.sh        # everything (needs the images from build.sh and the secrets from secrets.sh)
source "$(dirname "$0")/common.sh"
need terraform "https://developer.hashicorp.com/terraform/install"
[ -f "$TF/terraform.tfvars" ] || { echo "Copy infra/terraform/terraform.tfvars.example to terraform.tfvars and fill it in." >&2; exit 1; }
[ -d "$TF/.terraform" ] || terraform -chdir="$TF" init -input=false

TARGETS=()
if [ "${1:-}" = "base" ]; then
  # what the services need first: secrets.sh fills the secrets, build.sh pushes the images
  for r in google_project_service.api google_artifact_registry_repository.images google_secret_manager_secret.s \
           google_secret_manager_secret_iam_member.server_reads google_sql_database_instance.main google_sql_database.app \
           google_service_account.server google_service_account.agents google_service_account.build \
           google_project_iam_member.server_cloudsql google_project_iam_member.server_vertex google_project_iam_member.agents_vertex \
           google_project_iam_member.build google_billing_budget.budget; do
    TARGETS+=("-target=$r")
  done
  echo "Base only (Terraform warns that -target is for exceptional use: this is one)."
fi

PLAN="$TF/tfplan"
terraform -chdir="$TF" plan -input=false -out="$PLAN" ${TARGETS[@]+"${TARGETS[@]}"}
echo
if confirm "Apply this plan?"; then
  terraform -chdir="$TF" apply -input=false "$PLAN"
  rm -f "$PLAN"
  echo
  terraform -chdir="$TF" output
else
  rm -f "$PLAN"
  echo "Nothing changed."
fi
