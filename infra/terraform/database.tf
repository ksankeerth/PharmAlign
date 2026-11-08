# Postgres 16 with the pgvector extension (created by infra/scripts/load-cloud-sql.sh).
#
# Public IP, with no authorised network: nothing can connect except through the Cloud SQL Auth Proxy (the Mac, with the
# developer's login) or Cloud Run's built-in connector (the server's service account). That needs no VPC, no private
# service connection and no NAT, which keeps it cheap and quick to set up. Every connection is encrypted and needs the
# database password as well as the IAM right.

resource "google_sql_database_instance" "main" {
  name             = "pharmalign-db"
  region           = var.region
  database_version = "POSTGRES_16"

  deletion_protection = var.db_deletion_protection

  settings {
    edition           = "ENTERPRISE"
    tier              = var.db_tier
    disk_type         = "PD_HDD"
    disk_size         = var.db_disk_gb
    disk_autoresize   = true
    availability_type = "ZONAL"

    # The switch: NEVER stops the instance, so only storage and the IP address are charged.
    activation_policy = var.awake ? "ALWAYS" : "NEVER"

    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "01:00"
      point_in_time_recovery_enabled = false
      backup_retention_settings {
        retained_backups = 1
      }
    }

    maintenance_window {
      day  = 7
      hour = 2
    }
  }

  depends_on = [google_project_service.api]
}

resource "google_sql_database" "app" {
  name     = "pharmalign"
  instance = google_sql_database_instance.main.name
}

# The database logins are made by infra/scripts/secrets.sh, so their passwords never enter the Terraform state: the app login
# "pharmalign", and the agents' read-only "pharmalign_agent", which the first migration creates (NOSUPERUSER and nothing more).
