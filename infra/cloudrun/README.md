# Cloud Run: the agent services

One Cloud Run service per agent, from `services/<agent>.yaml`. Nothing here is deployed by this repository, and no file holds a secret.

What every service has:
- Scale to zero (`minScale` 0), at most 2 to 5 instances, 1 CPU and 1 GiB.
- CPU stays on while an instance is up (`cpu-throttling: "false"`), because a run goes on in the background between the server's polls. Runs are held in memory, so a restart makes a poll return 404, which the server treats as a lost run and starts again.
- Model mode `vertex` (Gemini on Vertex AI, through the service account), telemetry export on, `PHARMALIGN_MCP_URL` set to the server's `/mcp`. The scripted model is refused on Cloud Run.
- Who may call it is set by the platform: the invoker policy in `invoker-policy.yaml` lists only the server's service account. The services are not public.

## Fill in

| Name | Meaning |
|---|---|
| `PROJECT_ID` | Google Cloud project |
| `REGION` | Cloud Run and Artifact Registry region, such as `europe-west1` |
| `SERVER_URL` | The server's public URL (no trailing slash). Agents read through `SERVER_URL/mcp` with the run's token |
| `SERVER_SERVICE_ACCOUNT` | The service account the server runs as. It gets `roles/run.invoker` on each agent |
| `TAG` | The image tag, such as a short commit id |

The agents run as `pharmalign-agent@PROJECT_ID.iam.gserviceaccount.com`, which needs `roles/aiplatform.user`. Create both accounts and the Artifact Registry repository `pharmalign` first.

## Build, push, deploy

Run from the repository root.

```sh
export PROJECT_ID=... REGION=... SERVER_URL=https://... SERVER_SERVICE_ACCOUNT=... TAG=$(date +%Y%m%d-%H%M)
AGENTS="dossier-reader checklist-assessor sameness challenger coordinator reliance-watch gap-analyst knowledge-assistant"

for a in $AGENTS; do
  image=$REGION-docker.pkg.dev/$PROJECT_ID/pharmalign/$a:$TAG
  docker build -f agents/$a/Dockerfile -t $image .
  docker push $image
done

for a in $AGENTS; do
  envsubst < infra/cloudrun/services/$a.yaml | gcloud run services replace - --project $PROJECT_ID --region $REGION
  envsubst < infra/cloudrun/invoker-policy.yaml > /tmp/invoker-policy.yaml
  gcloud run services set-iam-policy pharmalign-$a /tmp/invoker-policy.yaml --project $PROJECT_ID --region $REGION --quiet
done
```

Then give the server each agent's URL (`gcloud run services describe pharmalign-<agent> --format 'value(status.url)'`) as `PHARMALIGN_AGENT_URL_<AGENT>`, with `PHARMALIGN_AGENT_MODE=http`.

## Before it works end to end

- The server sends `PHARMALIGN_AGENT_TOKEN` as a static bearer, which Cloud Run does not accept. Deploying needs a `token_provider` for `HttpAgentClient` that returns a Google ID token with the agent's URL as audience (the provider receives that URL).
- The server's `/mcp` must be reachable from the agents. It checks the run token itself.
