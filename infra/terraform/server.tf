# The server: the API, the agent tool server at /mcp, the background worker and the web UI, in one container.

locals {
  # A Cloud Run service has a fixed address built from its name, the project number and the region. Naming it here
  # lets the agents point at the server while the server points at the agents, with no cycle.
  server_url = "https://pharmalign-server-${var.project_number}.${var.region}.run.app"

  agent_url_env = {
    for name, m in module.agent : "PHARMALIGN_AGENT_URL_${upper(replace(name, "-", "_"))}" => m.uri
  }
}

resource "google_cloud_run_v2_service" "server" {
  name     = "pharmalign-server"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    service_account = google_service_account.server.email
    timeout         = "300s"

    # Queued work is kept in the database, so an instance that stops when idle loses nothing: the worker picks it up when one starts.
    scaling {
      min_instance_count = var.server_min_instances
      max_instance_count = var.server_max_instances
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [local.connection_name]
      }
    }

    containers {
      image = "${local.registry}/server:${var.image_tag}"
      # The image's own command runs the migrations first. They are run from the Mac instead (infra/scripts/load-cloud-sql.sh).
      command = ["uvicorn"]
      args    = ["--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = var.server_cpu
          memory = var.server_memory
        }
        cpu_idle          = false # the in-process worker needs CPU between requests; billing runs only while an instance is up
        startup_cpu_boost = true
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # The only identity provider is the demo one, which needs simulation. Agents are called for real (http), not replayed.
      env {
        name  = "PHARMALIGN_ENV"
        value = "simulation"
      }
      env {
        name  = "PHARMALIGN_AGENT_MODE"
        value = "http"
      }
      env {
        name  = "PHARMALIGN_AGENT_AUTH"
        value = "google_id_token"
      }
      env {
        name  = "PHARMALIGN_AGENTS_OFF"
        value = join(",", var.agents_off)
      }
      # The Coordinator drafts questions to the company in these languages only. The pack's languages are unchanged.
      env {
        name  = "PHARMALIGN_BRIEF_LANGUAGES"
        value = join(",", var.brief_languages)
      }
      # What one agent run may spend. A real model needs more rounds of search, read and verify than the scripted one.
      env {
        name  = "PHARMALIGN_AGENT_MAX_MODEL_CALLS"
        value = tostring(var.agent_max_model_calls)
      }
      env {
        name  = "PHARMALIGN_AGENT_MAX_TOOL_CALLS"
        value = tostring(var.agent_max_tool_calls)
      }
      env {
        name  = "PHARMALIGN_AGENT_RUN_DEADLINE_SECONDS"
        value = tostring(var.agent_run_deadline_seconds)
      }
      # How often the server asks a running agent whether it has finished. A real run takes from 20 seconds to a few minutes.
      env {
        name  = "PHARMALIGN_AGENT_POLL_SECONDS"
        value = tostring(var.agent_poll_seconds)
      }
      env {
        name  = "PHARMALIGN_EMBEDDER"
        value = "gemini-embedding-001"
      }
      env {
        name  = "PHARMALIGN_RERANKER"
        value = "off"
      }
      env {
        name  = "PHARMALIGN_DB_POOL_MAX"
        value = tostring(var.db_pool_max)
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.embedding_location
      }

      dynamic "env" {
        for_each = local.agent_url_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = {
          PHARMALIGN_DATABASE_URL       = "pharmalign-database-url"
          PHARMALIGN_AGENT_DATABASE_URL = "pharmalign-agent-database-url"
          PHARMALIGN_RUN_TOKEN_SECRET   = "pharmalign-run-token-secret"
          PHARMALIGN_SERVICE_TOKEN      = "pharmalign-service-token"
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.s[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        http_get {
          path = "/health"
        }
        period_seconds    = 5
        failure_threshold = 36
      }
      liveness_probe {
        http_get {
          path = "/health"
        }
        period_seconds = 30
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.image_tag != ""
      error_message = "No image_tag yet: run infra/scripts/build.sh first (it writes image.auto.tfvars)."
    }
  }

  depends_on = [
    google_project_service.api,
    google_artifact_registry_repository.images,
    google_secret_manager_secret_iam_member.server_reads,
    google_project_iam_member.server_cloudsql,
  ]
}

# Who may open the server. Open to anyone with the link, by decision, for this prototype.
resource "google_cloud_run_v2_service_iam_member" "server_invoker" {
  for_each = toset(var.server_invokers)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.server.name
  role     = "roles/run.invoker"
  member   = each.value
}
