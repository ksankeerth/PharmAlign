# Two service accounts, each with only what it needs.

resource "google_service_account" "server" {
  account_id   = "pharmalign-server"
  display_name = "PharmAlign server"

  depends_on = [google_project_service.api]
}

resource "google_service_account" "agents" {
  account_id   = "pharmalign-agent"
  display_name = "PharmAlign agents"

  depends_on = [google_project_service.api]
}

# Server: the database, Vertex AI (embeddings). Its secrets and its right to call each agent are granted where those are made.
resource "google_project_iam_member" "server_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.server.email}"
}

resource "google_project_iam_member" "server_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.server.email}"
}

# Agents: Vertex AI (the Gemini model). Telemetry roles only when it is on.
resource "google_project_iam_member" "agents_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agents.email}"
}

resource "google_project_iam_member" "agents_telemetry" {
  for_each = var.agents_telemetry ? toset(["roles/cloudtrace.agent", "roles/logging.logWriter"]) : toset([])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.agents.email}"
}

# Cloud Build: builds the images and pushes them. infra/scripts/build.sh runs builds as this account.
resource "google_service_account" "build" {
  account_id   = "pharmalign-build"
  display_name = "PharmAlign image builds"

  depends_on = [google_project_service.api]
}

resource "google_project_iam_member" "build" {
  for_each = toset(["roles/artifactregistry.writer", "roles/logging.logWriter", "roles/storage.objectViewer"])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.build.email}"
}
