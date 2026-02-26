"""Leaves MCP tools — leave management operations."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date as date_type
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from _auth import check_erp_result, get_erp_token, tool_error_handler
from _constants import MAX_ENCASHMENT_DAYS, MAX_LEAVE_DAYS, MAX_LEAVE_REASON_LEN
from clients import get_registry

logger = logging.getLogger("erp_mcp.server")

__all__ = ["register"]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_HALF_DAY_PERIODS = {"first_half", "second_half"}


def _validate_date_str(value: str, name: str = "date") -> None:
    """Validate that *value* is strictly YYYY-MM-DD (no ISO week dates)."""
    if not _DATE_RE.match(value):
        raise ToolError(f"{name} must be in YYYY-MM-DD format, got: {value!r}")


def register(mcp: FastMCP) -> None:
    """Register all leaves tools on the given FastMCP instance."""

    # ------------------------------------------------------------------
    # Read tools
    # ------------------------------------------------------------------

    @mcp.tool
    @tool_error_handler("Failed to fetch leave choices. Please try again.")
    async def leaves_get_choices() -> dict[str, Any]:
        """Get leave types and approver for the authenticated user."""
        token, _email = await get_erp_token()
        return check_erp_result(await get_registry().leaves.get_choices(token))

    @mcp.tool
    @tool_error_handler("Failed to fetch fiscal years. Please try again.")
    async def leaves_get_fiscal_years() -> dict[str, Any]:
        """Get available fiscal years for leave summary queries.

        Returns the list of fiscal years with their IDs and date ranges,
        plus the ID of the currently active fiscal year.
        """
        token, _email = await get_erp_token()
        return check_erp_result(await get_registry().leaves.get_fiscal_years(token))

    @mcp.tool
    @tool_error_handler("Failed to fetch leave summary. Please try again.")
    async def leaves_get_summary(fiscal_year_id: int | None = None) -> dict[str, Any]:
        """Get leave balances for the authenticated user.

        Args:
            fiscal_year_id: Fiscal year ID from leaves_get_fiscal_years.
                If omitted, the current active fiscal year is used automatically.
        """
        token, _email = await get_erp_token()
        client = get_registry().leaves
        if fiscal_year_id is None:
            fy_result = check_erp_result(await client.get_fiscal_years(token))
            fiscal_year_id = fy_result.get("data", {}).get("selected_fiscal_year")
            if fiscal_year_id is None:
                logger.debug("Fiscal year auto-resolution failed. fy_result=%s", fy_result)
                raise ToolError("No active fiscal year found.")
        return check_erp_result(await client.get_summary(token, fiscal_year_id))

    @mcp.tool
    @tool_error_handler("Failed to fetch monthly leaves. Please try again.")
    async def leaves_list_month(year: int, month: int) -> dict[str, Any]:
        """Get approved leaves and holidays for a specific month.

        Args:
            year: Year (e.g. 2024).
            month: Month number (1-12).
        """
        token, _email = await get_erp_token()
        client = get_registry().leaves
        leaves_result, holidays_result = await asyncio.gather(
            client.get_month_leaves(token, year, month),
            client.get_holidays(token, year, month),
        )

        leaves_data = (
            leaves_result.get("data", []) if leaves_result.get("status") == "success" else []
        )
        holidays_data = (
            holidays_result.get("data", []) if holidays_result.get("status") == "success" else []
        )

        # If both failed, raise
        if leaves_result.get("status") != "success" and holidays_result.get("status") != "success":
            return check_erp_result(leaves_result)

        return {
            "status": "success",
            "data": {
                "leaves": leaves_data,
                "holidays": holidays_data,
                "year": year,
                "month": month,
            },
        }

    @mcp.tool
    @tool_error_handler("Failed to fetch own leaves. Please try again.")
    async def leaves_list_mine(year: int, month: int) -> dict[str, Any]:
        """List own leaves for a month (all statuses).

        Args:
            year: Year (e.g. 2024).
            month: Month number (1-12).
        """
        token, _email = await get_erp_token()
        return check_erp_result(await get_registry().leaves.list_mine(token, year, month))

    @mcp.tool
    @tool_error_handler("Failed to fetch team leaves. Please try again.")
    async def leaves_list_team() -> dict[str, Any]:
        """List team members currently on leave."""
        token, _email = await get_erp_token()
        return check_erp_result(await get_registry().leaves.list_team(token))

    @mcp.tool
    @tool_error_handler("Failed to fetch encashments. Please try again.")
    async def leaves_list_encashments() -> dict[str, Any]:
        """List own leave encashment claims."""
        token, _email = await get_erp_token()
        return check_erp_result(await get_registry().leaves.list_encashments(token))

    # ------------------------------------------------------------------
    # Write tools
    # ------------------------------------------------------------------

    @mcp.tool
    @tool_error_handler("Failed to apply for leave. Please try again.")
    async def leaves_apply(
        leave_type: int,
        start_date: str,
        end_date: str,
        reason: str,
        half_day: bool = False,
        half_day_period: str | None = None,
    ) -> dict[str, Any]:
        """Apply for leave.

        Args:
            leave_type: Leave type ID (from leaves_get_choices).
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            reason: Reason for leave.
            half_day: Whether this is a half-day leave. Default is false.
            half_day_period: If half_day is true, specify 'first_half' or 'second_half'.
        """
        _validate_date_str(start_date, "start_date")
        _validate_date_str(end_date, "end_date")
        if len(reason) > MAX_LEAVE_REASON_LEN:
            raise ToolError(f"Reason too long (max {MAX_LEAVE_REASON_LEN} characters)")

        if half_day and half_day_period not in _VALID_HALF_DAY_PERIODS:
            raise ToolError(
                f"half_day_period must be one of {_VALID_HALF_DAY_PERIODS} when half_day is true."
            )
        if half_day_period and not half_day:
            raise ToolError("half_day must be true when half_day_period is specified.")

        start = date_type.fromisoformat(start_date)
        end = date_type.fromisoformat(end_date)
        if end < start:
            raise ValueError("end_date must be on or after start_date.")
        day_span = (end - start).days + 1
        if day_span > MAX_LEAVE_DAYS:
            raise ValueError(
                f"Date range spans {day_span} days, exceeding the maximum of "
                f"{MAX_LEAVE_DAYS} days."
            )

        token, email = await get_erp_token()
        result = check_erp_result(
            await get_registry().leaves.apply(
                token,
                leave_type=leave_type,
                start_date=start_date,
                end_date=end_date,
                reason=reason,
                half_day=half_day,
                half_day_period=half_day_period,
            )
        )
        logger.info(
            "WRITE_OP tool=leaves_apply user=%s leave_type=%s start=%s end=%s",
            email,
            leave_type,
            start_date,
            end_date,
        )
        return result

    @mcp.tool
    @tool_error_handler("Failed to cancel leave. Please try again.")
    async def leaves_cancel(leave_id: int) -> dict[str, Any]:
        """Cancel a pending leave request.

        Args:
            leave_id: ID of the leave to cancel.
        """
        if leave_id <= 0:
            raise ValueError("leave_id must be a positive integer.")

        token, email = await get_erp_token()
        result = check_erp_result(await get_registry().leaves.cancel(token, leave_id=leave_id))
        logger.info(
            "WRITE_OP tool=leaves_cancel user=%s leave_id=%s",
            email,
            leave_id,
        )
        return result

    @mcp.tool
    @tool_error_handler("Failed to create encashment. Please try again.")
    async def leaves_create_encashment(
        leave_type: int,
        days: int,
    ) -> dict[str, Any]:
        """Create a leave encashment request.

        Args:
            leave_type: Leave type ID (from leaves_get_choices).
            days: Number of days to encash.
        """
        if days <= 0:
            raise ValueError("days must be a positive integer.")
        if days > MAX_ENCASHMENT_DAYS:
            raise ValueError(f"days exceeds maximum of {MAX_ENCASHMENT_DAYS}.")

        token, email = await get_erp_token()
        result = check_erp_result(
            await get_registry().leaves.create_encashment(token, leave_type=leave_type, days=days)
        )
        logger.info(
            "WRITE_OP tool=leaves_create_encashment user=%s leave_type=%s days=%s",
            email,
            leave_type,
            days,
        )
        return result
