# ---------- Stage 1: builder ----------
FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0 AS builder

WORKDIR /app

RUN python -m venv /app/venv

COPY requirements.lock .
RUN --mount=type=cache,target=/root/.cache/pip \
    /app/venv/bin/pip install -r requirements.lock && \
    /app/venv/bin/pip uninstall -y pip setuptools wheel 2>/dev/null || true

# ---------- Stage 2: runtime ----------
FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0

ARG APP_VERSION=dev
ARG BUILD_DATE=unknown
# APP_VERSION is read at runtime for version reporting; also used in OCI labels.
ENV APP_VERSION=${APP_VERSION}
LABEL org.opencontainers.image.source="https://github.com/arbisoft/erp-mcp" \
      org.opencontainers.image.description="ERP MCP Server for Arbisoft Workstream" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# NOTE: apt-get upgrade patches OS-level CVEs but makes builds non-reproducible.
# Accepted trade-off: security patches > perfect reproducibility.
# Periodically update the base image digest pin for a more deterministic approach.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

RUN groupadd -r --gid 1001 appuser && \
    useradd -r --uid 1001 --gid 1001 --no-log-init -s /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /app/venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# NOTE: Explicitly list top-level files; packages are copied as directories.
# Directories need 755 (execute for traversal); files inside get 644 from umask.
COPY --chown=root:root --chmod=644 server.py _auth.py _constants.py ./
COPY --chown=root:root --chmod=755 clients/ ./clients/
COPY --chown=root:root --chmod=755 tools/ ./tools/

ENV MCP_HOST=0.0.0.0

USER 1001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"MCP_PORT\",\"8100\")}/health', timeout=3)"

EXPOSE 8100
# NOTE: Python runs as PID 1. Use `docker run --init` or `init: true`
# in Compose to enable proper zombie process reaping if needed.
CMD ["python", "server.py"]
