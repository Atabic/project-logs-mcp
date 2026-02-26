"""Tests for clients/leaves.py -- LeavesClient HTTP-level tests.

Uses respx for HTTP mocking, following the same patterns as test_clients_timelogs.py.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
import respx

from clients._base import BaseERPClient
from clients.leaves import LeavesClient

# =========================================================================
# Fixtures
# =========================================================================

BASE_URL = "https://erp.example.com/api/v1"
ALLOWED_DOMAIN = "arbisoft.com"


@pytest_asyncio.fixture
async def base_client() -> AsyncGenerator[BaseERPClient, None]:
    c = BaseERPClient(base_url=BASE_URL, allowed_domain=ALLOWED_DOMAIN)
    yield c
    await c.close()


@pytest.fixture
def leaves_client(base_client: BaseERPClient) -> LeavesClient:
    return LeavesClient(base_client)


# =========================================================================
# Read method tests
# =========================================================================


class TestGetChoices:
    @respx.mock
    async def test_returns_choices(self, leaves_client: LeavesClient) -> None:
        respx.get(f"{BASE_URL}/leaves/choices/get/").mock(
            return_value=httpx.Response(200, json={"leave_types": [{"id": 1, "name": "Annual"}]})
        )
        result = await leaves_client.get_choices("tok")
        assert result["status"] == "success"


class TestGetSummary:
    @respx.mock
    async def test_returns_summary(self, leaves_client: LeavesClient) -> None:
        route = respx.get(f"{BASE_URL}/leaves/leave_summary/get/").mock(
            return_value=httpx.Response(200, json={"total": 20, "used": 5})
        )
        result = await leaves_client.get_summary("tok", selected_year=2026)
        assert result["status"] == "success"
        assert route.calls.last.request.url.params["selected_year"] == "2026"

    @respx.mock
    async def test_returns_error_when_summary_fails(self, leaves_client: LeavesClient) -> None:
        respx.get(f"{BASE_URL}/leaves/leave_summary/get/").mock(
            return_value=httpx.Response(500, json={"detail": "server error"})
        )
        result = await leaves_client.get_summary("tok", selected_year=2026)
        assert result["status"] == "error"


class TestGetMonthLeaves:
    @respx.mock
    async def test_returns_month_leaves(self, leaves_client: LeavesClient) -> None:
        route = respx.get(f"{BASE_URL}/leaves/person/month_leaves/").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "date": "2024-06-10"}])
        )
        result = await leaves_client.get_month_leaves("tok", 2024, 6)
        assert result["status"] == "success"
        assert route.calls.last.request.url.params["year"] == "2024"
        assert route.calls.last.request.url.params["month"] == "6"


class TestGetHolidays:
    @respx.mock
    async def test_returns_holidays(self, leaves_client: LeavesClient) -> None:
        route = respx.get(f"{BASE_URL}/leaves/holiday_records/").mock(
            return_value=httpx.Response(200, json=[{"name": "Eid"}])
        )
        result = await leaves_client.get_holidays("tok", 2024, 6)
        assert result["status"] == "success"
        assert route.calls.last.request.url.params["year"] == "2024"


class TestListMine:
    @respx.mock
    async def test_returns_own_leaves(self, leaves_client: LeavesClient) -> None:
        respx.get(f"{BASE_URL}/leaves/list/").mock(
            return_value=httpx.Response(200, json=[{"id": 10}])
        )
        result = await leaves_client.list_mine("tok", 2024, 6)
        assert result["status"] == "success"


class TestListTeam:
    @respx.mock
    async def test_returns_team_leaves(self, leaves_client: LeavesClient) -> None:
        respx.get(f"{BASE_URL}/leaves/team_leaves/list/").mock(
            return_value=httpx.Response(200, json=[{"person": "Jane"}])
        )
        result = await leaves_client.list_team("tok")
        assert result["status"] == "success"


class TestListEncashments:
    @respx.mock
    async def test_returns_encashments(self, leaves_client: LeavesClient) -> None:
        respx.get(f"{BASE_URL}/leaves/person-leave-encashments/").mock(
            return_value=httpx.Response(200, json=[{"id": 5}])
        )
        result = await leaves_client.list_encashments("tok")
        assert result["status"] == "success"


# =========================================================================
# Write method tests
# =========================================================================


class TestApply:
    @respx.mock
    async def test_apply_leave(self, leaves_client: LeavesClient) -> None:
        route = respx.post(f"{BASE_URL}/leaves/request/apply/").mock(
            return_value=httpx.Response(200, json={"id": 100, "status_text": "Pending"})
        )
        result = await leaves_client.apply(
            "tok",
            leave_type=1,
            start_date="2024-06-10",
            end_date="2024-06-12",
            reason="Family event",
        )
        assert result["status"] == "success"
        assert route.called

    @respx.mock
    async def test_apply_with_half_day(self, leaves_client: LeavesClient) -> None:
        respx.post(f"{BASE_URL}/leaves/request/apply/").mock(
            return_value=httpx.Response(200, json={"id": 101})
        )
        result = await leaves_client.apply(
            "tok",
            leave_type=1,
            start_date="2024-06-10",
            end_date="2024-06-10",
            reason="Doctor appointment",
            half_day=True,
            half_day_period="first_half",
        )
        assert result["status"] == "success"


class TestCancel:
    @respx.mock
    async def test_cancel_leave(self, leaves_client: LeavesClient) -> None:
        route = respx.post(f"{BASE_URL}/leaves/delete_leave/42/").mock(
            return_value=httpx.Response(200, json={"deleted": True})
        )
        result = await leaves_client.cancel("tok", leave_id=42)
        assert result["status"] == "success"
        assert route.called


class TestCreateEncashment:
    @respx.mock
    async def test_create_encashment(self, leaves_client: LeavesClient) -> None:
        route = respx.post(f"{BASE_URL}/leaves/person-leave-encashments/").mock(
            return_value=httpx.Response(200, json={"id": 10, "days": 5})
        )
        result = await leaves_client.create_encashment("tok", leave_type=1, days=5)
        assert result["status"] == "success"
        assert route.called


# =========================================================================
# SEC-01: No email parameter on any client method
# =========================================================================


class TestSEC01NoEmailParam:
    """Verify that LeavesClient methods do NOT accept an ``email`` parameter."""

    _ALL_METHODS = [
        "get_choices",
        "get_summary",
        "get_month_leaves",
        "get_holidays",
        "list_mine",
        "list_team",
        "list_encashments",
        "apply",
        "cancel",
        "create_encashment",
    ]

    @pytest.mark.parametrize("method_name", _ALL_METHODS)
    def test_no_email_in_signature(self, method_name: str) -> None:
        method = getattr(LeavesClient, method_name)
        sig = inspect.signature(method)
        param_names = set(sig.parameters.keys())
        assert "email" not in param_names, (
            f"{method_name} must not accept an 'email' parameter (SEC-01)"
        )
