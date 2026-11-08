terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
    }
  }
}

resource "google_cloud_run_v2_service" "agent" {
  name     = "pharmalign-${var.name}"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL" # reachable, but only with a Google ID token of an account that holds the invoker role below

  deletion_protection = false

  template {
    service_account = var.service_account
    timeout         = "300s"

    # A run goes on in the background between the server's polls, so CPU stays on while an instance is up.
    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }
    max_instance_request_concurrency = 8

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = false
        startup_cpu_boost = true
      }

      env {
        name  = "PHARMALIGN_MODEL_MODE"
        value = "vertex" # the scripted model is refused on Cloud Run
      }
      env {
        name  = "PHARMALIGN_MCP_URL"
        value = var.mcp_url
      }
      env {
        name  = "PHARMALIGN_OTEL_TO_CLOUD"
        value = var.telemetry ? "true" : "false"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.model_location
      }
      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      startup_probe {
        http_get {
          path = "/health"
        }
        period_seconds    = 5
        failure_threshold = 24
      }
    }
  }
}

# Only the server's service account may call it.
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.agent.name
  role     = "roles/run.invoker"
  member   = var.invoker_member
}
