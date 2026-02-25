"""ERP MCP Server — FastMCP v3 with Google OAuth.

Exposes ERP time-logging tools via the Model Context Protocol.
Authentication flows through Google OAuth (FastMCP GoogleProvider);
the raw Google access token is exchanged for an ERP session token
on every tool call.
"""

from __future__ import annotations

import functools
import importlib.metadata
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date as date_type
from typing import Any, ParamSpec, TypeVar

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import get_access_token
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from erp_client import ERPClient

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

ALLOWED_DOMAIN: str = os.environ.get("ALLOWED_DOMAIN", "arbisoft.com").lower().strip()
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


# Module-level singleton — requires single-worker deployment (stateless_http=True).
# Tests must patch via `server.erp = mock_client` or use the _patch_erp fixture.
erp: ERPClient | None = None  # initialized in _lifespan

P = ParamSpec("P")
R = TypeVar("R")


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Manage application resources during server lifecycle."""
    global erp  # intentional module-level singleton
    if not _google_client_id or not _google_client_secret:
        logger.critical("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set — refusing to start")
        raise SystemExit(1)
    erp = ERPClient(base_url=ERP_BASE_URL, allowed_domain=ALLOWED_DOMAIN)
    logger.info("ERP MCP server starting up")
    try:
        yield
    finally:
        logger.info("ERP MCP server shutting down")
        if erp is not None:
            await erp.close()


def _get_erp() -> ERPClient:
    """Return the ERPClient singleton, or raise if server hasn't started."""
    if erp is None:
        raise RuntimeError("ERPClient not initialized. Server lifespan has not started.")
    return erp


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
# Internal helpers
# ---------------------------------------------------------------------------

_MAX_FILL_DAYS: int = 31
_MAX_QUERY_DAYS: int = 366
_MAX_DESCRIPTION_LEN: int = 5000


def _check_erp_result(result: dict[str, Any]) -> dict[str, Any]:
    """Raise ToolError if ERPClient returned an error dict."""
    if isinstance(result, dict) and result.get("status") == "error":
        raise ToolError(result.get("message", "ERP operation failed"))
    return result


def _tool_error_handler(
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
                raise  # Already a ToolError, pass through
            except PermissionError as exc:
                raise ToolError(str(exc)) from exc
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            except Exception:
                logger.exception("%s failed", fn.__name__)
                raise ToolError(error_message) from None

        return wrapper

    return decorator


async def _get_erp_token() -> tuple[str, str]:
    """Extract Google token from the current request, enforce domain, exchange for ERP token.

    Returns:
        (erp_token, email) tuple.

    Raises:
        PermissionError: If domain check fails or token is missing.
    """
    access_token: AccessToken | None = get_access_token()
    if access_token is None:
        raise PermissionError("Authentication required. Please sign in with Google.")

    # --- SEC-02: Domain restriction at MCP layer ---
    claims: dict[str, Any] = access_token.claims
    google_user_data: dict[str, Any] = claims.get("google_user_data", {})
    hd: str | None = google_user_data.get("hd")
    email: str | None = claims.get("email")

    if not email:
        raise PermissionError("Google token does not contain an email claim.")

    if hd != ALLOWED_DOMAIN:
        raise PermissionError(
            f"Access restricted to {ALLOWED_DOMAIN} accounts."
        )
    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        raise PermissionError(
            f"Access restricted to {ALLOWED_DOMAIN} accounts."
        )

    # Exchange the raw Google access token for an ERP session token.
    logger.debug("Exchanging Google token for ERP token (email=%s)", email)
    google_token: str = access_token.token
    try:
        erp_token, verified_email = await _get_erp().exchange_google_token(google_token)
    except ConnectionError as exc:
        logger.warning("ERP token exchange failed: connection error")
        raise PermissionError(
            "ERP service is temporarily unavailable. Please try again later."
        ) from exc
    logger.debug("ERP token exchange successful for %s", verified_email)
    return erp_token, verified_email


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


@mcp.tool
@_tool_error_handler("Failed to fetch active projects. Please try again.")
async def get_active_projects() -> dict[str, Any]:
    """Get list of active projects/subteams for the authenticated user."""
    token, _email = await _get_erp_token()
    return _check_erp_result(await _get_erp().get_active_projects(token))


@mcp.tool
@_tool_error_handler("Failed to fetch log labels. Please try again.")
async def get_log_labels() -> dict[str, Any]:
    """Get available log labels for categorizing log entries."""
    token, _email = await _get_erp_token()
    return _check_erp_result(await _get_erp().get_log_labels(token))


@mcp.tool
@_tool_error_handler("Failed to fetch week logs. Please try again.")
async def get_week_logs(week_starting: str) -> dict[str, Any]:
    """Get project logs for a specific week.

    Args:
        week_starting: Week starting date in YYYY-MM-DD format (must be a Monday).
    """
    d = date_type.fromisoformat(week_starting)
    if d.weekday() != 0:
        raise ToolError(
            f"week_starting must be a Monday, got {week_starting} ({d.strftime('%A')})"
        )
    token, _email = await _get_erp_token()
    return _check_erp_result(await _get_erp().get_week_logs(token, week_starting))


@mcp.tool
@_tool_error_handler("Failed to fetch day logs. Please try again.")
async def get_day_logs(date_str: str) -> dict[str, Any]:
    """Get detailed project logs for a specific day.

    Returns all projects, tasks, and time logged for that day.

    Args:
        date_str: Date in YYYY-MM-DD format.
    """
    token, _email = await _get_erp_token()
    return _check_erp_result(await _get_erp().get_day_logs(token, date_str))


@mcp.tool
@_tool_error_handler("Failed to fetch logs for date range. Please try again.")
async def get_logs_for_date_range(
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Get project logs for a date range.

    Args:
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
    """
    # Validate and cap date range
    try:
        start = date_type.fromisoformat(start_date)
        end = date_type.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError(
            f"Invalid date format. Use YYYY-MM-DD. Details: {exc}"
        ) from exc
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    day_span = (end - start).days + 1
    if day_span > _MAX_QUERY_DAYS:
        raise ValueError(
            f"Date range spans {day_span} days, exceeding the maximum of "
            f"{_MAX_QUERY_DAYS} days for read queries."
        )

    token, _email = await _get_erp_token()
    return _check_erp_result(
        await _get_erp().get_logs_for_date_range(token, start_date, end_date)
    )


@mcp.tool
@_tool_error_handler("Failed to fetch month logs. Please try again.")
async def get_month_logs(year: int, month: int) -> dict[str, Any]:
    """Get all project logs for a specific month and year.

    Args:
        year: Year (e.g. 2024).
        month: Month number (1-12).
    """
    token, _email = await _get_erp_token()
    return _check_erp_result(await _get_erp().get_month_logs(token, year, month))


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


@mcp.tool
@_tool_error_handler("Failed to create or update log. Please try again.")
async def create_or_update_log(
    date: str,
    description: str,
    hours: float,
    project_id: int | None = None,
    project_name: str | None = None,
    label_id: int | None = None,
    label_name: str | None = None,
) -> dict[str, Any]:
    """Add or update a time log entry for a date.

    Specify the project by ID or name (one is required). Optionally categorize
    with a label by ID or name.

    Args:
        date: Date in YYYY-MM-DD format.
        description: Description of work done.
        hours: Hours worked (decimal allowed, e.g. 2 or 8.5, 0-24).
        project_id: Project/subteam ID (from get_active_projects). Use this or project_name.
        project_name: Project/team name (e.g. 'Your Project Name'). Resolved automatically.
        label_id: Label ID (from get_log_labels). Use this or label_name.
        label_name: Label name (e.g. 'Coding'). Resolved automatically.
    """
    if project_id is not None and project_name is not None:
        raise ToolError("Provide either project_id or project_name, not both.")
    if len(description) > _MAX_DESCRIPTION_LEN:
        raise ToolError(f"Description too long (max {_MAX_DESCRIPTION_LEN} characters)")
    if hours <= 0 or hours > 24:
        raise ValueError(
            f"hours must be between 0 (exclusive) and 24 (inclusive), got {hours}"
        )
    if round(hours * 60) < 1:
        raise ToolError("Hours too small: rounds to 0 minutes. Minimum is ~0.02 (1 minute).")
    token, email = await _get_erp_token()
    client = _get_erp()
    resolved_project_id = await client.resolve_project_id(token, project_id, project_name)
    resolved_label_id = await client.resolve_label_id(token, label_id, label_name)
    result = _check_erp_result(
        await client.create_or_update_log(
            token,
            date_str=date,
            project_id=resolved_project_id,
            description=description,
            hours=hours,
            label_id=resolved_label_id,
        )
    )
    logger.info(
        "WRITE_OP tool=create_or_update_log user=%s date=%s project_id=%s hours=%s",
        email, date, resolved_project_id, hours,
    )
    return result


@mcp.tool
@_tool_error_handler("Failed to delete log. Please try again.")
async def delete_log(
    date: str,
    description: str,
    project_id: int | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Remove a time log entry for a date.

    Specify the project by ID or name (one is required) and provide the exact
    task description of the entry to delete.

    Args:
        date: Date in YYYY-MM-DD format.
        description: Exact task description of the log entry to remove.
        project_id: Project/subteam ID (from get_active_projects). Use this or project_name.
        project_name: Project/team name. Resolved automatically.
    """
    if project_id is not None and project_name is not None:
        raise ToolError("Provide either project_id or project_name, not both.")
    if len(description) > _MAX_DESCRIPTION_LEN:
        raise ToolError(f"Description too long (max {_MAX_DESCRIPTION_LEN} characters)")
    token, email = await _get_erp_token()
    client = _get_erp()
    resolved_project_id = await client.resolve_project_id(token, project_id, project_name)
    result = _check_erp_result(
        await client.delete_log(
            token,
            date_str=date,
            project_id=resolved_project_id,
            description=description,
        )
    )
    logger.info(
        "WRITE_OP tool=delete_log user=%s date=%s project_id=%s",
        email, date, resolved_project_id,
    )
    return result


@mcp.tool
@_tool_error_handler("Failed to complete week log. Please try again.")
async def complete_week_log(
    week_starting: str,
    save_draft: bool = False,
) -> dict[str, Any]:
    """Mark a week's project logs as completed (or save as draft).

    Args:
        week_starting: Week starting date in YYYY-MM-DD format (must be a Monday).
        save_draft: If true, saves as draft without completing. Default is false.
    """
    d = date_type.fromisoformat(week_starting)
    if d.weekday() != 0:
        raise ToolError(
            f"week_starting must be a Monday, got {week_starting} ({d.strftime('%A')})"
        )
    token, email = await _get_erp_token()
    result = _check_erp_result(
        await _get_erp().complete_week_log(token, week_starting, save_draft=save_draft)
    )
    logger.info(
        "WRITE_OP tool=complete_week_log user=%s week_starting=%s save_draft=%s",
        email, week_starting, save_draft,
    )
    return result


@mcp.tool
@_tool_error_handler("Failed to fill logs for days. Please try again.")
async def fill_logs_for_days(
    start_date: str,
    end_date: str,
    description: str,
    hours_per_day: float = 8.0,
    project_id: int | None = None,
    project_name: str | None = None,
    label_id: int | None = None,
    label_name: str | None = None,
    skip_weekends: bool = False,
) -> dict[str, Any]:
    """Fill time logs for multiple days at once (bulk operation).

    Specify the project by ID or name (one is required). Optionally set a label.
    The date range is capped at 31 days for safety.

    Args:
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
        description: Description of the work done.
        hours_per_day: Hours to log per day (default 8).
        project_id: Project/subteam ID. Use this or project_name.
        project_name: Project/team name. Resolved automatically.
        label_id: Label ID. Use this or label_name.
        label_name: Label name. Resolved automatically.
        skip_weekends: Skip Saturday and Sunday. Default is false.
    """
    if project_id is not None and project_name is not None:
        raise ToolError("Provide either project_id or project_name, not both.")
    if len(description) > _MAX_DESCRIPTION_LEN:
        raise ToolError(f"Description too long (max {_MAX_DESCRIPTION_LEN} characters)")
    if hours_per_day <= 0 or hours_per_day > 24:
        raise ValueError(
            f"hours_per_day must be between 0 (exclusive) and 24 (inclusive), "
            f"got {hours_per_day}"
        )
    if round(hours_per_day * 60) < 1:
        raise ToolError("Hours too small: rounds to 0 minutes. Minimum is ~0.02 (1 minute).")
    # --- SEC-05: 31-day cap ---
    try:
        start = date_type.fromisoformat(start_date)
        end = date_type.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError(
            f"Invalid date format. Use YYYY-MM-DD. Details: {exc}"
        ) from exc

    day_count = (end - start).days + 1
    if day_count > _MAX_FILL_DAYS:
        raise ValueError(
            f"Date range spans {day_count} days, exceeding the maximum of "
            f"{_MAX_FILL_DAYS} days. Please use a shorter range."
        )
    if day_count < 1:
        raise ValueError("end_date must be on or after start_date.")

    token, email = await _get_erp_token()
    client = _get_erp()
    resolved_project_id = await client.resolve_project_id(token, project_id, project_name)
    resolved_label_id = await client.resolve_label_id(token, label_id, label_name)
    result = _check_erp_result(
        await client.fill_logs_for_days(
            token,
            start_date=start_date,
            end_date=end_date,
            project_id=resolved_project_id,
            description=description,
            hours_per_day=hours_per_day,
            label_id=resolved_label_id,
            skip_weekends=skip_weekends,
        )
    )
    logger.info(
        "WRITE_OP tool=fill_logs_for_days user=%s start=%s end=%s project_id=%s days=%s",
        email, start_date, end_date, resolved_project_id, day_count,
    )
    return result


# ---------------------------------------------------------------------------
# Diagnostic tools
# ---------------------------------------------------------------------------


@mcp.tool
@_tool_error_handler("Failed to check PersonWeekProject existence. Please try again.")
async def check_person_week_project_exists(
    date: str,
    project_id: int | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Check if a PersonWeekProject exists for a given date and project.

    PersonWeekProject is required before adding logs. It links PersonWeekLog
    (for the week) and PersonTeam (linking person to subteam). Returns what
    is needed if it does not exist.

    Args:
        date: Date in YYYY-MM-DD format.
        project_id: Project/subteam ID. Use this or project_name.
        project_name: Project/team name. Resolved automatically.
    """
    if project_id is not None and project_name is not None:
        raise ToolError("Provide either project_id or project_name, not both.")
    token, _email = await _get_erp_token()
    client = _get_erp()
    resolved_project_id = await client.resolve_project_id(token, project_id, project_name)
    return _check_erp_result(
        await client.check_person_week_project_exists(
            token, date, resolved_project_id
        )
    )


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
