#!/usr/bin/env bash
# Builds the server image and the five agent images with Cloud Build (no Docker needed here, and no arm-to-amd64 emulation),
# and records their tag.
#
#     infra/scripts/build.sh [TAG]
#
# The tag is TAG, or the date and time. It is written to infra/terraform/image.auto.tfvars, so the next deploy.sh rolls every
# service to these images. Cloud Build has a free monthly allowance of build minutes. The registry keeps the newest three versions.
source "$(dirname "$0")/common.sh"
need gcloud "https://cloud.google.com/sdk/docs/install"

PROJECT="$(tfvar project_id pharmalign-sk)"
REGION="$(tfvar region me-central1)"
REGISTRY="$(out registry)"
BUILDER="$(out build_service_account)"
TAG="${1:-$(date -u +%Y%m%d-%H%M)}"

echo "Building $TAG into $REGISTRY"
gcloud builds submit "$ROOT" --project "$PROJECT" --region "$REGION" --config "$ROOT/infra/cloudbuild.yaml" \
  --service-account "projects/$PROJECT/serviceAccounts/$BUILDER" \
  --substitutions "_REGISTRY=$REGISTRY,_TAG=$TAG"

echo "image_tag = \"$TAG\"" > "$TF/image.auto.tfvars"
echo
echo "Built. Next: infra/scripts/deploy.sh"
