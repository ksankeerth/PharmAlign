# contracts

The interfaces between PharmAlign components. Strict Pydantic models are the single source, and one OpenAPI file per component is generated from them. `server/` and `agents/` both depend on this package and never on each other.

## Specs

| Spec | Component |
|---|---|
| `openapi/server.yaml` | The server API, used by the web UI, the connector and the scheduler |
| `openapi/agents/*.yaml` | One per agent: seven run agents and the Knowledge Assistant |
| `openapi/regulator.yaml` | What PharmAlign expects from a government system of record. The built-in mock serves it at `/mock` |

Generated files. Don't edit them by hand.

## Source

| Path | Holds |
|---|---|
| `pharmalign_contracts/common.py` | Shared fields, run response, errors, health |
| `pharmalign_contracts/agents/` | Each agent's request, result, tools and examples |
| `pharmalign_contracts/server/models.py` | Server request and response models, including the country pack format |
| `pharmalign_contracts/server/routes.py` | Every server operation |
| `pharmalign_contracts/regulator.py` | The regulator records API |
| `pharmalign_contracts/spec.py` | Turns routes and models into OpenAPI |
| `scripts/generate_openapi.py` | Writes the specs. `--check` fails if one is out of date |
| `tests/` | The contracts' rules, as tests |

## Commands

From this folder:

```
python scripts/generate_openapi.py
python scripts/generate_openapi.py --check
python -m pytest tests -q
```

To change a contract, edit the model, regenerate, then raise `CONTRACT_VERSION` if callers are affected.

All examples are synthetic.
