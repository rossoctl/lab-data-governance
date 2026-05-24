# Data-governance container image (issue #38).
#
# One Containerfile, one image, two tags. The receiver Deployment
# (deploy/k8s/30-receiver.yaml) and the UI Deployment
# (deploy/k8s/40-ui.yaml) both run from this image; they differ only in the
# k8s `command:` they set. The image therefore ships the full
# `data_governance` package and must be capable of running:
#
#   python -m data_governance.processors.otlp_receiver   (receiver)
#   python -m data_governance.api                        (UI backend)
#   python -m data_governance.db.migrate                 (init container)
#   python -m data_governance.db.schema_version          (startup check, #10)
#
# The build is uv-native: deps are installed from `pyproject.toml + uv.lock`
# via `uv sync --frozen` so the runtime environment matches the lockfile
# exactly (no re-resolution at build time). The final stage copies the
# resulting `.venv` and the source tree, including `alembic.ini` and the
# migrations directory required by the migrate CLI and the schema-version
# startup check (PROJECT.md §3 / ADR-0002).

# -----------------------------------------------------------------------------
# Stage 1: builder — resolve and install deps with uv
# -----------------------------------------------------------------------------
# `uv` ships preinstalled in this image; no separate uv bootstrap is needed.
# The image is pinned to a specific uv version + a slim Python base so build
# determinism survives upstream changes to the rolling tag.
FROM ghcr.io/astral-sh/uv:0.5.11-python3.11-bookworm-slim AS builder

# Keep uv from chattering and re-using a host cache mount we don't have here.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# First, install only the dependencies. Splitting the deps install from the
# project install keeps the heavy step cacheable across source-only edits:
# changing `data_governance/*.py` does not invalidate the deps layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Now copy the rest of the source and install the project itself into the
# environment. The migrations tree (`data_governance/db/migrations/`) and
# `alembic.ini` come along for the ride.
COPY alembic.ini ./
COPY data_governance ./data_governance
COPY README.md ./
RUN uv sync --frozen --no-dev

# -----------------------------------------------------------------------------
# Stage 2: runtime — slim Python image with the prebuilt venv copied in
# -----------------------------------------------------------------------------
# Match the builder's Python minor version so the venv's compiled bytecode
# and any C-extension shared objects load cleanly.
FROM python:3.11-slim-bookworm AS runtime

# Run as a non-root user. The k8s manifests do not set a securityContext
# explicitly; baking a non-root default in the image keeps "least surprise"
# for any future cluster that defaults to PSA-restricted.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home --home-dir /home/app app

WORKDIR /app

# Pull the venv built in stage 1 — no network or compiler in the runtime
# stage, no apt packages beyond what python:3.11-slim ships.
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Copy the source tree, alembic config, and migrations needed at runtime.
# - data_governance/                  — source + migrations tree
# - alembic.ini                       — read by the migrate CLI and the
#                                       schema-version startup check
COPY --from=builder --chown=app:app /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=app:app /app/data_governance /app/data_governance

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER app

# No ENTRYPOINT — the k8s manifests set `command:` per Deployment to pick the
# entry point (receiver, UI, or migrate). A default CMD is provided so the
# image is still runnable standalone for ad-hoc inspection
# (`docker run data-governance/receiver:latest python -m data_governance.db.schema_version`).
CMD ["python", "-c", "import data_governance; print('data_governance image; pick a command via -m')"]
