locals {
  apis = [
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "aiplatform.googleapis.com",
    "iam.googleapis.com",
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com", # Terraform reads the project's services through it
    "cloudbuild.googleapis.com",
    "billingbudgets.googleapis.com",
  ]
}

resource "google_project_service" "api" {
  for_each = toset(local.apis)

  service            = each.value
  disable_on_destroy = false # other things in the project may use them
}
