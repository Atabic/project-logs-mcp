"""Unit tests for the ERP MCP server (FastMCP v3).

Every tool is tested via direct function call with mocked ERPClient and
mocked ``get_access_token``. We never hit real Google or ERP APIs.
"""

from __future__ import annotations

import ast
import inspect
import logging
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

import server as server_module

# ---------------------------------------------------------------------------
# AST-based tool discovery helpers
# ---------------------------------------------------------------------------

_SERVER_PATH: str = inspect.getfile(server_module)


def _is_mcp_tool_decorator(dec: ast.expr) -> bool:
    """Return True if *dec* looks like ``@mcp.tool`` or ``@mcp.tool(...)``."""
    # @mcp.tool
    if isinstance(dec, ast.Attribute) and dec.attr == "tool":
        return True
    # @tool (bare name, unlikely but covered)
    if isinstance(dec, ast.Name) and dec.id == "tool":
        return True
    # @mcp.tool(...) call form
    if isinstance(dec, ast.Call):
        func = dec.func
        if isinstance(func, ast.Attribute) and func.attr == "tool":
            return True
    return False


def _discover_tool_names() -> list[str]:
    """Parse server.py AST and return names of all ``@mcp.tool`` async functions.

    This avoids hardcoding tool lists in tests — if a new tool is added to
    server.py, it is automatically picked up by security checks.
    """
    with open(_SERVER_PATH, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if any(_is_mcp_tool_decorator(dec) for dec in node.decorator_list):
            names.append(node.name)
    return names


def _discover_write_tool_names() -> set[str]:
    """Parse server.py AST and return names of ``@mcp.tool`` functions that emit WRITE_OP.

    A write tool is identified by having a string constant containing ``"WRITE_OP"``
    anywhere in its function body (i.e. a ``logger.info("WRITE_OP ...")`` call).
    """
    with open(_SERVER_PATH, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not any(_is_mcp_tool_decorator(dec) for dec in node.decorator_list):
            continue
        # Walk the function body for string constants containing "WRITE_OP"
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and "WRITE_OP" in child.value
            ):
                names.add(node.name)
                break
    return names


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_access_token(
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


@pytest.fixture
def mock_erp() -> AsyncMock:
    """Return a fully mocked ``ERPClient`` instance."""
    client = AsyncMock()
    client.exchange_google_token.return_value = ("erp-token-abc", "user@arbisoft.com")
    client.get_active_projects.return_value = {"status": "success", "data": []}
    client.get_log_labels.return_value = {"status": "success", "data": []}
    client.get_week_logs.return_value = {"status": "success", "data": []}
    client.get_day_logs.return_value = {"status": "success", "data": []}
    client.get_logs_for_date_range.return_value = {"status": "success", "data": []}
    client.get_month_logs.return_value = {"status": "success", "data": []}
    client.create_or_update_log.return_value = {"status": "success"}
    client.delete_log.return_value = {"status": "success"}
    client.complete_week_log.return_value = {"status": "success"}
    client.fill_logs_for_days.return_value = {"status": "success", "created": 5}
    client.check_person_week_project_exists.return_value = {"status": "success", "exists": True}
    client.resolve_project_id.return_value = 42
    client.resolve_label_id.return_value = 7
    client.close.return_value = None
    return client


@pytest.fixture
def valid_token() -> AccessToken:
    """Return a valid arbisoft.com AccessToken."""
    return _make_access_token()


def _patch_token(token: AccessToken | None):
    """Shorthand for patching ``get_access_token`` in the server module."""
    return patch("server.get_access_token", return_value=token)


def _patch_erp(mock_client: AsyncMock):
    """Shorthand for patching the module-level ``erp`` client in server."""
    return patch("server.erp", mock_client, create=True)


# ---------------------------------------------------------------------------
# _get_erp_token tests
# ---------------------------------------------------------------------------


class TestGetErpToken:
    """Tests for the ``_get_erp_token`` internal helper."""

    async def test_returns_erp_token_and_email(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import _get_erp_token

        with _patch_token(valid_token), _patch_erp(mock_erp):
            erp_token, email = await _get_erp_token()

        assert erp_token == "erp-token-abc"
        assert email == "user@arbisoft.com"
        mock_erp.exchange_google_token.assert_awaited_once_with("google-access-token-xyz")

    async def test_rejects_missing_token(self, mock_erp: AsyncMock) -> None:
        from server import _get_erp_token

        with _patch_token(None), _patch_erp(mock_erp):
            with pytest.raises(PermissionError, match="Authentication required"):
                await _get_erp_token()

    async def test_rejects_wrong_hd_claim(self, mock_erp: AsyncMock) -> None:
        """SEC-02: hd claim must match ALLOWED_DOMAIN."""
        from server import _get_erp_token

        bad_token = _make_access_token(hd="evil.com", email="user@arbisoft.com")
        with _patch_token(bad_token), _patch_erp(mock_erp):
            with pytest.raises(PermissionError, match="restricted"):
                await _get_erp_token()

    async def test_rejects_wrong_email_domain(self, mock_erp: AsyncMock) -> None:
        """SEC-02: email must end with @ALLOWED_DOMAIN."""
        from server import _get_erp_token

        bad_token = _make_access_token(hd="arbisoft.com", email="attacker@evil.com")
        with _patch_token(bad_token), _patch_erp(mock_erp):
            with pytest.raises(PermissionError, match="restricted"):
                await _get_erp_token()

    async def test_rejects_missing_email_claim(self, mock_erp: AsyncMock) -> None:
        from server import _get_erp_token

        no_email_token = AccessToken(
            token="tok",
            client_id="cid",
            scopes=["openid"],
            claims={
                "sub": "1",
                "email": None,
                "google_user_data": {"hd": "arbisoft.com"},
            },
        )
        with _patch_token(no_email_token), _patch_erp(mock_erp):
            with pytest.raises(PermissionError, match="email claim"):
                await _get_erp_token()

    async def test_rejects_missing_hd_in_google_user_data(
        self, mock_erp: AsyncMock
    ) -> None:
        """SEC-02: hd is inside google_user_data, not top-level."""
        from server import _get_erp_token

        no_hd_token = AccessToken(
            token="tok",
            client_id="cid",
            scopes=["openid"],
            claims={
                "sub": "1",
                "email": "user@arbisoft.com",
                "google_user_data": {},  # no hd key
            },
        )
        with _patch_token(no_hd_token), _patch_erp(mock_erp):
            with pytest.raises(PermissionError, match="restricted"):
                await _get_erp_token()


# ---------------------------------------------------------------------------
# Read tool tests
# ---------------------------------------------------------------------------


class TestGetActiveProjects:
    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_active_projects

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await get_active_projects()

        assert result["status"] == "success"
        mock_erp.get_active_projects.assert_awaited_once_with("erp-token-abc")

    async def test_unexpected_error_raises_tool_error(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_active_projects

        mock_erp.get_active_projects.side_effect = RuntimeError("DB down")
        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="Failed to fetch active projects"):
                await get_active_projects()


class TestGetLogLabels:
    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_log_labels

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await get_log_labels()

        assert result["status"] == "success"
        mock_erp.get_log_labels.assert_awaited_once_with("erp-token-abc")


class TestGetWeekLogs:
    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_week_logs

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await get_week_logs(week_starting="2024-01-08")

        mock_erp.get_week_logs.assert_awaited_once_with("erp-token-abc", "2024-01-08")
        assert result["status"] == "success"

    async def test_rejects_non_monday(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_week_logs

        # 2024-01-10 is a Wednesday
        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="Monday"):
                await get_week_logs(week_starting="2024-01-10")


class TestGetDayLogs:
    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_day_logs

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await get_day_logs(date_str="2024-01-10")

        mock_erp.get_day_logs.assert_awaited_once_with("erp-token-abc", "2024-01-10")
        assert result["status"] == "success"


class TestGetLogsForDateRange:
    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_logs_for_date_range

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await get_logs_for_date_range(
                start_date="2024-01-01", end_date="2024-01-31"
            )

        mock_erp.get_logs_for_date_range.assert_awaited_once_with(
            "erp-token-abc", "2024-01-01", "2024-01-31"
        )
        assert result["status"] == "success"

    async def test_rejects_end_before_start(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_logs_for_date_range

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="on or after"):
                await get_logs_for_date_range(
                    start_date="2024-01-31", end_date="2024-01-01"
                )


class TestGetLogsForDateRangeCap:
    async def test_rejects_range_over_366_days(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_logs_for_date_range

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="exceeding the maximum"):
                await get_logs_for_date_range(
                    start_date="2024-01-01", end_date="2025-01-02"  # 367 days
                )

    async def test_366_days_allowed(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_logs_for_date_range

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await get_logs_for_date_range(
                start_date="2024-01-01", end_date="2024-12-31"  # 366 days
            )
        assert result["status"] == "success"


class TestGetMonthLogs:
    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_month_logs

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await get_month_logs(year=2024, month=3)

        mock_erp.get_month_logs.assert_awaited_once_with("erp-token-abc", 2024, 3)
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Write tool tests
# ---------------------------------------------------------------------------


class TestCreateOrUpdateLog:
    async def test_calls_erp_client_with_resolved_ids(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import create_or_update_log

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await create_or_update_log(
                date="2024-01-10",
                description="Worked on feature X",
                hours=4.5,
                project_id=42,
                label_id=7,
            )

        mock_erp.resolve_project_id.assert_awaited_once_with("erp-token-abc", 42, None)
        mock_erp.resolve_label_id.assert_awaited_once_with("erp-token-abc", 7, None)
        mock_erp.create_or_update_log.assert_awaited_once_with(
            "erp-token-abc",
            date_str="2024-01-10",
            project_id=42,
            description="Worked on feature X",
            hours=4.5,
            label_id=7,
        )
        assert result["status"] == "success"

    async def test_resolves_by_name(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import create_or_update_log

        with _patch_token(valid_token), _patch_erp(mock_erp):
            await create_or_update_log(
                date="2024-01-10",
                description="task",
                hours=8,
                project_name="My Project",
                label_name="Coding",
            )

        mock_erp.resolve_project_id.assert_awaited_once_with(
            "erp-token-abc", None, "My Project"
        )
        mock_erp.resolve_label_id.assert_awaited_once_with(
            "erp-token-abc", None, "Coding"
        )

    async def test_audit_log_emitted(
        self, mock_erp: AsyncMock, valid_token: AccessToken, caplog: pytest.LogCaptureFixture
    ) -> None:
        from server import create_or_update_log

        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token), _patch_erp(mock_erp):
                await create_or_update_log(
                    date="2024-01-10",
                    description="task",
                    hours=8,
                    project_id=42,
                )

        assert any(
            "WRITE_OP" in r.message and "create_or_update_log" in r.message
            for r in caplog.records
        )

    async def test_rejects_oversized_description(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import create_or_update_log

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="Description too long"):
                await create_or_update_log(
                    date="2024-01-10",
                    description="x" * 5001,
                    hours=8,
                    project_id=42,
                )

    async def test_rejects_zero_hours(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import create_or_update_log

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="hours must be between"):
                await create_or_update_log(
                    date="2024-01-10", description="task", hours=0, project_id=42,
                )

    async def test_rejects_negative_hours(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import create_or_update_log

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="hours must be between"):
                await create_or_update_log(
                    date="2024-01-10", description="task", hours=-1, project_id=42,
                )

    async def test_rejects_over_24_hours(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import create_or_update_log

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="hours must be between"):
                await create_or_update_log(
                    date="2024-01-10", description="task", hours=25, project_id=42,
                )

    async def test_unexpected_error_no_stack_trace(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        """SEC-04: ToolError message must not contain stack traces."""
        from server import create_or_update_log

        mock_erp.create_or_update_log.side_effect = RuntimeError("internal DB error")
        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="Failed to create or update log") as exc_info:
                await create_or_update_log(
                    date="2024-01-10",
                    description="task",
                    hours=8,
                    project_id=42,
                )
        # Ensure the error message does NOT contain the internal error details
        assert "internal DB error" not in str(exc_info.value)
        assert "Traceback" not in str(exc_info.value)


class TestDeleteLog:
    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import delete_log

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await delete_log(
                date="2024-01-10",
                description="task to delete",
                project_id=42,
            )

        mock_erp.resolve_project_id.assert_awaited_once_with("erp-token-abc", 42, None)
        mock_erp.delete_log.assert_awaited_once_with(
            "erp-token-abc",
            date_str="2024-01-10",
            project_id=42,
            description="task to delete",
        )
        assert result["status"] == "success"

    async def test_rejects_oversized_description(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import delete_log

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="Description too long"):
                await delete_log(
                    date="2024-01-10",
                    description="x" * 5001,
                    project_id=42,
                )

    async def test_audit_log_emitted(
        self, mock_erp: AsyncMock, valid_token: AccessToken, caplog: pytest.LogCaptureFixture
    ) -> None:
        from server import delete_log

        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token), _patch_erp(mock_erp):
                await delete_log(date="2024-01-10", description="x", project_id=42)

        assert any("WRITE_OP" in r.message and "delete_log" in r.message for r in caplog.records)


class TestCompleteWeekLog:
    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import complete_week_log

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await complete_week_log(week_starting="2024-01-08")

        mock_erp.complete_week_log.assert_awaited_once_with(
            "erp-token-abc", "2024-01-08", save_draft=False
        )
        assert result["status"] == "success"

    async def test_save_draft_mode(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import complete_week_log

        with _patch_token(valid_token), _patch_erp(mock_erp):
            await complete_week_log(week_starting="2024-01-08", save_draft=True)

        mock_erp.complete_week_log.assert_awaited_once_with(
            "erp-token-abc", "2024-01-08", save_draft=True
        )

    async def test_audit_log_emitted(
        self, mock_erp: AsyncMock, valid_token: AccessToken, caplog: pytest.LogCaptureFixture
    ) -> None:
        from server import complete_week_log

        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token), _patch_erp(mock_erp):
                await complete_week_log(week_starting="2024-01-08")

        assert any(
            "WRITE_OP" in r.message and "complete_week_log" in r.message
            for r in caplog.records
        )


class TestFillLogsForDays:
    async def test_rejects_oversized_description(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import fill_logs_for_days

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="Description too long"):
                await fill_logs_for_days(
                    start_date="2024-01-08",
                    end_date="2024-01-12",
                    description="x" * 5001,
                    project_id=42,
                )

    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import fill_logs_for_days

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await fill_logs_for_days(
                start_date="2024-01-08",
                end_date="2024-01-12",
                description="Sprint work",
                hours_per_day=8.0,
                project_id=42,
                skip_weekends=True,
            )

        mock_erp.fill_logs_for_days.assert_awaited_once_with(
            "erp-token-abc",
            start_date="2024-01-08",
            end_date="2024-01-12",
            project_id=42,
            description="Sprint work",
            hours_per_day=8.0,
            label_id=7,
            skip_weekends=True,
        )
        assert result["status"] == "success"

    async def test_rejects_range_over_31_days(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        """SEC-05: fill_logs_for_days capped at 31 days."""
        from server import fill_logs_for_days

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="exceeding the maximum of 31"):
                await fill_logs_for_days(
                    start_date="2024-01-01",
                    end_date="2024-03-01",  # 61 days
                    description="bulk",
                    project_id=42,
                )

        # ERP client methods should NOT have been called
        mock_erp.fill_logs_for_days.assert_not_awaited()

    async def test_rejects_end_before_start(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import fill_logs_for_days

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="on or after"):
                await fill_logs_for_days(
                    start_date="2024-01-31",
                    end_date="2024-01-01",
                    description="backwards",
                    project_id=42,
                )

    async def test_exactly_31_days_allowed(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import fill_logs_for_days

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await fill_logs_for_days(
                start_date="2024-01-01",
                end_date="2024-01-31",  # exactly 31 days
                description="full month",
                project_id=42,
            )

        assert result["status"] == "success"

    async def test_32_days_rejected(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import fill_logs_for_days

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="exceeding the maximum of 31"):
                await fill_logs_for_days(
                    start_date="2024-01-01",
                    end_date="2024-02-01",  # 32 days
                    description="one too many",
                    project_id=42,
                )

    async def test_invalid_date_format(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import fill_logs_for_days

        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="Invalid date format"):
                await fill_logs_for_days(
                    start_date="not-a-date",
                    end_date="2024-01-10",
                    description="bad",
                    project_id=42,
                )

    async def test_audit_log_emitted(
        self, mock_erp: AsyncMock, valid_token: AccessToken, caplog: pytest.LogCaptureFixture
    ) -> None:
        from server import fill_logs_for_days

        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token), _patch_erp(mock_erp):
                await fill_logs_for_days(
                    start_date="2024-01-08",
                    end_date="2024-01-12",
                    description="work",
                    project_id=42,
                )

        assert any(
            "WRITE_OP" in r.message and "fill_logs_for_days" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Diagnostic tool tests
# ---------------------------------------------------------------------------


class TestCheckPersonWeekProjectExists:
    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import check_person_week_project_exists

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await check_person_week_project_exists(
                date="2024-01-10", project_id=42
            )

        mock_erp.resolve_project_id.assert_awaited_once_with("erp-token-abc", 42, None)
        mock_erp.check_person_week_project_exists.assert_awaited_once_with(
            "erp-token-abc", "2024-01-10", 42
        )
        assert result["exists"] is True

    async def test_resolves_by_name(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import check_person_week_project_exists

        with _patch_token(valid_token), _patch_erp(mock_erp):
            await check_person_week_project_exists(
                date="2024-01-10", project_name="My Project"
            )

        mock_erp.resolve_project_id.assert_awaited_once_with(
            "erp-token-abc", None, "My Project"
        )


# ---------------------------------------------------------------------------
# SEC-01: No tool accepts an `email` parameter
# ---------------------------------------------------------------------------


class TestNoEmailParameter:
    """SEC-01: Verify identity comes from the token, not a parameter."""

    def test_no_tool_function_has_email_param(self) -> None:
        """Parse server.py AST and check that no @mcp.tool function has an 'email' param."""
        server_path = inspect.getfile(server_module)
        with open(server_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        tool_functions_with_email: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            # Check if this function has a decorator that looks like mcp.tool
            is_tool = False
            for dec in node.decorator_list:
                if isinstance(dec, ast.Attribute) and dec.attr == "tool":
                    is_tool = True
                elif isinstance(dec, ast.Name) and dec.id == "tool":
                    is_tool = True
                elif isinstance(dec, ast.Call):
                    func = dec.func
                    if isinstance(func, ast.Attribute) and func.attr == "tool":
                        is_tool = True
            if not is_tool:
                continue

            # Check parameters
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.arg == "email":
                    tool_functions_with_email.append(node.name)

        assert tool_functions_with_email == [], (
            f"SEC-01 violation: these tools accept an 'email' parameter: "
            f"{tool_functions_with_email}"
        )

    def test_runtime_signatures_have_no_email(self) -> None:
        """Inspect the actual function signatures at runtime (auto-discovered)."""
        import server as srv

        tool_names = _discover_tool_names()
        assert tool_names, "No @mcp.tool functions discovered — is server.py empty?"

        for name in tool_names:
            func = getattr(srv, name)
            sig = inspect.signature(func)
            assert "email" not in sig.parameters, (
                f"SEC-01: {name} has an 'email' parameter"
            )


# ---------------------------------------------------------------------------
# SEC-04: No stack traces in ToolError
# ---------------------------------------------------------------------------


class TestNoStackTraces:
    """SEC-04: Verify unexpected errors produce generic messages, not tracebacks."""

    async def test_get_active_projects_generic_error(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_active_projects

        mock_erp.get_active_projects.side_effect = RuntimeError("segfault simulation")
        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError) as exc_info:
                await get_active_projects()

        msg = str(exc_info.value)
        assert "segfault" not in msg
        assert "Traceback" not in msg

    async def test_delete_log_generic_error(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import delete_log

        mock_erp.delete_log.side_effect = RuntimeError("db connection lost")
        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError) as exc_info:
                await delete_log(date="2024-01-10", description="x", project_id=42)

        msg = str(exc_info.value)
        assert "db connection" not in msg

    async def test_fill_logs_generic_error(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import fill_logs_for_days

        mock_erp.fill_logs_for_days.side_effect = RuntimeError("timeout")
        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError) as exc_info:
                await fill_logs_for_days(
                    start_date="2024-01-08",
                    end_date="2024-01-10",
                    description="work",
                    project_id=42,
                )

        msg = str(exc_info.value)
        assert "timeout" not in msg


# ---------------------------------------------------------------------------
# Permission / ValueError propagation tests
# ---------------------------------------------------------------------------


class TestErrorPropagation:
    """PermissionError and ValueError from ERPClient should surface as ToolError."""

    async def test_permission_error_surfaces(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_week_logs

        mock_erp.get_week_logs.side_effect = PermissionError("Token expired")
        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="Token expired"):
                await get_week_logs(week_starting="2024-01-08")

    async def test_value_error_surfaces(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import create_or_update_log

        mock_erp.resolve_project_id.side_effect = ValueError(
            "Project 'Nonexistent' not found"
        )
        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="not found"):
                await create_or_update_log(
                    date="2024-01-10",
                    description="task",
                    hours=8,
                    project_name="Nonexistent",
                )


# ---------------------------------------------------------------------------
# Write tool audit log coverage
# ---------------------------------------------------------------------------


class TestAuditLogCoverage:
    """Ensure ALL write tools emit structured audit log entries."""

    WRITE_TOOLS = [
        (
            "create_or_update_log",
            {"date": "2024-01-10", "description": "t", "hours": 8, "project_id": 42},
        ),
        ("delete_log", {"date": "2024-01-10", "description": "t", "project_id": 42}),
        ("complete_week_log", {"week_starting": "2024-01-08"}),
        (
            "fill_logs_for_days",
            {
                "start_date": "2024-01-08",
                "end_date": "2024-01-10",
                "description": "work",
                "project_id": 42,
            },
        ),
    ]

    @pytest.mark.parametrize(("tool_name", "kwargs"), WRITE_TOOLS, ids=[t[0] for t in WRITE_TOOLS])
    async def test_write_tool_emits_audit_log(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        mock_erp: AsyncMock,
        valid_token: AccessToken,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import server as srv

        tool_fn = getattr(srv, tool_name)
        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token), _patch_erp(mock_erp):
                await tool_fn(**kwargs)

        audit_records = [
            r for r in caplog.records
            if "WRITE_OP" in r.message and f"tool={tool_name}" in r.message
        ]
        assert len(audit_records) >= 1, (
            f"No WRITE_OP audit log found for {tool_name}. "
            f"Records: {[r.message for r in caplog.records]}"
        )

    def test_all_write_tools_covered(self) -> None:
        """Guard: every @mcp.tool that emits WRITE_OP must appear in WRITE_TOOLS."""
        discovered = _discover_write_tool_names()
        covered = {name for name, _ in self.WRITE_TOOLS}
        missing = discovered - covered
        assert not missing, (
            f"Write tools missing from TestAuditLogCoverage.WRITE_TOOLS: {missing}. "
            f"Add test entries for these tools to ensure audit logging is verified."
        )
