# One private Cloud Run service per agent that is on, each from its own image.

locals {
  # Settings for one agent only, by its name.
  agent_env = {
    # Knowledge chats run the chat pipeline (language gate, classify, retrieve, answer, check): about 10 s a turn, against 40 s to
    # over a minute for the model-driven loop, which the server cuts off at 60 s. Case chats keep the loop.
    "knowledge-assistant" = {
      PHARMALIGN_KA_PIPELINE = "on"
      # Flash-lite labels and rewrites the question, flash answers. Both are served at the agents' model location (global).
      PHARMALIGN_MODEL_KA_CLASSIFIER = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
      PHARMALIGN_MODEL_KA_ANSWER     = "gemini-3.8-flash,gemini-3.5-flash-lite"
      # A turn answers or fails inside this many seconds, under the server's 60 s read limit.
      PHARMALIGN_KA_TURN_BUDGET    = "50"
      PHARMALIGN_KA_ANSWER_TIMEOUT = "25"
      # A model that failed is skipped this long. The default (60) would send a minute of turns to the smaller model after one slow call.
      PHARMALIGN_KA_COOLDOWN_SECONDS = "15"
    }
  }
}

module "agent" {
  source   = "./modules/agent_service"
  for_each = var.agents_on

  name            = each.value
  project_id      = var.project_id
  region          = var.region
  model_location  = var.model_location
  image           = "${local.registry}/${each.value}:${var.image_tag}"
  service_account = google_service_account.agents.email
  mcp_url         = "${local.server_url}/mcp"
  invoker_member  = "serviceAccount:${google_service_account.server.email}"
  max_instances   = var.agent_max_instances
  memory          = var.agent_memory
  telemetry       = var.agents_telemetry
  env             = lookup(local.agent_env, each.value, {})

  depends_on = [google_project_service.api, google_artifact_registry_repository.images]
}
