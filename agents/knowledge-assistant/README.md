# knowledge-assistant

A chat agent: answers from the knowledge base with citations, and on a case also reads that case. Sessions and streamed turns over `/sessions`. Built on Google ADK, with the shared runtime in `../common`. Read `AGENT.md` first.

```
knowledge-assistant/
├── app/
│   ├── agent.py         # instruction, SPEC, root_agent, app
│   ├── answer.py        # what the model answers each turn
│   ├── rules.py         # first message and answer rules
│   └── fast_api_app.py  # ADK routes plus the session routes
├── tests/               # fake model, fake MCP server: no network, no keys
└── Dockerfile           # builds from the repository root
```

```bash
uv sync
uv run pytest -q
uv run uvicorn app.fast_api_app:app --port 8000 --reload
docker build -f agents/knowledge-assistant/Dockerfile .   # from the repository root
```

On Cloud Run, who may call an agent is set by the platform (invoker permission), not by the agent.
