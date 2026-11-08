# infra

Infrastructure as code. The prototype runs with Docker Compose on one machine, and can run on Google Cloud at a very low cost.

- `terraform/`: the Google Cloud setup. The server and five agents each run as a Cloud Run service. A Postgres 16 database runs on Cloud SQL, with its secrets in Secret Manager and the images in Artifact Registry.
- `scripts/`: `deploy.sh` (plan, then apply only after you say yes), `secrets.sh`, `build.sh` (Cloud Build), `awake.sh` (the money switch) and `load-cloud-sql.sh`.
- `cloudbuild.yaml`: builds the six images in parallel. `.gcloudignore` (in the repository root) keeps the upload small.
- `cloudrun/`: the same agent services as plain Cloud Run service files. Terraform replaces them; they are kept as a reference and are not deployed from here.

## What Terraform makes

| Part | Detail |
|---|---|
| Region | `me-central1` (Doha), set by `var.region`. The agents' model is called at `global`, because the current Gemini model answers nowhere else. Embeddings are called in `me-central1` |
| Server | `pharmalign-server`: the API, `/mcp`, the background worker and the web UI in one container. 1 vCPU, 512 MiB, at most 1 instance, and 0 at least (`var.server_min_instances`). CPU stays on while an instance is up, because the worker runs between requests. Open to anyone with the link (`var.server_invokers`) |
| Agents | One private service per agent that is on: dossier-reader, checklist-assessor, coordinator, knowledge-assistant, gap-analyst. 0 to 1 instance, 1 vCPU, 512 MiB, startup boost. Only the server's service account may call them, with a Google ID token |
| Agents off | Challenger, Sameness and Reliance Watch, through `PHARMALIGN_AGENTS_OFF`. A person does their work |
| Database | Cloud SQL Postgres 16, `db-f1-micro`, 10 GB HDD, zonal, one backup, no point-in-time recovery. Public IP with no authorised network: only the Auth Proxy and Cloud Run's connector can connect, and each needs the password as well as the IAM right. The server keeps at most 5 connections (`var.db_pool_max`), as the instance allows about 25 |
| Secrets | Empty containers made by Terraform. `scripts/secrets.sh` makes the database login and the values, so no password is in the Terraform state |
| Service accounts | Server: Cloud SQL client, Vertex AI user, its four secrets, invoker on each agent. Agents: Vertex AI user. Build: registry writer, log writer, source reader |
| Registry | Keeps the newest 3 versions of each image and deletes older ones after a week |
| Budget | An alert at 50, 90 and 100 percent of `var.budget_amount` (default 20, in the billing currency), when `billing_account_id` is set. It warns; it does not stop anything |

## Order

Run from the repository root. Terraform's first apply cannot start a service whose image is not pushed or whose secret has no value, so the first deploy has two steps.

```sh
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars   # project, project number, billing account
infra/scripts/deploy.sh base        # APIs, registry, accounts, empty secrets, the database
infra/scripts/secrets.sh            # the database login and the secret values
infra/scripts/build.sh              # the six images, with Cloud Build
infra/scripts/deploy.sh             # the services
infra/scripts/load-cloud-sql.sh     # restores the dump through the Auth Proxy, migrates, sets the agent password
```

`deploy.sh` shows the plan and asks before it applies. A new build later: `build.sh`, then `deploy.sh`.

## The money switch

```sh
infra/scripts/awake.sh off     # stop the database
infra/scripts/awake.sh on      # start it again (a few minutes)
infra/scripts/awake.sh         # say which
```

**While it is off the site does not work.** The server cannot reach its database, so every screen fails (the page may open, but it shows errors). Nothing is lost. Only the database's storage, backup and IP address are charged. The Cloud Run services cost nothing when nobody uses them.

When it is on, the server stops after about 15 minutes without visitors, so nothing runs and nothing is charged for compute. The first visit after a quiet spell waits about a minute for it to start. Queued work is kept in the database and resumes when the server starts again; reminders and agent polling simply wait. An agent run in progress when its agent stops is started again.

## Rough monthly cost (me-central1)

All figures are list-price estimates from memory, not read from Google's billing catalogue (that API is not enabled). me-central1 is a more expensive price tier than us-central1, so these are rounded up. Check the Google Cloud pricing calculator before relying on them.

| Line | (a) awake, idle | (b) awake, a few hours of use a day | (c) asleep |
|---|---|---|---|
| Cloud SQL `db-f1-micro` compute | about $10 to 14 | the same | $0 |
| Database storage, 10 GB HDD, and one backup | about $1.5 | the same | about $1.5 |
| Database public IP address | about $4 | about $4 | about $7 (idle rate) |
| Server (1 vCPU, 512 MiB, billed only while an instance is up) | $0 | about $8 (3 hours a day, at about $0.09 an hour) | $0 |
| Five agents (scale to zero, billed while up) | $0 | about $3 to 10 | $0 |
| Artifact Registry (about 10 GB of images, $0.10 a GB after 0.5 GB free) | about $1 | about $1 | about $1 |
| Secret Manager, Cloud Build, budget | $0 (free allowances) | $0 | $0 |
| Gemini model calls (Vertex AI) | $0 | about $1 to 5, depends on use | $0 |
| **Total** | **about $17 to 22** | **about $30 to 50** | **about $9 to 10** |

The default budget alert ($20) fires in case (a) at 90 to 100 percent. Raise `budget_amount` if that is not what you want.

## Open access

The server has no login. The only sign-in is the demo one, where anyone can act as any demo person, so a link holder can view and change everything. The first screen says so. Data can be reset at any time. Do not put anything real in it. Restrict `var.server_invokers` when a login is added.
