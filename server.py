"""ERP MCP Server — FastMCP v3 with Google OAuth.

Exposes ERP tools via the Model Context Protocol. Authentication flows
through Google OAuth (FastMCP GoogleProvider); the raw Google access
token is exchanged for an ERP session token on every tool call.
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from _auth import ALLOWED_DOMAIN
from clients import ERPClientRegistry, set_registry
from clients._base import BaseERPClient
from tools import load_domains

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("erp_mcp.server")

# Configure logging; LOG_LEVEL env var overrides the default INFO level.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ERP_BASE_URL: str = os.environ.get(
    "ERP_API_BASE_URL", "https://erp.arbisoft.com/api/v1/"
)

try:
    _APP_VERSION: str = importlib.metadata.version("erp-mcp")
except importlib.metadata.PackageNotFoundError:
    _APP_VERSION = os.environ.get("APP_VERSION", "dev")

# ---------------------------------------------------------------------------
# Auth provider + FastMCP instance
# ---------------------------------------------------------------------------

_google_client_id: str = os.environ.get("GOOGLE_CLIENT_ID", "")
_google_client_secret: str = os.environ.get("GOOGLE_CLIENT_SECRET", "")

auth = GoogleProvider(
    client_id=_google_client_id,
    client_secret=_google_client_secret,
    base_url=os.environ.get("MCP_BASE_URL", "https://erp.arbisoft.com"),
    redirect_path="/callback",
    required_scopes=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ],
)


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Manage application resources during server lifecycle."""
    if not _google_client_id or not _google_client_secret:
        logger.critical(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set — refusing to start"
        )
        raise SystemExit(1)
    base = BaseERPClient(base_url=ERP_BASE_URL, allowed_domain=ALLOWED_DOMAIN)
    registry = ERPClientRegistry(base=base)
    set_registry(registry)
    logger.info("ERP MCP server starting up")
    try:
        yield
    finally:
        logger.info("ERP MCP server shutting down")
        await registry.close()
        set_registry(None)


mcp = FastMCP(name="erp-mcp", auth=auth, lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Security headers middleware (defense-in-depth for HTTP responses)
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject hardening headers into every HTTP response."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


# Starlette Middleware descriptor passed to mcp.run()/mcp.http_app() at startup.
_security_middleware = Middleware(SecurityHeadersMiddleware)


# ---------------------------------------------------------------------------
# Health check endpoint (used by Docker HEALTHCHECK)
# ---------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Return 200 OK for Docker health checks and load balancers."""
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Load domain tools
# ---------------------------------------------------------------------------

_loaded_domains = load_domains(mcp)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not ALLOWED_DOMAIN:
        logger.critical("ALLOWED_DOMAIN must not be empty")
        raise SystemExit(1)
    if not _google_client_id or not _google_client_secret:
        raise SystemExit(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables are required."
        )
    mcp.run(
        transport="http",
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8100")),
        stateless_http=True,
        middleware=[_security_middleware],
    )
