# checklist-assessor

Assesses one checklist check per run: a finding with quotes for an agent check, extracted values for a typed check. Built on Google ADK, with the shared runtime in `../common`. Read `AGENT.md` first.

```
checklist-assessor/
├── app/
│   ├── agent.py         # instruction, SPEC, root_agent, app
│   ├── rules.py         # first message and result rules
│   ├── assessor.py      # where a contract run meets the shared runner
│   └── fast_api_app.py  # ADK routes plus the contract routes
├── tests/               # fake model, fake MCP server: no network, no keys
├── samples/golden/      # 28 scenarios on a real model against fake tools like the server's (not part of pytest; see its README)
└── Dockerfile           # builds from the repository root
```

```bash
uv sync
uv run pytest -q
uv run uvicorn app.fast_api_app:app --port 8000 --reload
docker build -f agents/checklist-assessor/Dockerfile .   # from the repository root
```

On Cloud Run, who may call an agent is set by the platform (invoker permission), not by the agent.
