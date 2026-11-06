# Build from the repo root: docker build -f server/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN useradd --system --create-home pharmalign

# The server needs ../contracts next to it, as in the repo.
WORKDIR /app/server
COPY contracts /app/contracts
COPY server/pyproject.toml server/uv.lock ./
# EXTRAS=bge-m3 installs the embedding model and downloads it now, so the container never fetches it.
ARG EXTRAS=""
RUN uv sync --frozen --no-dev $(for e in $EXTRAS; do printf ' --extra %s' "$e"; done)
ENV HF_HOME=/opt/hf
RUN case " $EXTRAS " in *" bge-m3 "*) python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')" ;; esac

COPY server/alembic.ini ./
COPY server/migrations ./migrations
COPY server/app ./app
COPY demo /app/demo

RUN chmod -R a+rX /opt/hf 2>/dev/null || true
USER pharmalign
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000"]
