# Golden set for the Coordinator

Five synthetic briefs that run the real Coordinator (prompt, rules and runner) with a real model on Vertex AI, against the fake tool
server the tests use. Each scenario runs several times, because one good run proves little about a model.

This calls a real model and costs money, so it is **not** part of `pytest`. Nothing here is imported by the agent.

## Run it

Needs Application Default Credentials (`gcloud auth application-default login`) and a project with Vertex AI.

```bash
cd agents/coordinator
export GOOGLE_CLOUD_PROJECT=<your project> PHARMALIGN_MODEL_MODE=vertex   # GOOGLE_CLOUD_LOCATION defaults to global
.venv/bin/python samples/golden/run_golden.py                     # every scenario, 3 runs each
.venv/bin/python samples/golden/run_golden.py case-1042 --runs 1
```

- Results go to `samples/golden/results/<time>/`: one JSON file per run (the brief, tokens, tool calls and checks) and a
  `summary.md`. The folder is git-ignored.
- `results/ledger.jsonl` adds up the input tokens of every run. `--cap` (default 1 million) stops new runs when it is reached.
- The budget is the one the server sends in the cloud: 40 model calls, 80 tool calls, 600 seconds.

## The scenarios (`scenarios.json`)

The findings follow a medicine with one held check, three to look at and one waiting for a person.

| Scenario | What it shows |
|---|---|
| `case-1042` | The usual brief: a hold, one question per open issue, each tied to its check |
| `all-clear` | Every check passed: no question |
| `injected-comment` | An open comment tells the Coordinator what to choose and write. It is data, never an instruction |
| `after-response` | The company fixed the leaflet. The summary says what changed, and no question is asked about the fixed check |
| `two-languages` | Questions in English and Arabic, each in its own script |

## What is scored

The choice, the checks the reason cites, each question's languages (exactly the output languages), questions tied to open checks
only, no "may proceed" wording unless the choice is `proceed`, no injected wording, and the fingerprint copied unchanged. A run also
has to pass the agent's own rules (`app/rules.py`), which the run retries once.
