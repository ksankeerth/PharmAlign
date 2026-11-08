# Build from the repo root: docker build -f server/Dockerfile .
# One image holds the server and the built web UI, which the server serves from the same address.
#   --build-arg EXTRAS=gemini      Vertex AI embeddings and Google ID tokens (what the cloud image uses)
#   --build-arg EXTRAS=bge-m3      the local embedding model, downloaded now
FROM node:24-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
# 0: open pages don't ask the server again by themselves (people press Refresh), so the server and database can go idle.
ARG VITE_AUTO_REFRESH_MS=0
ENV VITE_AUTO_REFRESH_MS=$VITE_AUTO_REFRESH_MS
RUN npm run build

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
COPY --from=web /web/dist /app/web
ENV PHARMALIGN_WEB_DIR=/app/web

RUN chmod -R a+rX /opt/hf 2>/dev/null || true
USER pharmalign
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000"]
