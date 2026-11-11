# Two images from one file (#86):
#
#   docker build --target server -t hikidashi-server .   the web UI and API,
#       import, places, and the stages enrich and embed
#   docker build --target worker -t hikidashi-worker .   asr, ocr, tag, recipe
#   docker build --target worker --build-arg EXTRAS="--extra cuda" \
#       -t hikidashi-worker-cuda .                        the same, for a GPU
#
# Ollama, which tag and recipe call, is the upstream ollama/ollama image.
#
# Published to GHCR with every release (#77, .github/workflows/release.yml):
# ghcr.io/spanthetree/hikidashi-server, -worker (amd64 and arm64) and
# -worker-cuda (amd64).

# The front end is built here and only its static output is copied on, so the
# server image has no Node in it, and the worker image no front end at all.
# Static files are the same on every architecture, so it is built once, on
# the builder's own (an arm64 image is otherwise built under emulation).
FROM --platform=$BUILDPLATFORM node:22-slim AS web

WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# svelte.config.js writes the build to ../app/web, i.e. /src/app/web.
RUN npm run build


# What both images share, so its layers are built and cached once.
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/srv/.venv

WORKDIR /srv

COPY pyproject.toml uv.lock ./

ENV PATH="/srv/.venv/bin:$PATH"

# The database and media live on a mounted volume, never in the image.
ENV HIKIDASHI_DB=/data/hikidashi.sqlite3 \
    HIKIDASHI_MEDIA=/data/media \
    HIKIDASHI_IMPORTS=/data/imports \
    HIKIDASHI_MODELS=/data/models \
    HIKIDASHI_LOGS=/data/logs \
    HIKIDASHI_RUN=/data/run \
    HIKIDASHI_CACHE=/data/cache

# The images run as any user: root by default, or the one a NAS gives them
# with `--user` (Unraid's 99:100, TrueNAS's 568:568) (#87). Such a user has no
# home, and `/` is not theirs to write, so yt-dlp's cache and onnxruntime's
# scratch go to /tmp. Nothing kept there matters: what lasts is under /data.
ENV HOME=/tmp


FROM base AS server

# ffprobe, for enrich. yt-dlp is not here: it is pinned in pyproject.toml, as
# Instagram breaks the distro's copy often, and arrives with uv sync.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Dependencies resolve from the lockfile alone, so this layer is cached until
# pyproject.toml or uv.lock actually change.
RUN uv sync --frozen --no-dev --no-install-project --extra server

# Then the project itself, editable, which is what puts `hikidashi` on PATH.
COPY README.md ./
COPY app ./app
RUN uv sync --frozen --no-dev --extra server
COPY --from=web /src/app/web ./app/web

# The stages a `hikidashi worker` in this image runs: the server-worker
# container. Embed is here, not on workers, because search embeds queries
# with the same model; enrich writes the media this host serves.
ENV HIKIDASHI_LOCAL_STAGES=enrich,embed

EXPOSE 8080

# Single user, single writer: one worker is correct here, not a limitation.
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]


FROM base AS worker

# ffmpeg, for ASR's audio and OCR's frames; tesseract, for OCR.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

# --build-arg EXTRAS="--extra cuda" for a GPU host: the cuBLAS and cuDNN
# wheels (~2 GB) that the cuda profile needs.
ARG EXTRAS=""
RUN uv sync --frozen --no-dev --no-install-project --extra worker $EXTRAS

COPY README.md ./
COPY app ./app
RUN uv sync --frozen --no-dev --extra worker $EXTRAS

# The stages that need no database of their own: exactly what a remote worker
# may run (protocol.REMOTE). Tag and recipe call Ollama at OLLAMA_URL.
ENV HIKIDASHI_LOCAL_STAGES=asr,ocr,tag,recipe

CMD ["hikidashi", "worker"]
