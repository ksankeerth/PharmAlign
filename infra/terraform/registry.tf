resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "pharmalign"
  format        = "DOCKER"
  description   = "PharmAlign server and agent images"

  # Storage is charged by the gigabyte: keep the newest three versions of each image and delete older ones after a week.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "delete-older"
    action = "DELETE"
    condition {
      older_than = "604800s"
    }
  }

  depends_on = [google_project_service.api]
}

locals {
  registry = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
