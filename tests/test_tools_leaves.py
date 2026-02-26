"""Unit tests for leaves MCP tools (tools/leaves.py).

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
def mock_leaves() -> AsyncMock:
    """Return a fully mocked ``LeavesClient`` instance."""
    client = AsyncMock()
    client.get_choices.return_value = {"status": "success", "data": []}
    client.get_summary.return_value = {
        "status": "success",
        "data": {"total": 20, "used": 5},
    }
    client.get_month_leaves.return_value = {"status": "success", "data": []}
    client.get_holidays.return_value = {"status": "success", "data": []}
    client.list_mine.return_value = {"status": "success", "data": []}
    client.list_team.return_value = {"status": "success", "data": []}
    client.list_encashments.return_value = {"status": "success", "data": []}
    client.apply.return_value = {"status": "success", "data": {"id": 100}}
    client.cancel.return_value = {"status": "success"}
    client.create_encashment.return_value = {"status": "success", "data": {"id": 10}}
    return client


@pytest.fixture
def mock_timelogs() -> AsyncMock:
    """Return a minimal mocked ``TimelogsClient`` instance."""
    return AsyncMock()


@pytest.fixture
def mock_registry(mock_leaves: AsyncMock, mock_timelogs: AsyncMock) -> AsyncMock:
    """Return a fully mocked ``ERPClientRegistry`` instance."""
    registry = AsyncMock()
    registry.leaves = mock_leaves
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
# Read tool tests
# ---------------------------------------------------------------------------


class TestLeavesGetChoices:
    async def test_calls_client(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_get_choices")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn()

        assert result["status"] == "success"
        mock_leaves.get_choices.assert_awaited_once_with("erp-token-abc")


class TestLeavesGetSummary:
    async def test_calls_client_with_selected_year(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_get_summary")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(selected_year=2026)

        assert result["status"] == "success"
        mock_leaves.get_summary.assert_awaited_once_with("erp-token-abc", 2026)


class TestLeavesListMonth:
    async def test_calls_client(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_list_month")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(year=2024, month=6)

        assert result["status"] == "success"
        assert result["data"]["year"] == 2024
        assert result["data"]["month"] == 6
        mock_leaves.get_month_leaves.assert_awaited_once_with("erp-token-abc", 2024, 6)
        mock_leaves.get_holidays.assert_awaited_once_with("erp-token-abc", 2024, 6)

    async def test_both_fail_raises(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        """When both leaves and holidays fail, tool should raise ToolError."""
        mock_leaves.get_month_leaves.return_value = {
            "status": "error",
            "message": "leaves failed",
        }
        mock_leaves.get_holidays.return_value = {
            "status": "error",
            "message": "holidays failed",
        }
        fn = _get_tool_fn("leaves_list_month")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="leaves failed"):
                await fn(year=2024, month=6)

    async def test_partial_failure_returns_data(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        """When one call fails but the other succeeds, return available data."""
        mock_leaves.get_month_leaves.return_value = {
            "status": "error",
            "message": "leaves failed",
        }
        mock_leaves.get_holidays.return_value = {
            "status": "success",
            "data": [{"name": "Eid"}],
        }
        fn = _get_tool_fn("leaves_list_month")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(year=2024, month=6)

        assert result["status"] == "success"
        assert result["data"]["leaves"] == []
        assert result["data"]["holidays"] == [{"name": "Eid"}]


class TestLeavesListMine:
    async def test_calls_client(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_list_mine")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(year=2024, month=6)

        assert result["status"] == "success"
        mock_leaves.list_mine.assert_awaited_once_with("erp-token-abc", 2024, 6)


class TestLeavesListTeam:
    async def test_calls_client(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_list_team")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn()

        assert result["status"] == "success"
        mock_leaves.list_team.assert_awaited_once_with("erp-token-abc")


class TestLeavesListEncashments:
    async def test_calls_client(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_list_encashments")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn()

        assert result["status"] == "success"
        mock_leaves.list_encashments.assert_awaited_once_with("erp-token-abc")


# ---------------------------------------------------------------------------
# Write tool tests
# ---------------------------------------------------------------------------


class TestLeavesApply:
    async def test_calls_client(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_apply")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(
                leave_type=1,
                start_date="2024-06-10",
                end_date="2024-06-12",
                reason="Family event",
            )

        assert result["status"] == "success"
        mock_leaves.apply.assert_awaited_once_with(
            "erp-token-abc",
            leave_type=1,
            start_date="2024-06-10",
            end_date="2024-06-12",
            reason="Family event",
            half_day=False,
            half_day_period=None,
        )

    async def test_rejects_oversized_reason(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_apply")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Reason too long"):
                await fn(
                    leave_type=1,
                    start_date="2024-06-10",
                    end_date="2024-06-12",
                    reason="x" * 2001,
                )

    async def test_rejects_end_before_start(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_apply")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="on or after"):
                await fn(
                    leave_type=1,
                    start_date="2024-06-12",
                    end_date="2024-06-10",
                    reason="test",
                )

    async def test_rejects_over_max_days(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_apply")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="exceeding the maximum of 90"):
                await fn(
                    leave_type=1,
                    start_date="2024-01-01",
                    end_date="2024-04-01",  # 92 days
                    reason="sabbatical",
                )

    async def test_exactly_90_days_allowed(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_apply")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(
                leave_type=1,
                start_date="2024-01-01",
                end_date="2024-03-30",  # exactly 90 days
                reason="maternity leave",
            )
        assert result["status"] == "success"

    async def test_audit_log_emitted(
        self,
        mock_leaves: AsyncMock,
        mock_registry: AsyncMock,
        valid_token: AccessToken,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fn = _get_tool_fn("leaves_apply")
        set_registry(mock_registry)
        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token):
                await fn(
                    leave_type=1,
                    start_date="2024-06-10",
                    end_date="2024-06-12",
                    reason="test",
                )

        assert any("WRITE_OP" in r.message and "leaves_apply" in r.message for r in caplog.records)


class TestLeavesCancel:
    async def test_calls_client(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_cancel")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(leave_id=42)

        assert result["status"] == "success"
        mock_leaves.cancel.assert_awaited_once_with("erp-token-abc", leave_id=42)

    async def test_rejects_zero_leave_id(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_cancel")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="positive integer"):
                await fn(leave_id=0)

    async def test_rejects_negative_leave_id(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_cancel")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="positive integer"):
                await fn(leave_id=-1)

    async def test_audit_log_emitted(
        self,
        mock_leaves: AsyncMock,
        mock_registry: AsyncMock,
        valid_token: AccessToken,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fn = _get_tool_fn("leaves_cancel")
        set_registry(mock_registry)
        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token):
                await fn(leave_id=42)

        assert any(
            "WRITE_OP" in r.message and "leaves_cancel" in r.message for r in caplog.records
        )


class TestLeavesCreateEncashment:
    async def test_calls_client(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_create_encashment")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            result = await fn(leave_type=1, days=5)

        assert result["status"] == "success"
        mock_leaves.create_encashment.assert_awaited_once_with(
            "erp-token-abc", leave_type=1, days=5
        )

    async def test_rejects_zero_days(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_create_encashment")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="positive integer"):
                await fn(leave_type=1, days=0)

    async def test_rejects_negative_days(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_create_encashment")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="positive integer"):
                await fn(leave_type=1, days=-3)

    async def test_audit_log_emitted(
        self,
        mock_leaves: AsyncMock,
        mock_registry: AsyncMock,
        valid_token: AccessToken,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fn = _get_tool_fn("leaves_create_encashment")
        set_registry(mock_registry)
        with caplog.at_level(logging.INFO, logger="erp_mcp.server"):
            with _patch_token(valid_token):
                await fn(leave_type=1, days=5)

        assert any(
            "WRITE_OP" in r.message and "leaves_create_encashment" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# SEC-04: No stack traces in ToolError
# ---------------------------------------------------------------------------


class TestNoStackTraces:
    """SEC-04: Verify unexpected errors produce generic messages, not tracebacks."""

    async def test_leaves_get_choices_generic_error(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_get_choices")
        mock_leaves.get_choices.side_effect = RuntimeError("segfault simulation")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError) as exc_info:
                await fn()

        msg = str(exc_info.value)
        assert "segfault" not in msg
        assert "Traceback" not in msg

    async def test_leaves_apply_generic_error(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_apply")
        mock_leaves.apply.side_effect = RuntimeError("internal DB error")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Failed to apply for leave") as exc_info:
                await fn(
                    leave_type=1,
                    start_date="2024-06-10",
                    end_date="2024-06-12",
                    reason="test",
                )
        assert "internal DB error" not in str(exc_info.value)

    async def test_leaves_cancel_generic_error(
        self, mock_leaves: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        fn = _get_tool_fn("leaves_cancel")
        mock_leaves.cancel.side_effect = RuntimeError("connection lost")
        set_registry(mock_registry)
        with _patch_token(valid_token):
            with pytest.raises(ToolError) as exc_info:
                await fn(leave_id=42)

        msg = str(exc_info.value)
        assert "connection lost" not in msg


# ---------------------------------------------------------------------------
# Write tool audit log coverage
# ---------------------------------------------------------------------------


class TestAuditLogCoverage:
    """Ensure ALL leaves write tools emit structured audit log entries."""

    WRITE_TOOLS = [
        (
            "leaves_apply",
            {
                "leave_type": 1,
                "start_date": "2024-06-10",
                "end_date": "2024-06-12",
                "reason": "test",
            },
        ),
        ("leaves_cancel", {"leave_id": 42}),
        ("leaves_create_encashment", {"leave_type": 1, "days": 5}),
    ]

    @pytest.mark.parametrize(("tool_name", "kwargs"), WRITE_TOOLS, ids=[t[0] for t in WRITE_TOOLS])
    async def test_write_tool_emits_audit_log(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        mock_leaves: AsyncMock,
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
            r
            for r in caplog.records
            if "WRITE_OP" in r.message and f"tool={tool_name}" in r.message
        ]
        assert len(audit_records) >= 1, (
            f"No WRITE_OP audit log found for {tool_name}. "
            f"Records: {[r.message for r in caplog.records]}"
        )

    def test_all_write_tools_covered(self) -> None:
        """Guard: every leaves @mcp.tool that emits WRITE_OP must appear in WRITE_TOOLS."""
        from tests.test_security import _discover_write_tool_names

        discovered = {n for n in _discover_write_tool_names() if n.startswith("leaves_")}
        covered = {name for name, _ in self.WRITE_TOOLS}
        missing = discovered - covered
        assert not missing, (
            f"Write tools missing from TestAuditLogCoverage.WRITE_TOOLS: {missing}. "
            f"Add test entries for these tools to ensure audit logging is verified."
        )
