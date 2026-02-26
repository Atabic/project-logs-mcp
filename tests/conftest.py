"""Pytest configuration for ERP MCP server tests.

Sets required environment variables before any test module imports server.py,
which reads GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET at module level.
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-google-client-secret")
os.environ.setdefault("MCP_BASE_URL", "http://localhost:8100")
os.environ.setdefault("ERP_API_BASE_URL", "http://127.0.0.1:8000/api/v1")
os.environ.setdefault("ALLOWED_DOMAIN", "arbisoft.com")

# ---------------------------------------------------------------------------
# Shared test helpers — used by test_tools_timelogs.py, test_tools_leaves.py
# ---------------------------------------------------------------------------

from fastmcp.server.auth import AccessToken
from fastmcp.tools.function_tool import FunctionTool

import server as server_module


def get_tool_fn(name: str):
    """Get a registered tool's underlying async function by name.

    Looks up the tool in ``mcp.local_provider._components``.
    Raises ``KeyError`` with available tool names if not found.
    """
    lp = server_module.mcp.local_provider
    for comp in lp._components.values():
        if isinstance(comp, FunctionTool) and comp.name == name:
            return comp.fn
    available = sorted(
        comp.name for comp in lp._components.values() if isinstance(comp, FunctionTool)
    )
    raise KeyError(f"Tool {name!r} not found. Available: {available}")


def make_access_token(
    *,
    email: str = "user@arbisoft.com",
    hd: str = "arbisoft.com",
    token: str = "google-access-token-xyz",
) -> AccessToken:
    """Build a fake ``AccessToken`` with the claims structure GoogleProvider produces."""
    return AccessToken(
        token=token,
        client_id="test-client-id",
        scopes=["openid"],
        expires_at=int(time.time()) + 3600,
        claims={
            "sub": "1234567890",
            "email": email,
            "name": "Test User",
            "picture": None,
            "given_name": "Test",
            "family_name": "User",
            "locale": "en",
            "google_user_data": {"hd": hd, "email": email},
            "google_token_info": {},
        },
    )


def patch_token(token: AccessToken | None):
    """Shorthand for patching ``get_access_token`` in the _auth module."""
    return patch("_auth.get_access_token", return_value=token)
