# challenger

Tries to disprove one finding by reading its sources again. Disputes only with a verified contradicting quote. Built on Google ADK, with the shared runtime in `../common`. Read `AGENT.md` first.

```
challenger/
├── app/
│   ├── agent.py         # instruction, SPEC, root_agent, app
│   ├── rules.py         # first message and result rules
│   ├── challenge.py     # where a contract run meets the shared runner
│   └── fast_api_app.py  # ADK routes plus the contract routes
├── tests/               # fake model, fake MCP server: no network, no keys
└── Dockerfile           # builds from the repository root
```

```bash
uv sync
uv run pytest -q
uv run uvicorn app.fast_api_app:app --port 8000 --reload
docker build -f agents/challenger/Dockerfile .   # from the repository root
```

On Cloud Run, who may call an agent is set by the platform (invoker permission), not by the agent.
