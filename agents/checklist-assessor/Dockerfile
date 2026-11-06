# Build with the repository root as the context, so contracts/ and agents/common are included:
#   docker build -f agents/checklist-assessor/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.13 /uv /uvx /bin/

WORKDIR /repo

COPY contracts ./contracts
COPY agents/common ./agents/common
COPY agents/checklist-assessor ./agents/checklist-assessor

WORKDIR /repo/agents/checklist-assessor

RUN uv sync --frozen --no-dev

ARG AGENT_VERSION=0.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

EXPOSE 8080

CMD ["uv", "run", "--no-dev", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
