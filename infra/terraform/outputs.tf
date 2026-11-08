output "server_url" {
  description = "Open this in a browser."
  value       = google_cloud_run_v2_service.server.uri
}

output "agent_urls" {
  value = { for name, m in module.agent : name => m.uri }
}

output "registry" {
  description = "Where infra/scripts/build.sh pushes images."
  value       = local.registry
}

output "db_connection_name" {
  description = "For the Cloud SQL Auth Proxy."
  value       = google_sql_database_instance.main.connection_name
}

output "sql_instance" {
  value = google_sql_database_instance.main.name
}

output "build_service_account" {
  description = "Cloud Build runs builds as this account."
  value       = google_service_account.build.email
}

output "server_service_account" {
  value = google_service_account.server.email
}

output "agents_service_account" {
  value = google_service_account.agents.email
}

output "secret_ids" {
  description = "Secret Manager names (the load script reads the two passwords)."
  value       = keys(local.secrets)
}

output "expected_server_url_matches" {
  description = "The agents were given this address for the server. It must equal server_url once applied."
  value       = local.server_url == google_cloud_run_v2_service.server.uri
}
