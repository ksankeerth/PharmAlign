variable "project_id" {
  description = "The Google Cloud project."
  type        = string
  default     = "pharmalign-sk"
}

variable "project_number" {
  description = "The project's number (gcloud projects describe PROJECT_ID --format='value(projectNumber)'). It gives Cloud Run services their fixed addresses, so the server and the agents can name each other without a cycle."
  type        = string
}

variable "region" {
  description = "Where Cloud Run, Cloud SQL and Artifact Registry run. me-central1 is Doha, the nearest to the UAE."
  type        = string
  default     = "me-central1"
}

variable "model_location" {
  description = "The Vertex AI location the agents' Gemini model is called in. The current model answers only at global."
  type        = string
  default     = "global"
}

variable "embedding_location" {
  description = "The Vertex AI location of the server's embedding calls."
  type        = string
  default     = "me-central1"
}

variable "image_tag" {
  description = "The tag of the images that infra/scripts/build.sh pushed. build.sh writes it to image.auto.tfvars."
  type        = string
  default     = ""
}

variable "agents_on" {
  description = "The agents that run as their own private Cloud Run service. Challenger, Sameness and Reliance Watch are off (see agents_off)."
  type        = set(string)
  default     = ["dossier-reader", "checklist-assessor", "coordinator", "knowledge-assistant", "gap-analyst"]
}

variable "agents_off" {
  description = "Agents switched off by configuration. A person does their work. Turn one on by removing it here, adding it to agents_on and giving it an image."
  type        = list(string)
  default     = ["challenger", "sameness", "reliance-watch"]
}

variable "brief_languages" {
  description = "The languages the Coordinator drafts questions to the company in. English only for now; empty means the pack's languages."
  type        = list(string)
  default     = ["en"]
}

variable "agent_max_model_calls" {
  description = "Most model calls one agent run may make. The first run on Gemini stopped at 12 (the Dossier Reader needs rounds of search, read and verify)."
  type        = number
  default     = 40
}

variable "agent_max_tool_calls" {
  description = "Most tool calls one agent run may make."
  type        = number
  default     = 80
}

variable "agent_run_deadline_seconds" {
  description = "Longest one agent run may take, in seconds. A large dossier can take the Dossier Reader past 300 on a slow day."
  type        = number
  default     = 600
}

variable "agent_poll_seconds" {
  description = "Seconds between the server's checks on a running agent run. The simulation default (1) suits stored answers, not a real model."
  type        = number
  default     = 5
}

variable "server_invokers" {
  description = "Who may open the server (it serves the web UI). allUsers means anyone with the link, which is the decision for this prototype."
  type        = list(string)
  default     = ["allUsers"]
}

variable "awake" {
  description = "The money switch. false stops the database (only its storage and IP address are then charged) and the site does not work until it is true again. Set it with infra/scripts/awake.sh."
  type        = bool
  default     = true
}

variable "server_cpu" {
  type    = string
  default = "1"
}

variable "server_memory" {
  description = "512Mi is the least Cloud Run allows with CPU always on. The server used about 160 MiB under a burst of searches."
  type        = string
  default     = "512Mi"
}

variable "server_min_instances" {
  description = "Server instances kept running. 0: it stops when idle and the first visit after a quiet spell waits for it to start (about a minute). Nothing is lost, because queued work is kept in the database. 1: no wait, at the cost of one always-on instance."
  type        = number
  default     = 0
}

variable "server_max_instances" {
  description = "One is enough for a prototype and keeps the database's connections low."
  type        = number
  default     = 1
}

variable "db_pool_max" {
  description = "Most database connections the server keeps open. A db-f1-micro instance allows about 25 in all."
  type        = number
  default     = 5
}

variable "agent_max_instances" {
  type    = number
  default = 1
}

variable "agent_memory" {
  description = "Each agent image used about 130 to 160 MiB idle. Raise it to 1Gi if a run is killed for memory."
  type        = string
  default     = "512Mi"
}

variable "billing_account_id" {
  description = "The billing account for the budget alert, such as XXXXXX-XXXXXX-XXXXXX (gcloud billing projects describe PROJECT_ID). Empty: no budget."
  type        = string
  default     = ""
}

variable "budget_amount" {
  description = "The monthly budget, in the billing account's currency. Alerts go to the billing account's admins at 50, 90 and 100 percent."
  type        = number
  default     = 20
}

variable "agents_telemetry" {
  description = "Export agent telemetry to Google Cloud. It needs the trace and log writer roles, so it is off by default."
  type        = bool
  default     = false
}

variable "db_tier" {
  description = "Cloud SQL machine type. db-f1-micro (shared core, 0.6 GB) is the cheapest. If searches are too slow, try db-g1-small (1.7 GB) or db-custom-1-3840 (3.75 GB, dedicated)."
  type        = string
  default     = "db-f1-micro"
}

variable "db_disk_gb" {
  type    = number
  default = 10
}

variable "db_deletion_protection" {
  description = "Refuse to delete the database instance. Set false before terraform destroy."
  type        = bool
  default     = true
}
