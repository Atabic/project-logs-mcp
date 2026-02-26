"""Domain client for ERP leave operations.

Uses composition: holds a reference to :class:`BaseERPClient` for HTTP
transport and delegates all network I/O through ``self._base._request()``.
"""

from __future__ import annotations

from typing import Any

from clients._base import BaseERPClient

__all__ = ["LeavesClient"]


class LeavesClient:
    """High-level operations on ERP leaves.

    All public methods take ``token: str`` as the first argument (SEC-01).
    """

    def __init__(self, base: BaseERPClient) -> None:
        self._base = base

    # -- read methods -------------------------------------------------------

    async def get_choices(self, token: str) -> dict[str, Any]:
        """Get leave types and approver for the user."""
        return await self._base._request("GET", "leaves/choices/get/", token)

    async def get_summary(self, token: str, selected_year: int) -> dict[str, Any]:
        """Get leave balances for the authenticated user.

        The ERP ``LeaveSummaryView`` identifies the person from the auth token,
        so no person-PK or email parameter is needed (SEC-01).

        Args:
            selected_year: Fiscal year to query (required by the ERP backend).
        """
        return await self._base._request(
            "GET",
            "leaves/leave_summary/get/",
            token,
            params={"selected_year": selected_year},
        )

    async def get_month_leaves(self, token: str, year: int, month: int) -> dict[str, Any]:
        """Get approved leaves for a month."""
        return await self._base._request(
            "GET",
            "leaves/person/month_leaves/",
            token,
            params={"year": year, "month": month},
        )

    async def get_holidays(self, token: str, year: int, month: int) -> dict[str, Any]:
        """Get holidays for a month."""
        return await self._base._request(
            "GET",
            "leaves/holiday_records/",
            token,
            params={"year": year, "month": month},
        )

    async def list_mine(self, token: str, year: int, month: int) -> dict[str, Any]:
        """List own leaves for a month (all statuses)."""
        return await self._base._request(
            "GET",
            "leaves/list/",
            token,
            params={"year": year, "month": month},
        )

    async def list_team(self, token: str) -> dict[str, Any]:
        """List team members currently on leave."""
        return await self._base._request("GET", "leaves/team_leaves/list/", token)

    async def list_encashments(self, token: str) -> dict[str, Any]:
        """List own encashment claims."""
        return await self._base._request("GET", "leaves/person-leave-encashments/", token)

    # -- write methods ------------------------------------------------------

    async def apply(
        self,
        token: str,
        leave_type: int,
        start_date: str,
        end_date: str,
        reason: str,
        half_day: bool = False,
        half_day_period: str | None = None,
    ) -> dict[str, Any]:
        """Apply for leave."""
        payload: dict[str, Any] = {
            "leave_type": leave_type,
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason,
            "half_day": half_day,
        }
        if half_day_period is not None:
            payload["half_day_period"] = half_day_period
        return await self._base._request("POST", "leaves/request/apply/", token, data=payload)

    async def cancel(self, token: str, leave_id: int) -> dict[str, Any]:
        """Cancel a pending leave."""
        return await self._base._request("POST", f"leaves/delete_leave/{leave_id}/", token)

    async def create_encashment(
        self,
        token: str,
        leave_type: int,
        days: int,
    ) -> dict[str, Any]:
        """Create a leave encashment request."""
        payload = {"leave_type": leave_type, "days": days}
        return await self._base._request(
            "POST", "leaves/person-leave-encashments/", token, data=payload
        )
