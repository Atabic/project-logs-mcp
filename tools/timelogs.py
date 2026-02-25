"""Timelog MCP tools — project time-logging operations."""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from _auth import check_erp_result, get_erp_token, tool_error_handler
from _constants import MAX_DESCRIPTION_LEN, MAX_FILL_DAYS, MAX_QUERY_DAYS
from clients import get_registry

logger = logging.getLogger("erp_mcp.server")

__all__ = ["register"]


def register(mcp: FastMCP) -> None:
    """Register all timelog tools on the given FastMCP instance."""

    # ------------------------------------------------------------------
    # Read tools
    # ------------------------------------------------------------------

    @mcp.tool
    @tool_error_handler("Failed to fetch active projects. Please try again.")
    async def timelogs_list_projects() -> dict[str, Any]:
        """Get list of active projects/subteams for the authenticated user."""
        token, _email = await get_erp_token()
        return check_erp_result(await get_registry().timelogs.get_active_projects(token))

    @mcp.tool
    @tool_error_handler("Failed to fetch log labels. Please try again.")
    async def timelogs_list_labels() -> dict[str, Any]:
        """Get available log labels for categorizing log entries."""
        token, _email = await get_erp_token()
        return check_erp_result(await get_registry().timelogs.get_log_labels(token))

    @mcp.tool
    @tool_error_handler("Failed to fetch week logs. Please try again.")
    async def timelogs_get_week(week_starting: str) -> dict[str, Any]:
        """Get project logs for a specific week.

        Args:
            week_starting: Week starting date in YYYY-MM-DD format (must be a Monday).
        """
        d = date_type.fromisoformat(week_starting)
        if d.weekday() != 0:
            raise ToolError(
                f"week_starting must be a Monday, got {week_starting} ({d.strftime('%A')})"
            )
        token, _email = await get_erp_token()
        return check_erp_result(
            await get_registry().timelogs.get_week_logs(token, week_starting)
        )

    @mcp.tool
    @tool_error_handler("Failed to fetch day logs. Please try again.")
    async def timelogs_get_day(date_str: str) -> dict[str, Any]:
        """Get detailed project logs for a specific day.

        Returns all projects, tasks, and time logged for that day.

        Args:
            date_str: Date in YYYY-MM-DD format.
        """
        token, _email = await get_erp_token()
        return check_erp_result(
            await get_registry().timelogs.get_day_logs(token, date_str)
        )

    @mcp.tool
    @tool_error_handler("Failed to fetch logs for date range. Please try again.")
    async def timelogs_get_range(
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
        if day_span > MAX_QUERY_DAYS:
            raise ValueError(
                f"Date range spans {day_span} days, exceeding the maximum of "
                f"{MAX_QUERY_DAYS} days for read queries."
            )

        token, _email = await get_erp_token()
        return check_erp_result(
            await get_registry().timelogs.get_logs_for_date_range(
                token, start_date, end_date
            )
        )

    @mcp.tool
    @tool_error_handler("Failed to fetch month logs. Please try again.")
    async def timelogs_get_month(year: int, month: int) -> dict[str, Any]:
        """Get all project logs for a specific month and year.

        Args:
            year: Year (e.g. 2024).
            month: Month number (1-12).
        """
        token, _email = await get_erp_token()
        return check_erp_result(
            await get_registry().timelogs.get_month_logs(token, year, month)
        )

    # ------------------------------------------------------------------
    # Write tools
    # ------------------------------------------------------------------

    @mcp.tool
    @tool_error_handler("Failed to create or update log. Please try again.")
    async def timelogs_upsert_entry(
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
        if len(description) > MAX_DESCRIPTION_LEN:
            raise ToolError(
                f"Description too long (max {MAX_DESCRIPTION_LEN} characters)"
            )
        if hours <= 0 or hours > 24:
            raise ValueError(
                f"hours must be between 0 (exclusive) and 24 (inclusive), got {hours}"
            )
        if round(hours * 60) < 1:
            raise ToolError(
                "Hours too small: rounds to 0 minutes. Minimum is ~0.02 (1 minute)."
            )
        token, email = await get_erp_token()
        client = get_registry().timelogs
        resolved_project_id = await client.resolve_project_id(
            token, project_id, project_name
        )
        resolved_label_id = await client.resolve_label_id(
            token, label_id, label_name
        )
        result = check_erp_result(
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
            "WRITE_OP tool=timelogs_upsert_entry user=%s date=%s project_id=%s hours=%s",
            email,
            date,
            resolved_project_id,
            hours,
        )
        return result

    @mcp.tool
    @tool_error_handler("Failed to delete log. Please try again.")
    async def timelogs_delete_entry(
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
        if len(description) > MAX_DESCRIPTION_LEN:
            raise ToolError(
                f"Description too long (max {MAX_DESCRIPTION_LEN} characters)"
            )
        token, email = await get_erp_token()
        client = get_registry().timelogs
        resolved_project_id = await client.resolve_project_id(
            token, project_id, project_name
        )
        result = check_erp_result(
            await client.delete_log(
                token,
                date_str=date,
                project_id=resolved_project_id,
                description=description,
            )
        )
        logger.info(
            "WRITE_OP tool=timelogs_delete_entry user=%s date=%s project_id=%s",
            email,
            date,
            resolved_project_id,
        )
        return result

    @mcp.tool
    @tool_error_handler("Failed to complete week log. Please try again.")
    async def timelogs_complete_week(
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
        token, email = await get_erp_token()
        result = check_erp_result(
            await get_registry().timelogs.complete_week_log(
                token, week_starting, save_draft=save_draft
            )
        )
        logger.info(
            "WRITE_OP tool=timelogs_complete_week user=%s week_starting=%s save_draft=%s",
            email,
            week_starting,
            save_draft,
        )
        return result

    @mcp.tool
    @tool_error_handler("Failed to fill logs for days. Please try again.")
    async def timelogs_fill_days(
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
        if len(description) > MAX_DESCRIPTION_LEN:
            raise ToolError(
                f"Description too long (max {MAX_DESCRIPTION_LEN} characters)"
            )
        if hours_per_day <= 0 or hours_per_day > 24:
            raise ValueError(
                f"hours_per_day must be between 0 (exclusive) and 24 (inclusive), "
                f"got {hours_per_day}"
            )
        if round(hours_per_day * 60) < 1:
            raise ToolError(
                "Hours too small: rounds to 0 minutes. Minimum is ~0.02 (1 minute)."
            )
        # --- SEC-05: 31-day cap ---
        try:
            start = date_type.fromisoformat(start_date)
            end = date_type.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError(
                f"Invalid date format. Use YYYY-MM-DD. Details: {exc}"
            ) from exc

        day_count = (end - start).days + 1
        if day_count > MAX_FILL_DAYS:
            raise ValueError(
                f"Date range spans {day_count} days, exceeding the maximum of "
                f"{MAX_FILL_DAYS} days. Please use a shorter range."
            )
        if day_count < 1:
            raise ValueError("end_date must be on or after start_date.")

        token, email = await get_erp_token()
        client = get_registry().timelogs
        resolved_project_id = await client.resolve_project_id(
            token, project_id, project_name
        )
        resolved_label_id = await client.resolve_label_id(
            token, label_id, label_name
        )
        result = check_erp_result(
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
            "WRITE_OP tool=timelogs_fill_days user=%s start=%s end=%s project_id=%s days=%s",
            email,
            start_date,
            end_date,
            resolved_project_id,
            day_count,
        )
        return result

    # ------------------------------------------------------------------
    # Diagnostic tools
    # ------------------------------------------------------------------

    @mcp.tool
    @tool_error_handler("Failed to check PersonWeekProject existence. Please try again.")
    async def timelogs_check_week_project(
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
        token, _email = await get_erp_token()
        client = get_registry().timelogs
        resolved_project_id = await client.resolve_project_id(
            token, project_id, project_name
        )
        return check_erp_result(
            await client.check_person_week_project_exists(
                token, date, resolved_project_id
            )
        )
