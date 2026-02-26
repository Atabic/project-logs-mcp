"""Domain client for ERP time-log operations.

Uses composition: holds a reference to :class:`BaseERPClient` for HTTP
transport and delegates all network I/O through ``self._base.request()``.
Static parsing helpers are called via ``BaseERPClient.<method>()``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from _constants import DEFAULT_LABEL_ID
from clients._base import BaseERPClient

__all__ = ["TimelogsClient"]


class TimelogsClient:
    """High-level operations on ERP time logs.

    All public methods take ``token: str`` as the first argument (SEC-01).
    """

    def __init__(self, base: BaseERPClient) -> None:
        self._base = base

    # -- read methods -------------------------------------------------------

    async def get_active_projects(self, token: str) -> dict[str, Any]:
        return await self._base.request("GET", "project-logs/person/active_project_list/", token)

    async def get_log_labels(self, token: str) -> dict[str, Any]:
        return await self._base.request("GET", "project-logs/log_labels/", token)

    async def get_week_logs(
        self,
        token: str,
        week_starting: str,
    ) -> dict[str, Any]:
        """Fetch the detailed week log for *week_starting* (YYYY-MM-DD)."""
        year = date.fromisoformat(week_starting).year

        list_result = await self._base.request(
            "GET", "project-logs/person/list/", token, params={"year": year}
        )
        if list_result["status"] != "success":
            return list_result

        data = BaseERPClient._unwrap_person_week_logs(list_result.get("data", []))
        week_log_id = BaseERPClient._find_week_log_id(data, week_starting)
        if week_log_id is None:
            return {
                "status": "error",
                "message": f"Week log not found for week starting {week_starting}",
            }

        return await self._base.request("GET", f"project-logs/person/get/{week_log_id}/", token)

    async def get_day_logs(
        self,
        token: str,
        date_str: str,
    ) -> dict[str, Any]:
        """Derive a single day's logs from its week log."""
        log_date = date.fromisoformat(date_str)
        monday = BaseERPClient._monday_of(log_date)

        week_result = await self.get_week_logs(token, monday.isoformat())
        if week_result.get("status") != "success":
            return week_result

        week_data = week_result.get("data", {})
        day_info = BaseERPClient._extract_day(week_data, date_str)
        day_info["week_starting"] = monday.isoformat()
        # person_id/person_name included intentionally — the authenticated
        # user's own data, useful for display and cross-referencing.
        day_info["week_log"] = {
            "id": week_data.get("id"),
            "person_id": week_data.get("person_id"),
            "person_name": week_data.get("person_name"),
            "is_completed": week_data.get("is_completed", False),
            "week_starting": week_data.get("week_starting"),
            "week_ending": week_data.get("week_ending"),
        }
        return {"status": "success", "data": day_info}

    async def get_logs_for_date_range(
        self,
        token: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """Fetch month-list logs for each calendar month in the range."""
        start_d = date.fromisoformat(start_date)
        end_d = date.fromisoformat(end_date)

        all_logs: list[dict[str, Any]] = []
        last_error: str | None = None
        cursor = start_d

        while cursor <= end_d:
            result = await self._base.request(
                "GET",
                "project-logs/person/month-list/",
                token,
                params={"year": cursor.year, "month": cursor.month},
            )
            if result.get("status") != "success":
                last_error = result.get("message", "API error")
            else:
                items, _err = BaseERPClient._extract_log_list(result)
                all_logs.extend(items)

            # Advance to the first of the next month.
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)

        if not all_logs and last_error:
            return {
                "status": "error",
                "message": f"Could not load logs for date range: {last_error}",
                "start_date": start_date,
                "end_date": end_date,
            }

        # Filter: keep weeks that overlap [start, end].
        filtered: list[dict[str, Any]] = []
        for log in all_logs:
            if not isinstance(log, dict):
                continue
            log_year = log.get("year", start_d.year)
            if isinstance(log_year, str):
                try:
                    log_year = int(log_year)
                except ValueError:
                    log_year = start_d.year
            ws = log.get("week_starting", "")
            ws_date = BaseERPClient._parse_week_starting_to_date(ws, log_year)
            if ws_date is None:
                continue
            we_date = ws_date + timedelta(days=6)
            if ws_date <= end_d and we_date >= start_d:
                filtered.append(log)

        return {
            "status": "success",
            "data": filtered,
            "count": len(filtered),
            "start_date": start_date,
            "end_date": end_date,
        }

    async def get_month_logs(
        self,
        token: str,
        year: int,
        month: int,
    ) -> dict[str, Any]:
        return await self._base.request(
            "GET",
            "project-logs/person/month-list/",
            token,
            # No int() coercion needed: MCP/FastMCP handles type coercion
            # for tool parameters before they reach this method.
            params={"year": year, "month": month},
        )

    # -- write methods ------------------------------------------------------

    async def create_or_update_log(
        self,
        token: str,
        date_str: str,
        project_id: int,
        description: str,
        hours: float,
        label_id: int | None = None,
    ) -> dict[str, Any]:
        """Create or update a log entry for a single day.

        Strategy:
        1. Verify the project exists in the user's active projects.
        2. If a week-log already exists for the target week, use the
           **Save API** (PATCH ``person-week-log/save/<id>/``).
        3. Otherwise fall back to the **Slack endpoint** (POST).
        """
        # Validate project exists in active projects.
        ap_result = await self._base.request(
            "GET", "project-logs/person/active_project_list/", token
        )
        if ap_result["status"] != "success":
            return ap_result

        active_projects = ap_result.get("data", [])
        subteam_id, active_team_name = BaseERPClient._find_active_project(
            active_projects, project_id
        )

        if subteam_id is None:
            return {
                "status": "error",
                "message": (f"Project {project_id} not found in active projects."),
            }

        effective_label = int(label_id) if label_id is not None else DEFAULT_LABEL_ID

        # Compute week Monday.
        log_date = date.fromisoformat(date_str)
        monday = BaseERPClient._monday_of(log_date)
        monday_str = monday.isoformat()
        year = monday.year

        # Look up existing week log.
        list_result = await self._base.request(
            "GET", "project-logs/person/list/", token, params={"year": year}
        )

        week_log_id: int | None = None
        if isinstance(list_result, dict) and list_result.get("status") == "success":
            data = BaseERPClient._unwrap_person_week_logs(list_result.get("data", []))
            week_log_id = BaseERPClient._find_week_log_id(data, monday_str)

        # Path 1: week log exists -- use Save API.
        if week_log_id is not None and week_log_id != 0:
            return await self._save_api_upsert(
                token=token,
                week_log_id=week_log_id,
                date_str=date_str,
                active_team_name=active_team_name,
                description=description,
                hours=hours,
                effective_label=effective_label,
                monday_str=monday_str,
            )

        # Path 2: no week log -- try Slack endpoint.
        total_minutes = round(hours * 60)
        hours_int, minutes_int = divmod(total_minutes, 60)
        time_str = f"{hours_int:02d}:{minutes_int:02d}"

        slack_payload = {
            "logs": [
                {
                    "date": date_str,
                    "time_spent": time_str,
                    "description": description,
                    "subteam": subteam_id,
                    "label_id": effective_label,
                }
            ],
        }
        slack_result = await self._base.request(
            "POST",
            "project-logs/person/person-week-log-from-slack/",
            token,
            data=slack_payload,
        )
        if slack_result.get("status") == "success":
            return {
                "status": "success",
                "message": f"Log entry added for {date_str}.",
                "data": slack_result.get("data", {}),
            }

        return {
            "status": "error",
            "message": (
                f"Could not create log for {date_str}. "
                f"No existing week log found for week starting {monday_str}, "
                "and the fallback creation endpoint also failed. "
                "Please create the week log via the ERP web interface first."
            ),
        }

    async def delete_log(
        self,
        token: str,
        date_str: str,
        project_id: int,
        description: str,
    ) -> dict[str, Any]:
        """Delete a log entry by setting hours to 0 via the Save API."""
        log_date = date.fromisoformat(date_str)
        monday = BaseERPClient._monday_of(log_date)
        monday_str = monday.isoformat()
        year = monday.year

        list_result = await self._base.request(
            "GET", "project-logs/person/list/", token, params={"year": year}
        )
        if list_result.get("status") != "success":
            return {
                "status": "error",
                "message": "Could not load week logs list.",
            }

        data = BaseERPClient._unwrap_person_week_logs(list_result.get("data", []))
        week_log_id = BaseERPClient._find_week_log_id(data, monday_str)
        if week_log_id is None:
            return {
                "status": "error",
                "message": (f"No week log for week starting {monday_str}.  Nothing to delete."),
            }

        get_result = await self._base.request(
            "GET", f"project-logs/person/get/{week_log_id}/", token
        )
        if get_result.get("status") != "success":
            return get_result

        week_log_data = get_result.get("data", {})
        if "modified_at" not in week_log_data:
            return {
                "status": "error",
                "message": "Week log data missing modified_at.",
            }

        # Resolve active-project team name.
        ap_result = await self._base.request(
            "GET", "project-logs/person/active_project_list/", token
        )
        active_team_name: str | None = None
        if ap_result.get("status") == "success":
            _, active_team_name = BaseERPClient._find_active_project(
                ap_result.get("data", []), project_id
            )
            active_team_name = active_team_name or None

        project_data = BaseERPClient._match_project_in_week_log(week_log_data, active_team_name)
        if project_data is None:
            return {
                "status": "error",
                "message": (f"Project not found in week log for week starting {monday_str}."),
            }

        tasks = project_data.get("tasks", [])
        task_index: int | None = None
        for i, task in enumerate(tasks):
            task_desc = (task.get("description") or "").strip().lower()
            if task_desc == (description or "").strip().lower():
                task_index = i
                break

        if task_index is None:
            return {
                "status": "error",
                "message": (f'No task matching "{description[:50]}" in that project.'),
            }

        task = tasks[task_index]
        days = task.get("days", [])
        new_days = [d for d in days if str(d.get("date")) != date_str]
        if not new_days:
            tasks.pop(task_index)
        else:
            task["days"] = new_days

        save_result = await self._base.request(
            "PATCH",
            f"project-logs/person/person-week-log/save/{week_log_id}/",
            token,
            data=week_log_data,
        )
        if save_result.get("status") == "success":
            return {
                "status": "success",
                "message": f"Log entry removed for {date_str}.",
                "data": save_result.get("data", {}),
            }
        return save_result

    async def complete_week_log(
        self,
        token: str,
        week_starting: str,
        save_draft: bool = False,
    ) -> dict[str, Any]:
        year = date.fromisoformat(week_starting).year
        list_result = await self._base.request(
            "GET", "project-logs/person/list/", token, params={"year": year}
        )
        if list_result.get("status") != "success":
            return list_result

        data = BaseERPClient._unwrap_person_week_logs(list_result.get("data", []))
        week_log_id = BaseERPClient._find_week_log_id(data, week_starting)
        if week_log_id is None:
            return {
                "status": "error",
                "message": (f"Week log not found for week starting {week_starting}"),
            }

        return await self._base.request(
            "PATCH",
            f"project-logs/person/person-week-log/complete/{week_log_id}/",
            token,
            data={"save_draft": bool(save_draft)},
        )

    async def fill_logs_for_days(
        self,
        token: str,
        start_date: str,
        end_date: str,
        project_id: int,
        description: str,
        hours_per_day: float = 8.0,
        label_id: int | None = None,
        skip_weekends: bool = False,
    ) -> dict[str, Any]:
        """Batch-create logs.  SEC-05: capped at 31 days.

        This operation is idempotent — re-running for the same dates updates
        existing entries rather than creating duplicates (upsert semantics).
        """
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        if end < start:
            return {
                "status": "error",
                "message": "end_date must be >= start_date.",
            }

        span = (end - start).days + 1
        if span > 31:
            return {
                "status": "error",
                "message": (f"Date range exceeds 31 days ({span} days requested)."),
            }

        updated_count = 0
        skipped_count = 0
        errors: list[dict[str, str]] = []
        current = start

        # TODO: batch by week to reduce sequential HTTP calls (currently 2-4 calls per day).
        while current <= end:
            if skip_weekends and current.weekday() >= 5:
                skipped_count += 1
                current += timedelta(days=1)
                continue

            result = await self.create_or_update_log(
                token=token,
                date_str=current.isoformat(),
                project_id=project_id,
                description=description,
                hours=hours_per_day,
                label_id=label_id,
            )
            if result.get("status") == "success":
                updated_count += 1
            else:
                errors.append(
                    {
                        "date": current.isoformat(),
                        "error": result.get("message", "Unknown error"),
                    }
                )

            current += timedelta(days=1)

        total_days = span - skipped_count
        if not errors:
            status = "success"
        elif updated_count > 0:
            status = "partial_error"
        else:
            status = "error"

        return {
            "status": status,
            "message": (f"Processed {updated_count}/{total_days} days ({len(errors)} errors)"),
            "data": {
                "start_date": start_date,
                "end_date": end_date,
                "updated": updated_count,
                "skipped": skipped_count,
                "total_days": span,
                "errors": errors,
            },
        }

    async def check_person_week_project_exists(
        self,
        token: str,
        date_str: str,
        project_id: int,
    ) -> dict[str, Any]:
        """Check whether a PersonWeekProject exists for date + project."""
        log_date = date.fromisoformat(date_str)
        monday = BaseERPClient._monday_of(log_date)
        monday_str = monday.isoformat()

        week_result = await self.get_week_logs(token, monday_str)
        if week_result.get("status") != "success":
            return {
                "status": "error",
                "message": f"Could not retrieve week log for {monday_str}",
                "details": week_result.get("message", "Unknown error"),
            }

        week_data = week_result.get("data", {})
        projects = week_data.get("projects", [])
        for project in projects:
            try:
                if int(project.get("id", -1)) == project_id:
                    return {
                        "status": "success",
                        "exists": True,
                        "person_week_project_id": project.get("id"),
                        "week_starting": monday_str,
                        "project_id": project_id,
                        "message": "PersonWeekProject exists.",
                    }
            except (ValueError, TypeError):
                continue

        return {
            "status": "success",
            "exists": False,
            "week_starting": monday_str,
            "project_id": project_id,
            "message": "PersonWeekProject does not exist.",
        }

    # -- resolution helpers --------------------------------------------------

    async def resolve_project_id(
        self,
        token: str,
        project_id: int | None = None,
        project_name: str | None = None,
    ) -> int:
        """Resolve a project by ID or case-insensitive partial name match.

        Raises:
            ValueError: If neither is given, or name is not found.
        """
        if project_id is not None:
            return int(project_id)

        if not project_name:
            raise ValueError("Either project_id or project_name must be provided.")

        result = await self.get_active_projects(token)
        if result.get("status") != "success":
            raise ValueError(f"Failed to fetch active projects: {result.get('message')}")

        search = project_name.lower().strip()
        projects = [p for p in result.get("data", []) if isinstance(p, dict)]

        # First pass: exact case-insensitive match.
        for proj in projects:
            team = (proj.get("team") or "").lower()
            if search == team:
                return int(proj["id"])

        # Second pass: substring match -- must be unambiguous.
        substring_matches: list[dict[str, Any]] = []
        for proj in projects:
            team = (proj.get("team") or "").lower()
            if search in team:
                substring_matches.append(proj)

        if len(substring_matches) == 1:
            return int(substring_matches[0]["id"])

        if len(substring_matches) > 1:
            ambiguous = [p.get("team", "") for p in substring_matches]
            raise ValueError(
                f"Ambiguous project name '{project_name}' matched multiple "
                f"projects: {ambiguous}.  Use a more specific name or project_id."
            )

        available = [p.get("team", "") for p in projects]
        raise ValueError(
            f"Project '{project_name}' not found in active projects.  Available: {available}"
        )

    async def resolve_label_id(
        self,
        token: str,
        label_id: int | None = None,
        label_name: str | None = None,
    ) -> int | None:
        """Resolve a label by ID or case-insensitive exact name match.

        Returns ``None`` if no label is specified (falls back to default).

        Raises:
            ValueError: If ``label_name`` is provided but no match is found,
                or if the labels API call fails.
        """
        if label_id is not None:
            return int(label_id)

        if not label_name:
            return None

        result = await self.get_log_labels(token)
        if result.get("status") != "success":
            raise ValueError(
                f"Failed to fetch log labels (cannot resolve '{label_name}'): "
                f"{result.get('message')}"
            )

        labels = [lb for lb in result.get("data", []) if isinstance(lb, dict)]
        search = label_name.strip().lower()
        for lb in labels:
            name = (lb.get("name") or "").strip().lower()
            if name == search:
                return int(lb["id"])

        raise ValueError(
            f"Label '{label_name}' not found. "
            f"Available labels: {[lb.get('name') for lb in labels]}"
        )

    # -- private helpers -----------------------------------------------------

    async def _save_api_upsert(
        self,
        *,
        token: str,
        week_log_id: int,
        date_str: str,
        active_team_name: str,
        description: str,
        hours: float,
        effective_label: int,
        monday_str: str,
    ) -> dict[str, Any]:
        """Upsert a log entry via the Save API (PATCH)."""
        get_result = await self._base.request(
            "GET", f"project-logs/person/get/{week_log_id}/", token
        )
        if get_result.get("status") != "success":
            return get_result

        week_log_data = get_result.get("data", {})
        if "modified_at" not in week_log_data:
            return {
                "status": "error",
                "message": "Week log data missing modified_at.",
            }

        project_data = BaseERPClient._match_project_in_week_log(week_log_data, active_team_name)
        if project_data is None:
            return {
                "status": "error",
                "message": (f"Project not found in week log for week starting {monday_str}."),
            }

        total_minutes = round(hours * 60)
        hours_int, minutes_int = divmod(total_minutes, 60)

        # Find or create the task.
        task_data: dict[str, Any] | None = None
        for task in project_data.get("tasks", []):
            if task.get("description", "").strip().lower() == description.strip().lower():
                task_data = task
                break

        if task_data is not None:
            # Update existing task.
            day_detail: dict[str, Any] | None = None
            for day in task_data.get("days", []):
                if day.get("date") == date_str:
                    day_detail = day
                    break
            if day_detail is not None:
                day_detail["hours"] = hours_int
                day_detail["minutes"] = minutes_int
                day_detail["decimal_hours"] = round(hours, 2)
                day_detail["label"] = effective_label
            else:
                new_day: dict[str, Any] = {
                    "date": date_str,
                    "hours": hours_int,
                    "minutes": minutes_int,
                    "decimal_hours": round(hours, 2),
                    "label": effective_label,
                }
                task_data.setdefault("days", []).append(new_day)
        else:
            new_task: dict[str, Any] = {
                "description": description,
                "days": [
                    {
                        "date": date_str,
                        "hours": hours_int,
                        "minutes": minutes_int,
                        "decimal_hours": round(hours, 2),
                        "label": effective_label,
                    }
                ],
            }
            project_data.setdefault("tasks", []).append(new_task)

        return await self._base.request(
            "PATCH",
            f"project-logs/person/person-week-log/save/{week_log_id}/",
            token,
            data=week_log_data,
        )
