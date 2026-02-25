"""Unit tests for timelog MCP tools (tools/timelogs.py).

Every tool is tested via direct function call with mocked ERPClientRegistry
and mocked ``get_access_token``.  We never hit real Google or ERP APIs.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

import server as server_module
from clients import set_registry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_tool_fn(name: str):
    """Get a registered tool's underlying async function."""
    from fastmcp.tools.function_tool import FunctionTool

    lp = server_module.mcp.local_provider
    for comp in lp._components.values():
        if isinstance(comp, FunctionTool) and comp.name == name:
            return comp.fn
    available = sorted(
        comp.name for comp in lp._components.values() if isinstance(comp, FunctionTool)
    )
    raise KeyError(f"Tool {name!r} not found. Available: {available}")


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


def _patch_token(token: AccessToken | None):
    """Shorthand for patching ``get_access_token`` in the _auth module."""
    return patch("_auth.get_access_token", return_value=token)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_timelogs() -> AsyncMock:
    """Return a fully mocked ``TimelogsClient`` instance."""
    client = AsyncMock()
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
    return client


@pytest.fixture
def mock_registry(mock_timelogs: AsyncMock) -> AsyncMock:
    """Return a fully mocked ``ERPClientRegistry`` instance."""
    registry = AsyncMock()
    registry.timelogs = mock_timelogs
    registry.base = AsyncMock()
    registry.base.exchange_google_token.return_value = ("erp-token-abc", "user@arbisoft.com")
    return registry


@pytest.fixture
def valid_token() -> AccessToken:
    """Return a valid arbisoft.com AccessToken."""
    return _make_access_token()


@pytest.fixture(autouse=True)
def _cleanup_registry():
    """Ensure registry is cleaned up after each test."""
    yield
    set_registry(None)


# ---------------------------------------------------------------------------
# _get_erp_token tests (now in _auth module)
# ---------------------------------------------------------------------------


class TestGetErpToken:
    """Tests for the ``get_erp_token`` internal helper in _auth."""

    async def test_returns_erp_token_and_email(
        self, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        from _auth import get_erp_token

        set_registry(mock_registry)
        with _patch_token(valid_token):
            erp_token, email = await get_erp_token()

        assert erp_token == "erp-token-abc"
        assert email == "user@arbisoft.com"
        mock_registry.base.exchange_google_token.assert_awaited_once_with(
            "google-access-token-xyz"
        )

    async def test_rejects_missing_token(self, mock_registry: AsyncMock) -> None:
        from _auth import get_erp_token

        set_registry(mock_registry)
        with _patch_token(None):
            with pytest.raises(PermissionError, match="Authentication required"):
                await get_erp_token()

    async def test_rejects_wrong_hd_claim(self, mock_registry: AsyncMock) -> None:
        """SEC-02: hd claim must match ALLOWED_DOMAIN."""
        from _auth import get_erp_token

        bad_token = _make_access_token(hd="evil.com", email="user@arbisoft.com")
        set_registry(mock_registry)
        with _patch_token(bad_token):
            with pytest.raises(PermissionError, match="restricted"):
                await get_erp_token()

    async def test_rejects_wrong_email_domain(self, mock_registry: AsyncMock) -> None:
        """SEC-02: email must end with @ALLOWED_DOMAIN."""
        from _auth import get_erp_token

        bad_token = _make_access_token(hd="arbisoft.com", email="attacker@evil.com")
        set_registry(mock_registry)
        with _patch_token(bad_token):
            with pytest.raises(PermissionError, match="restricted"):
                await get_erp_token()

    async def test_rejects_missing_email_claim(self, mock_registry: AsyncMock) -> None:
        from _auth import get_erp_token

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
        set_registry(mock_registry)
        with _patch_token(no_email_token):
            with pytest.raises(PermissionError, match="email claim"):
                await get_erp_token()

    async def test_rejects_missing_hd_in_google_user_data(
        self, mock_registry: AsyncMock
    ) -> None:
        """SEC-02: hd is inside google_user_data, not top-level."""
        from _auth import get_erp_token

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
        set_registry(mock_registry)
        with _patch_token(no_hd_token):
            with pytest.raises(PermissionError, match="restricted"):
                await get_erp_token()


# ---------------------------------------------------------------------------
# Read tool tests
# ---------------------------------------------------------------------------


class TestTimelogsListProjects:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_list_projects")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn()

        assert result["status"] == "success"
        mock_timelogs.get_active_projects.assert_awaited_once_with("erp-token-abc")

    async def test_unexpected_error_raises_tool_error(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_list_projects")
        mock_timelogs.get_active_projects.side_effect = RuntimeError("DB down")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Failed to fetch active projects"):
                await fn()


class TestTimelogsListLabels:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_list_labels")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn()

        assert result["status"] == "success"
        mock_timelogs.get_log_labels.assert_awaited_once_with("erp-token-abc")


class TestTimelogsGetWeek:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_get_week")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(week_starting="2024-01-08")

        mock_timelogs.get_week_logs.assert_awaited_once_with("erp-token-abc", "2024-01-08")
        assert result["status"] == "success"

    async def test_rejects_non_monday(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_get_week")
        # 2024-01-10 is a Wednesday
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Monday"):
                await fn(week_starting="2024-01-10")


class TestTimelogsGetDay:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_get_day")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(date_str="2024-01-10")

        mock_timelogs.get_day_logs.assert_awaited_once_with("erp-token-abc", "2024-01-10")
        assert result["status"] == "success"


class TestTimelogsGetRange:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_get_range")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(start_date="2024-01-01", end_date="2024-01-31")

        mock_timelogs.get_logs_for_date_range.assert_awaited_once_with(
            "erp-token-abc", "2024-01-01", "2024-01-31"
        )
        assert result["status"] == "success"

    async def test_rejects_end_before_start(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_get_range")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="on or after"):
                await fn(start_date="2024-01-31", end_date="2024-01-01")


class TestTimelogsGetRangeCap:
    async def test_rejects_range_over_366_days(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_get_range")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="exceeding the maximum"):
                await fn(start_date="2024-01-01", end_date="2025-01-02")  # 367 days

    async def test_366_days_allowed(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_get_range")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(start_date="2024-01-01", end_date="2024-12-31")  # 366 days

        assert result["status"] == "success"


class TestTimelogsGetMonth:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_get_month")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(year=2024, month=3)

        mock_timelogs.get_month_logs.assert_awaited_once_with("erp-token-abc", 2024, 3)
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Write tool tests
# ---------------------------------------------------------------------------


class TestTimelogsUpsertEntry:
    async def test_calls_client_with_resolved_ids(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_upsert_entry")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(
                date="2024-01-10",
                description="Worked on feature X",
                hours=4.5,
                project_id=42,
                label_id=7,
            )

        mock_timelogs.resolve_project_id.assert_awaited_once_with("erp-token-abc", 42, None)
        mock_timelogs.resolve_label_id.assert_awaited_once_with("erp-token-abc", 7, None)
        mock_timelogs.create_or_update_log.assert_awaited_once_with(
            "erp-token-abc",
            date_str="2024-01-10",
            project_id=42,
            description="Worked on feature X",
            hours=4.5,
            label_id=7,
        )
        assert result["status"] == "success"

    async def test_resolves_by_name(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_upsert_entry")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            await fn(
                date="2024-01-10",
                description="task",
                hours=8,
                project_name="My Project",
                label_name="Coding",
            )

        mock_timelogs.resolve_project_id.assert_awaited_once_with(
            "erp-token-abc", None, "My Project"
        )
        mock_timelogs.resolve_label_id.assert_awaited_once_with(
            "erp-token-abc", None, "Coding"
        )

    async def test_audit_log_emitted(
        self,
        mock_timelogs: AsyncMock,
        mock_registry: AsyncMock,
        valid_token: AccessToken,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fn = _get_tool_fn("timelogs_upsert_entry")
        set_registry(mock_registry)
        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token):
                await fn(
                    date="2024-01-10",
                    description="task",
                    hours=8,
                    project_id=42,
                )

        assert any(
            "WRITE_OP" in r.message and "timelogs_upsert_entry" in r.message
            for r in caplog.records
        )

    async def test_rejects_oversized_description(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_upsert_entry")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Description too long"):
                await fn(
                    date="2024-01-10",
                    description="x" * 5001,
                    hours=8,
                    project_id=42,
                )

    async def test_rejects_zero_hours(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_upsert_entry")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="hours must be between"):
                await fn(
                    date="2024-01-10", description="task", hours=0, project_id=42,
                )

    async def test_rejects_negative_hours(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_upsert_entry")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="hours must be between"):
                await fn(
                    date="2024-01-10", description="task", hours=-1, project_id=42,
                )

    async def test_rejects_over_24_hours(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_upsert_entry")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="hours must be between"):
                await fn(
                    date="2024-01-10", description="task", hours=25, project_id=42,
                )

    async def test_unexpected_error_no_stack_trace(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        """SEC-04: ToolError message must not contain stack traces."""
        fn = _get_tool_fn("timelogs_upsert_entry")
        mock_timelogs.create_or_update_log.side_effect = RuntimeError("internal DB error")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Failed to create or update log") as exc_info:
                await fn(
                    date="2024-01-10",
                    description="task",
                    hours=8,
                    project_id=42,
                )
        # Ensure the error message does NOT contain the internal error details
        assert "internal DB error" not in str(exc_info.value)
        assert "Traceback" not in str(exc_info.value)


class TestTimelogsDeleteEntry:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_delete_entry")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(
                date="2024-01-10",
                description="task to delete",
                project_id=42,
            )

        mock_timelogs.resolve_project_id.assert_awaited_once_with("erp-token-abc", 42, None)
        mock_timelogs.delete_log.assert_awaited_once_with(
            "erp-token-abc",
            date_str="2024-01-10",
            project_id=42,
            description="task to delete",
        )
        assert result["status"] == "success"

    async def test_rejects_oversized_description(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_delete_entry")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Description too long"):
                await fn(
                    date="2024-01-10",
                    description="x" * 5001,
                    project_id=42,
                )

    async def test_audit_log_emitted(
        self,
        mock_timelogs: AsyncMock,
        mock_registry: AsyncMock,
        valid_token: AccessToken,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fn = _get_tool_fn("timelogs_delete_entry")
        set_registry(mock_registry)
        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token):
                await fn(date="2024-01-10", description="x", project_id=42)

        assert any(
            "WRITE_OP" in r.message and "timelogs_delete_entry" in r.message
            for r in caplog.records
        )


class TestTimelogsCompleteWeek:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_complete_week")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(week_starting="2024-01-08")

        mock_timelogs.complete_week_log.assert_awaited_once_with(
            "erp-token-abc", "2024-01-08", save_draft=False
        )
        assert result["status"] == "success"

    async def test_save_draft_mode(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_complete_week")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            await fn(week_starting="2024-01-08", save_draft=True)

        mock_timelogs.complete_week_log.assert_awaited_once_with(
            "erp-token-abc", "2024-01-08", save_draft=True
        )

    async def test_audit_log_emitted(
        self,
        mock_timelogs: AsyncMock,
        mock_registry: AsyncMock,
        valid_token: AccessToken,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fn = _get_tool_fn("timelogs_complete_week")
        set_registry(mock_registry)
        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token):
                await fn(week_starting="2024-01-08")

        assert any(
            "WRITE_OP" in r.message and "timelogs_complete_week" in r.message
            for r in caplog.records
        )


class TestTimelogsFillDays:
    async def test_rejects_oversized_description(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_fill_days")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Description too long"):
                await fn(
                    start_date="2024-01-08",
                    end_date="2024-01-12",
                    description="x" * 5001,
                    project_id=42,
                )

    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_fill_days")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(
                start_date="2024-01-08",
                end_date="2024-01-12",
                description="Sprint work",
                hours_per_day=8.0,
                project_id=42,
                skip_weekends=True,
            )

        mock_timelogs.fill_logs_for_days.assert_awaited_once_with(
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
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        """SEC-05: fill_logs_for_days capped at 31 days."""
        fn = _get_tool_fn("timelogs_fill_days")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="exceeding the maximum of 31"):
                await fn(
                    start_date="2024-01-01",
                    end_date="2024-03-01",  # 61 days
                    description="bulk",
                    project_id=42,
                )

        # ERP client methods should NOT have been called
        mock_timelogs.fill_logs_for_days.assert_not_awaited()

    async def test_rejects_end_before_start(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_fill_days")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="on or after"):
                await fn(
                    start_date="2024-01-31",
                    end_date="2024-01-01",
                    description="backwards",
                    project_id=42,
                )

    async def test_exactly_31_days_allowed(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_fill_days")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(
                start_date="2024-01-01",
                end_date="2024-01-31",  # exactly 31 days
                description="full month",
                project_id=42,
            )

        assert result["status"] == "success"

    async def test_32_days_rejected(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_fill_days")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="exceeding the maximum of 31"):
                await fn(
                    start_date="2024-01-01",
                    end_date="2024-02-01",  # 32 days
                    description="one too many",
                    project_id=42,
                )

    async def test_invalid_date_format(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_fill_days")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Invalid date format"):
                await fn(
                    start_date="not-a-date",
                    end_date="2024-01-10",
                    description="bad",
                    project_id=42,
                )

    async def test_audit_log_emitted(
        self,
        mock_timelogs: AsyncMock,
        mock_registry: AsyncMock,
        valid_token: AccessToken,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fn = _get_tool_fn("timelogs_fill_days")
        set_registry(mock_registry)
        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token):
                await fn(
                    start_date="2024-01-08",
                    end_date="2024-01-10",
                    description="work",
                    project_id=42,
                )

        assert any(
            "WRITE_OP" in r.message and "timelogs_fill_days" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Diagnostic tool tests
# ---------------------------------------------------------------------------


class TestTimelogsCheckWeekProject:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_check_week_project")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(date="2024-01-10", project_id=42)

        mock_timelogs.resolve_project_id.assert_awaited_once_with("erp-token-abc", 42, None)
        mock_timelogs.check_person_week_project_exists.assert_awaited_once_with(
            "erp-token-abc", "2024-01-10", 42
        )
        assert result["exists"] is True

    async def test_resolves_by_name(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_check_week_project")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            await fn(date="2024-01-10", project_name="My Project")

        mock_timelogs.resolve_project_id.assert_awaited_once_with(
            "erp-token-abc", None, "My Project"
        )


# ---------------------------------------------------------------------------
# SEC-04: No stack traces in ToolError
# ---------------------------------------------------------------------------


class TestNoStackTraces:
    """SEC-04: Verify unexpected errors produce generic messages, not tracebacks."""

    async def test_timelogs_list_projects_generic_error(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_list_projects")
        mock_timelogs.get_active_projects.side_effect = RuntimeError("segfault simulation")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError) as exc_info:
                await fn()

        msg = str(exc_info.value)
        assert "segfault" not in msg
        assert "Traceback" not in msg

    async def test_timelogs_delete_entry_generic_error(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_delete_entry")
        mock_timelogs.delete_log.side_effect = RuntimeError("db connection lost")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError) as exc_info:
                await fn(date="2024-01-10", description="x", project_id=42)

        msg = str(exc_info.value)
        assert "db connection" not in msg

    async def test_timelogs_fill_days_generic_error(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_fill_days")
        mock_timelogs.fill_logs_for_days.side_effect = RuntimeError("timeout")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError) as exc_info:
                await fn(
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
    """PermissionError and ValueError from clients should surface as ToolError."""

    async def test_permission_error_surfaces(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_get_week")
        mock_timelogs.get_week_logs.side_effect = PermissionError("Token expired")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Token expired"):
                await fn(week_starting="2024-01-08")

    async def test_value_error_surfaces(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("timelogs_upsert_entry")
        mock_timelogs.resolve_project_id.side_effect = ValueError(
            "Project 'Nonexistent' not found"
        )
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="not found"):
                await fn(
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
            "timelogs_upsert_entry",
            {"date": "2024-01-10", "description": "t", "hours": 8, "project_id": 42},
        ),
        ("timelogs_delete_entry", {"date": "2024-01-10", "description": "t", "project_id": 42}),
        ("timelogs_complete_week", {"week_starting": "2024-01-08"}),
        (
            "timelogs_fill_days",
            {
                "start_date": "2024-01-08",
                "end_date": "2024-01-10",
                "description": "work",
                "project_id": 42,
            },
        ),
    ]

    @pytest.mark.parametrize(
        ("tool_name", "kwargs"), WRITE_TOOLS, ids=[t[0] for t in WRITE_TOOLS]
    )
    async def test_write_tool_emits_audit_log(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        mock_timelogs: AsyncMock,
        mock_registry: AsyncMock,
        valid_token: AccessToken,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        tool_fn = _get_tool_fn(tool_name)
        set_registry(mock_registry)
        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token):
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
        from tests.test_security import _discover_write_tool_names

        discovered = _discover_write_tool_names()
        covered = {name for name, _ in self.WRITE_TOOLS}
        missing = discovered - covered
        assert not missing, (
            f"Write tools missing from TestAuditLogCoverage.WRITE_TOOLS: {missing}. "
            f"Add test entries for these tools to ensure audit logging is verified."
        )
