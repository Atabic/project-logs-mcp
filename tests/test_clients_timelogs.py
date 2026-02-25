"""Tests for clients/timelogs.py -- TimelogsClient HTTP-level tests.

Migrated from test_erp_client.py tests that exercise data-query methods,
project/label resolution, and write operations via respx-mocked HTTP calls.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
import respx

from clients._base import BaseERPClient
from clients.timelogs import TimelogsClient

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
def timelogs_client(base_client: BaseERPClient) -> TimelogsClient:
    return TimelogsClient(base_client)


# =========================================================================
# resolve_project_id / resolve_label_id tests
# =========================================================================


class TestResolveProjectId:
    """Tests for project name -> ID resolution."""

    @respx.mock
    async def test_returns_project_id_directly(self, timelogs_client: TimelogsClient) -> None:
        result = await timelogs_client.resolve_project_id("tok", project_id=42)
        assert result == 42

    @respx.mock
    async def test_case_insensitive_partial_match(self, timelogs_client: TimelogsClient) -> None:
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 10, "team": "Alpha Project"},
                    {"id": 20, "team": "Beta Testing Team"},
                    {"id": 30, "team": "Gamma Internal"},
                ],
            )
        )
        # Partial, case-insensitive match.
        result = await timelogs_client.resolve_project_id("tok", project_name="beta testing")
        assert result == 20

    @respx.mock
    async def test_partial_match_substring(self, timelogs_client: TimelogsClient) -> None:
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 10, "team": "Alpha Project"},
                    {"id": 20, "team": "Beta Testing Team"},
                ],
            )
        )
        result = await timelogs_client.resolve_project_id("tok", project_name="alpha")
        assert result == 10

    @respx.mock
    async def test_not_found_raises_valueerror(self, timelogs_client: TimelogsClient) -> None:
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(
                200,
                json=[{"id": 1, "team": "Only Project"}],
            )
        )
        with pytest.raises(ValueError, match="not found"):
            await timelogs_client.resolve_project_id("tok", project_name="nonexistent")

    async def test_no_id_no_name_raises(self, timelogs_client: TimelogsClient) -> None:
        with pytest.raises(ValueError, match="Either"):
            await timelogs_client.resolve_project_id("tok")

    @respx.mock
    async def test_exact_match_preferred_over_substring(
        self, timelogs_client: TimelogsClient
    ) -> None:
        """Exact match should be returned even if substring matches exist."""
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 10, "team": "Alpha"},
                    {"id": 20, "team": "Alpha Project Extended"},
                    {"id": 30, "team": "Another Alpha Project"},
                ],
            )
        )
        result = await timelogs_client.resolve_project_id("tok", project_name="Alpha")
        assert result == 10  # exact match, not substring

    @respx.mock
    async def test_ambiguous_substring_raises(self, timelogs_client: TimelogsClient) -> None:
        """Multiple substring matches should raise ValueError with project names."""
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 10, "team": "Alpha Project"},
                    {"id": 20, "team": "Alpha Testing"},
                    {"id": 30, "team": "Beta Project"},
                ],
            )
        )
        with pytest.raises(ValueError, match="Ambiguous"):
            await timelogs_client.resolve_project_id("tok", project_name="Alpha")


class TestResolveLabelId:
    """Tests for label name -> ID resolution."""

    @respx.mock
    async def test_returns_label_id_directly(self, timelogs_client: TimelogsClient) -> None:
        result = await timelogs_client.resolve_label_id("tok", label_id=5)
        assert result == 5

    @respx.mock
    async def test_case_insensitive_exact_match(self, timelogs_client: TimelogsClient) -> None:
        respx.get(f"{BASE_URL}/project-logs/log_labels/").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 1, "name": "Coding"},
                    {"id": 2, "name": "Review"},
                    {"id": 3, "name": "Meeting"},
                ],
            )
        )
        result = await timelogs_client.resolve_label_id("tok", label_name="coding")
        assert result == 1

    @respx.mock
    async def test_no_match_raises_valueerror(self, timelogs_client: TimelogsClient) -> None:
        respx.get(f"{BASE_URL}/project-logs/log_labels/").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "name": "Coding"}])
        )
        with pytest.raises(ValueError, match="not found"):
            await timelogs_client.resolve_label_id("tok", label_name="nonexistent")

    async def test_no_id_no_name_returns_none(self, timelogs_client: TimelogsClient) -> None:
        result = await timelogs_client.resolve_label_id("tok")
        assert result is None


# =========================================================================
# SEC-01: No email parameter on data-query methods
# =========================================================================


class TestSEC01NoEmailParam:
    """Verify that TimelogsClient data-query methods do NOT accept an ``email`` parameter."""

    _DATA_METHODS = [
        "get_active_projects",
        "get_log_labels",
        "get_week_logs",
        "get_day_logs",
        "get_logs_for_date_range",
        "get_month_logs",
        "create_or_update_log",
        "delete_log",
        "complete_week_log",
        "fill_logs_for_days",
        "check_person_week_project_exists",
    ]

    @pytest.mark.parametrize("method_name", _DATA_METHODS)
    def test_no_email_in_signature(self, method_name: str) -> None:
        method = getattr(TimelogsClient, method_name)
        sig = inspect.signature(method)
        param_names = set(sig.parameters.keys())
        assert "email" not in param_names, (
            f"{method_name} must not accept an 'email' parameter (SEC-01)"
        )


# =========================================================================
# SEC-05: fill_logs_for_days 31-day cap
# =========================================================================


class TestFillLogsDayCap:
    """SEC-05: fill_logs_for_days must cap at 31 days."""

    async def test_32_days_rejected(self, timelogs_client: TimelogsClient) -> None:
        result = await timelogs_client.fill_logs_for_days(
            token="tok",
            start_date="2026-01-01",
            end_date="2026-02-01",  # 32 days
            project_id=1,
            description="work",
        )
        assert result["status"] == "error"
        assert "31" in result["message"]

    @respx.mock
    async def test_31_days_allowed(self, timelogs_client: TimelogsClient) -> None:
        """31 days should not be rejected by the cap."""
        # Mock the endpoints that fill_logs_for_days will call internally.
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "team": "TestProj"}])
        )
        respx.get(f"{BASE_URL}/project-logs/person/list/").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{BASE_URL}/project-logs/person/person-week-log-from-slack/").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        result = await timelogs_client.fill_logs_for_days(
            token="tok",
            start_date="2026-01-01",
            end_date="2026-01-31",  # exactly 31 days
            project_id=1,
            description="work",
        )
        # Should not be rejected by the cap (may fail for other reasons
        # in this mock, but the cap should NOT be the error).
        assert "exceeds" not in result.get("message", "")

    async def test_reversed_dates_rejected(self, timelogs_client: TimelogsClient) -> None:
        result = await timelogs_client.fill_logs_for_days(
            token="tok",
            start_date="2026-02-01",
            end_date="2026-01-01",
            project_id=1,
            description="work",
        )
        assert result["status"] == "error"


# =========================================================================
# Integration-style tests for create_or_update_log
# =========================================================================


class TestCreateOrUpdateLog:
    """Tests for the Save API vs Slack endpoint fallback logic."""

    @respx.mock
    async def test_slack_fallback_when_no_week_log(
        self, timelogs_client: TimelogsClient
    ) -> None:
        """When no week log exists, should POST to Slack endpoint."""
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(200, json=[{"id": 42, "team": "My Project"}])
        )
        respx.get(f"{BASE_URL}/project-logs/person/list/").mock(
            return_value=httpx.Response(200, json=[])
        )
        slack_route = respx.post(
            f"{BASE_URL}/project-logs/person/person-week-log-from-slack/"
        ).mock(return_value=httpx.Response(200, json={"created": True}))

        result = await timelogs_client.create_or_update_log(
            token="tok",
            date_str="2026-01-07",
            project_id=42,
            description="Did some work",
            hours=8.0,
        )
        assert result["status"] == "success"
        assert slack_route.called

    @respx.mock
    async def test_save_api_when_week_log_exists(
        self, timelogs_client: TimelogsClient
    ) -> None:
        """When week log exists, should PATCH via Save API."""
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(200, json=[{"id": 42, "team": "My Project"}])
        )
        # person/list returns a week log for the right week.
        respx.get(f"{BASE_URL}/project-logs/person/list/").mock(
            return_value=httpx.Response(
                200,
                json=[{"id": 999, "week_starting": "2026-01-05"}],
            )
        )
        # GET the detailed week log.
        respx.get(f"{BASE_URL}/project-logs/person/get/999/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 999,
                    "modified_at": "2026-01-06T00:00:00Z",
                    "projects": [
                        {
                            "team": "My Project",
                            "subteam": "My Project",
                            "tasks": [],
                        }
                    ],
                },
            )
        )
        save_route = respx.patch(
            f"{BASE_URL}/project-logs/person/person-week-log/save/999/"
        ).mock(return_value=httpx.Response(200, json={"saved": True}))

        result = await timelogs_client.create_or_update_log(
            token="tok",
            date_str="2026-01-07",
            project_id=42,
            description="Did some work",
            hours=2.5,
        )
        assert result["status"] == "success"
        assert save_route.called

    @respx.mock
    async def test_project_not_in_active_projects(
        self, timelogs_client: TimelogsClient
    ) -> None:
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "team": "Other"}])
        )
        result = await timelogs_client.create_or_update_log(
            token="tok",
            date_str="2026-01-07",
            project_id=999,
            description="work",
            hours=8.0,
        )
        assert result["status"] == "error"
        assert "not found" in result["message"]


class TestSaveApiUpsertNewDay:
    """Test that _save_api_upsert correctly appends a new day to an existing task."""

    @respx.mock
    async def test_appends_new_day_to_existing_task(
        self, timelogs_client: TimelogsClient
    ) -> None:
        """When a task already exists but not the target date, a new day should be appended."""
        week_log_id = 100
        existing_week_log = {
            "id": week_log_id,
            "modified_at": "2026-01-10T12:00:00Z",
            "projects": [
                {
                    "id": 10,
                    "team": "Test Project",
                    "subteam": "Test Project",
                    "tasks": [
                        {
                            "description": "Existing task",
                            "days": [
                                {
                                    "date": "2026-01-05",
                                    "hours": 8,
                                    "minutes": 0,
                                    "decimal_hours": 8.0,
                                    "label": 66,
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        # Mock GET for week log detail
        respx.get(f"{BASE_URL}/project-logs/person/get/{week_log_id}/").mock(
            return_value=httpx.Response(200, json=existing_week_log)
        )

        # Mock PATCH for save
        save_route = respx.patch(
            f"{BASE_URL}/project-logs/person/person-week-log/save/{week_log_id}/"
        ).mock(return_value=httpx.Response(200, json={"status": "ok"}))

        result = await timelogs_client._save_api_upsert(
            token="tok",
            week_log_id=week_log_id,
            date_str="2026-01-06",  # New day, same task description
            active_team_name="Test Project",
            description="Existing task",
            hours=4.0,
            effective_label=66,
            monday_str="2026-01-05",
        )

        assert result["status"] == "success"
        # Verify the PATCH payload includes both the original and new day
        sent_data = json.loads(save_route.calls.last.request.content)
        task = sent_data["projects"][0]["tasks"][0]
        assert len(task["days"]) == 2
        new_day = task["days"][1]
        assert new_day["date"] == "2026-01-06"
        assert new_day["hours"] == 4
        assert new_day["minutes"] == 0


class TestSaveApiUpsert:
    """Tests for the _save_api_upsert private method (Save API path)."""

    @respx.mock
    async def test_creates_new_task_in_existing_project(
        self, timelogs_client: TimelogsClient
    ) -> None:
        """When project exists in week log but task is new, it should be appended."""
        respx.get(f"{BASE_URL}/project-logs/person/get/999/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 999,
                    "modified_at": "2026-01-06T00:00:00Z",
                    "projects": [
                        {
                            "team": "My Project",
                            "subteam": "My Project",
                            "tasks": [],
                        }
                    ],
                },
            )
        )
        save_route = respx.patch(
            f"{BASE_URL}/project-logs/person/person-week-log/save/999/"
        ).mock(return_value=httpx.Response(200, json={"saved": True}))

        result = await timelogs_client._save_api_upsert(
            token="tok",
            week_log_id=999,
            date_str="2026-01-07",
            active_team_name="My Project",
            description="New task",
            hours=4.0,
            effective_label=66,
            monday_str="2026-01-05",
        )
        assert result["status"] == "success"
        assert save_route.called

    @respx.mock
    async def test_updates_existing_task_day(self, timelogs_client: TimelogsClient) -> None:
        """When task and day exist, it should update hours in place."""
        respx.get(f"{BASE_URL}/project-logs/person/get/999/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 999,
                    "modified_at": "2026-01-06T00:00:00Z",
                    "projects": [
                        {
                            "team": "My Project",
                            "subteam": "My Project",
                            "tasks": [
                                {
                                    "description": "Existing task",
                                    "days": [
                                        {
                                            "date": "2026-01-07",
                                            "hours": 4,
                                            "minutes": 0,
                                            "decimal_hours": 4.0,
                                            "label": 66,
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
            )
        )
        save_route = respx.patch(
            f"{BASE_URL}/project-logs/person/person-week-log/save/999/"
        ).mock(return_value=httpx.Response(200, json={"saved": True}))

        result = await timelogs_client._save_api_upsert(
            token="tok",
            week_log_id=999,
            date_str="2026-01-07",
            active_team_name="My Project",
            description="Existing task",
            hours=6.0,
            effective_label=66,
            monday_str="2026-01-05",
        )
        assert result["status"] == "success"
        assert save_route.called

    @respx.mock
    async def test_missing_modified_at_returns_error(
        self, timelogs_client: TimelogsClient
    ) -> None:
        respx.get(f"{BASE_URL}/project-logs/person/get/999/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 999,
                    "projects": [],
                },
            )
        )
        result = await timelogs_client._save_api_upsert(
            token="tok",
            week_log_id=999,
            date_str="2026-01-07",
            active_team_name="My Project",
            description="task",
            hours=4.0,
            effective_label=66,
            monday_str="2026-01-05",
        )
        assert result["status"] == "error"
        assert "modified_at" in result["message"]


# =========================================================================
# delete_log tests
# =========================================================================


class TestDeleteLog:
    @respx.mock
    async def test_delete_removes_day_entry(self, timelogs_client: TimelogsClient) -> None:
        respx.get(f"{BASE_URL}/project-logs/person/list/").mock(
            return_value=httpx.Response(
                200, json=[{"id": 100, "week_starting": "2026-01-05"}]
            )
        )
        respx.get(f"{BASE_URL}/project-logs/person/get/100/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 100,
                    "modified_at": "2026-01-06T00:00:00Z",
                    "projects": [
                        {
                            "team": "Proj",
                            "subteam": "Proj",
                            "tasks": [
                                {
                                    "description": "Some task",
                                    "days": [
                                        {"date": "2026-01-07", "hours": 8, "minutes": 0},
                                    ],
                                }
                            ],
                        }
                    ],
                },
            )
        )
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(200, json=[{"id": 42, "team": "Proj"}])
        )
        respx.patch(f"{BASE_URL}/project-logs/person/person-week-log/save/100/").mock(
            return_value=httpx.Response(200, json={"saved": True})
        )

        result = await timelogs_client.delete_log(
            token="tok",
            date_str="2026-01-07",
            project_id=42,
            description="Some task",
        )
        assert result["status"] == "success"

    @respx.mock
    async def test_delete_preserves_other_days(self, timelogs_client: TimelogsClient) -> None:
        """Deleting a day entry should keep other days for the same task."""
        respx.get(f"{BASE_URL}/project-logs/person/list/").mock(
            return_value=httpx.Response(
                200, json=[{"id": 100, "week_starting": "2026-01-05"}]
            )
        )
        respx.get(f"{BASE_URL}/project-logs/person/get/100/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 100,
                    "modified_at": "2026-01-06T00:00:00Z",
                    "projects": [
                        {
                            "team": "Proj",
                            "subteam": "Proj",
                            "tasks": [
                                {
                                    "description": "Some task",
                                    "days": [
                                        {"date": "2026-01-07", "hours": 8, "minutes": 0},
                                        {"date": "2026-01-08", "hours": 4, "minutes": 0},
                                    ],
                                }
                            ],
                        }
                    ],
                },
            )
        )
        respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
            return_value=httpx.Response(200, json=[{"id": 42, "team": "Proj"}])
        )
        save_route = respx.patch(
            f"{BASE_URL}/project-logs/person/person-week-log/save/100/"
        ).mock(return_value=httpx.Response(200, json={"saved": True}))

        result = await timelogs_client.delete_log(
            token="tok",
            date_str="2026-01-07",
            project_id=42,
            description="Some task",
        )
        assert result["status"] == "success"
        # Verify the PATCH payload still has the other day
        sent_body = json.loads(save_route.calls.last.request.content)
        remaining_days = sent_body["projects"][0]["tasks"][0]["days"]
        assert len(remaining_days) == 1
        assert remaining_days[0]["date"] == "2026-01-08"


# =========================================================================
# complete_week_log tests
# =========================================================================


class TestCompleteWeekLog:
    @respx.mock
    async def test_complete_patches_endpoint(self, timelogs_client: TimelogsClient) -> None:
        respx.get(f"{BASE_URL}/project-logs/person/list/").mock(
            return_value=httpx.Response(
                200, json=[{"id": 50, "week_starting": "2026-01-05"}]
            )
        )
        complete_route = respx.patch(
            f"{BASE_URL}/project-logs/person/person-week-log/complete/50/"
        ).mock(return_value=httpx.Response(200, json={"completed": True}))

        result = await timelogs_client.complete_week_log("tok", "2026-01-05")
        assert result["status"] == "success"
        assert complete_route.called

    @respx.mock
    async def test_complete_not_found(self, timelogs_client: TimelogsClient) -> None:
        respx.get(f"{BASE_URL}/project-logs/person/list/").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = await timelogs_client.complete_week_log("tok", "2026-01-05")
        assert result["status"] == "error"
        assert "not found" in result["message"]
