variable "name" { type = string }
variable "project_id" { type = string }
variable "region" { type = string }
variable "model_location" { type = string }
variable "image" { type = string }
variable "service_account" { type = string }
variable "mcp_url" { type = string }
variable "invoker_member" { type = string }
variable "max_instances" { type = number }
variable "telemetry" { type = bool }

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "1Gi"
}

variable "env" {
  description = "Extra environment variables for this agent only, by name."
  type        = map(string)
  default     = {}
}
