"""Shared authentication and error-handling helpers for MCP tools."""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token

from clients import get_registry

logger = logging.getLogger("erp_mcp.server")

ALLOWED_DOMAIN: str = os.environ.get("ALLOWED_DOMAIN", "arbisoft.com").lower().strip()

P = ParamSpec("P")
R = TypeVar("R")


def check_erp_result(result: dict[str, Any]) -> dict[str, Any]:
    """Raise ToolError if ERPClient returned an error dict."""
    if isinstance(result, dict) and result.get("status") == "error":
        raise ToolError(result.get("message", "ERP operation failed"))
    return result


def tool_error_handler(
    error_message: str,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator that wraps MCP tool functions with standard error handling.

    Converts PermissionError and ValueError to ToolError (preserving message),
    and catches all other exceptions with a generic message (SEC-04).
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return await fn(*args, **kwargs)
            except ToolError:
                raise
            except PermissionError as exc:
                raise ToolError(str(exc)) from exc
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            except Exception:
                logger.exception("%s failed", fn.__name__)
                raise ToolError(error_message) from None

        return wrapper

    return decorator


async def get_erp_token() -> tuple[str, str]:
    """Extract Google token, enforce domain, exchange for ERP token.

    Returns:
        (erp_token, email) tuple.

    Raises:
        PermissionError: If domain check fails or token is missing.
    """
    access_token: AccessToken | None = get_access_token()
    if access_token is None:
        raise PermissionError("Authentication required. Please sign in with Google.")

    claims: dict[str, Any] = access_token.claims
    google_user_data: dict[str, Any] = claims.get("google_user_data", {})
    hd: str | None = google_user_data.get("hd")
    email: str | None = claims.get("email")

    if not email:
        raise PermissionError("Google token does not contain an email claim.")

    if hd != ALLOWED_DOMAIN:
        raise PermissionError(f"Access restricted to {ALLOWED_DOMAIN} accounts.")
    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        raise PermissionError(f"Access restricted to {ALLOWED_DOMAIN} accounts.")

    logger.debug("Exchanging Google token for ERP token (email=%s)", email)
    google_token: str = access_token.token
    try:
        erp_token, verified_email = await get_registry().base.exchange_google_token(google_token)
    except ConnectionError as exc:
        logger.warning("ERP token exchange failed: connection error")
        raise PermissionError(
            "ERP service is temporarily unavailable. Please try again later."
        ) from exc
    logger.debug("ERP token exchange successful for %s", verified_email)
    return erp_token, verified_email
