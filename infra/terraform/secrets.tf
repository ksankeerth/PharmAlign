# Secret Manager holds the secrets; Terraform makes only the empty containers. infra/scripts/secrets.sh makes the passwords and
# tokens and stores them, so no secret value is ever in Terraform or its state file.

locals {
  connection_name = google_sql_database_instance.main.connection_name

  # name -> what it holds
  secrets = {
    "pharmalign-db-password"        = "the app login's database password (used by infra/scripts/load-cloud-sql.sh)"
    "pharmalign-agent-db-password"  = "the pharmalign_agent login's database password"
    "pharmalign-database-url"       = "the server's connection string"
    "pharmalign-agent-database-url" = "the agent tool server's connection string (a read-only login)"
    "pharmalign-run-token-secret"   = "signs the run token that scopes an agent's tools"
    "pharmalign-service-token"      = "the bearer token of non-person callers"
  }

  # The server reads these four. The two passwords are for the load script, which runs as a person.
  server_secrets = ["pharmalign-database-url", "pharmalign-agent-database-url", "pharmalign-run-token-secret", "pharmalign-service-token"]
}

resource "google_secret_manager_secret" "s" {
  for_each = local.secrets

  secret_id   = each.key
  labels      = { app = "pharmalign" }
  annotations = { purpose = each.value }

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.api]
}

resource "google_secret_manager_secret_iam_member" "server_reads" {
  for_each = toset(local.server_secrets)

  secret_id = google_secret_manager_secret.s[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.server.email}"
}
