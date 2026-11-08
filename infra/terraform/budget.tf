# A cost alert. The billing account's admins get an email at half, 90 percent and all of the monthly amount.
# It does not stop anything: use infra/scripts/awake.sh off for that.

resource "google_billing_budget" "budget" {
  count = var.billing_account_id == "" ? 0 : 1

  billing_account = var.billing_account_id
  display_name    = "PharmAlign (${var.project_id})"

  budget_filter {
    projects = ["projects/${var.project_number}"]
  }

  amount {
    specified_amount {
      units = tostring(var.budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.api]
}
