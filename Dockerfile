# ---------- Stage 1: builder ----------
FROM python:3.14.3-slim-bookworm@sha256:ac8c3a801ac7c62f305cbc399a52e9e50077fd183ba57f1c052e6c70d0ed030e AS builder

WORKDIR /app

RUN python -m venv /app/venv

COPY requirements.lock .
RUN --mount=type=cache,target=/root/.cache/pip \
    /app/venv/bin/pip install -r requirements.lock && \
    /app/venv/bin/pip uninstall -y pip setuptools wheel 2>/dev/null || true

# ---------- Stage 2: runtime ----------
FROM python:3.14.3-slim-bookworm@sha256:ac8c3a801ac7c62f305cbc399a52e9e50077fd183ba57f1c052e6c70d0ed030e

ARG APP_VERSION=dev
ARG BUILD_DATE=unknown
# APP_VERSION is read at runtime for version reporting; also used in OCI labels.
ENV APP_VERSION=${APP_VERSION}
LABEL org.opencontainers.image.source="https://github.com/arbisoft/erp-mcp" \
      org.opencontainers.image.description="ERP MCP Server for Arbisoft Workstream" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}"

RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

RUN groupadd -r --gid 1001 appuser && \
    useradd -r --uid 1001 --gid 1001 --no-log-init -s /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /app/venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# NOTE: Explicitly list application files. Update this line when adding new modules.
COPY --chown=root:root --chmod=644 server.py erp_client.py ./

ENV MCP_HOST=0.0.0.0

USER 1001

# Port 8100 matches MCP_PORT default. If MCP_PORT is overridden at runtime,
# this health check will report unhealthy. For custom ports, override HEALTHCHECK.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health', timeout=3)"

EXPOSE 8100
CMD ["python", "server.py"]
