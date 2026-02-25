"""Stateless ERP API client for the Arbisoft time-logging MCP server.

Replaces the legacy ``api_client.py`` (1 550+ lines) and ``auth.py`` (148 lines)
with a single, stateless, typed module.  Every public method that queries
user-specific data takes ``token: str`` -- identity comes exclusively from the
Google token, never from an ``email`` parameter (SEC-01).

Security controls implemented:
    SEC-01  No ``email`` parameter on data-query methods.
    SEC-02  Domain restriction in ``exchange_google_token``.
    SEC-03  SHA-256 hashed cache keys with bounded LRU eviction (500 entries).
    SEC-04  ``_request`` returns error dicts for 4xx/5xx -- never raises.
    SEC-05  ``fill_logs_for_days`` caps at 31 days.
    SEC-06  ``httpx.AsyncClient(follow_redirects=False)``.
    SEC-07  Constructor rejects non-HTTPS base_url for non-localhost targets.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import time
from collections import OrderedDict
from datetime import date, timedelta
from typing import Any, cast
from urllib.parse import urlparse

import httpx

__all__ = ["ERPClient", "TTLCache"]

logger = logging.getLogger("erp_mcp.client")

# ---------------------------------------------------------------------------
# TTLCache
# ---------------------------------------------------------------------------

_MONTH_MAP: dict[str, int] = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


class TTLCache[T]:
    """Bounded LRU cache with per-entry TTL expiry.

    Uses :class:`collections.OrderedDict` for O(1) move-to-end.
    Clock source: :func:`time.monotonic` (immune to wall-clock changes).

    Sync methods (``_get``/``_put``/``clear``/``__len__``) are NOT async-safe.
    Use ``aget``/``aput``/``aclear`` for concurrent async access within a
    single event loop.
    """

    __slots__ = ("_data", "_lock", "_maxsize", "_ttl")

    def __init__(self, maxsize: int = 500, ttl: float = 900.0) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        if ttl <= 0:
            raise ValueError("ttl must be > 0")
        self._maxsize = maxsize
        self._ttl = ttl
        # value stored as (payload, expires_at)
        self._data: OrderedDict[str, tuple[T, float]] = OrderedDict()
        self._lock = asyncio.Lock()

    # -- internal sync helpers (use aget/aput/aclear for async-safe access) --

    def _get(self, key: str) -> T | None:
        """Return cached value or ``None`` if missing / expired."""
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            # expired -- evict
            del self._data[key]
            return None
        # Mark as recently used.
        self._data.move_to_end(key)
        return value

    def _put(self, key: str, value: T) -> None:
        """Insert or overwrite *key*.  Evicts LRU entry if at capacity."""
        now = time.monotonic()
        if key in self._data:
            # Overwrite: remove first so move_to_end puts it at the tail.
            del self._data[key]
        elif len(self._data) >= self._maxsize:
            # Evict least-recently-used (front of OrderedDict).
            self._data.popitem(last=False)
        self._data[key] = (value, now + self._ttl)

    # -- public async API ----------------------------------------------------

    async def aget(self, key: str) -> T | None:
        """Async-safe wrapper around :meth:`_get`."""
        async with self._lock:
            return self._get(key)

    async def aput(self, key: str, value: T) -> None:
        """Async-safe wrapper around :meth:`_put`."""
        async with self._lock:
            self._put(key, value)

    async def aclear(self) -> None:
        """Async-safe cache clear."""
        async with self._lock:
            self._data.clear()

    def clear(self) -> None:
        """Sync clear -- NOT lock-protected.  For use in tests/setup only."""
        self._data.clear()

    def __len__(self) -> int:
        """Return count of non-expired entries (read-only, no eviction)."""
        now = time.monotonic()
        return sum(1 for _, exp in self._data.values() if exp > now)


# ---------------------------------------------------------------------------
# ERPClient
# ---------------------------------------------------------------------------

# ERP "General" category label ID. Must match the ERP backend's `log_labels` table.
# Verify via: GET /api/v1/project-logs/log_labels/ → look for name="General".
_DEFAULT_LABEL_ID: int = 66

# Maximum recursion depth for _find_week_log_id to prevent stack overflow
# on pathological API responses.
_MAX_RECURSION_DEPTH: int = 20

_MAX_TOKEN_LENGTH: int = 4096


class ERPClient:
    """Stateless async HTTP client for the Arbisoft ERP time-logging API.

    Every data-query method takes ``token: str`` as the first positional
    argument.  The token is forwarded as ``Authorization: Token <value>`` on
    every request.  No method accepts an ``email`` parameter (SEC-01).
    """

    # -- construction -------------------------------------------------------

    def __init__(
        self,
        base_url: str,
        allowed_domain: str = "arbisoft.com",
    ) -> None:
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()

        # SEC-07: reject non-HTTPS for non-localhost targets.
        if parsed.scheme != "https" and not self._is_loopback(host):
            raise ValueError(
                f"Non-HTTPS base_url is only permitted for localhost. Got: {base_url}"
            )

        self._base_url: str = base_url.rstrip("/")
        self._allowed_domain: str = allowed_domain.lower().strip()
        if not self._allowed_domain:
            raise ValueError("allowed_domain must not be empty")
        self._token_cache: TTLCache[tuple[str, str]] = TTLCache(maxsize=500)
        # Per-key locks to coalesce concurrent token exchanges for the same key.
        self._exchange_locks: dict[str, asyncio.Lock] = {}
        # SEC-06: disable HTTP redirects.
        self._http: httpx.AsyncClient = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
            verify=True,
        )

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._http.aclose()

    async def __aenter__(self) -> ERPClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @staticmethod
    def _is_loopback(host: str) -> bool:
        """Check if host is a loopback address (localhost, 127.x.x.x, ::1, etc.)."""
        if host in ("localhost",):
            return True
        # Strip brackets for IPv6 (e.g., "[::1]" -> "::1")
        stripped = host.strip("[]")
        try:
            return ipaddress.ip_address(stripped).is_loopback
        except ValueError:
            return False

    # -- authentication -----------------------------------------------------

    async def exchange_google_token(
        self,
        google_token: str,
    ) -> tuple[str, str]:
        """Exchange a Google OAuth access token for an ERP DRF token.

        Returns:
            ``(erp_token, email)``

        Raises:
            ValueError: On empty token, backend error, domain mismatch.
        """
        if not google_token or not google_token.strip():
            raise ValueError("google_token must not be empty")

        google_token = google_token.strip()

        if len(google_token) > _MAX_TOKEN_LENGTH:
            raise ValueError(f"google_token exceeds maximum length ({_MAX_TOKEN_LENGTH})")

        # SEC-03: SHA-256 hash as cache key.
        cache_key = hashlib.sha256(google_token.encode()).hexdigest()

        cached = await self._token_cache.aget(cache_key)
        if cached is not None:
            return cached

        # Per-key lock: coalesce concurrent exchanges for the same google_token
        # so only the first caller does the HTTP round-trip.
        if cache_key not in self._exchange_locks:
            self._exchange_locks[cache_key] = asyncio.Lock()
        lock = self._exchange_locks[cache_key]

        try:
            async with lock:
                # Re-check cache: another coroutine may have populated it while we waited.
                cached = await self._token_cache.aget(cache_key)
                if cached is not None:
                    return cached

                # Call ERP backend (no auth header for login endpoints).
                url = f"{self._base_url}/core/google-login/"
                try:
                    response = await self._http.post(
                        url,
                        json={"platform": "google", "access_token": google_token},
                        headers={
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                    )
                except httpx.TransportError as exc:
                    raise ConnectionError(f"Google token exchange failed: {exc}") from exc

                if response.status_code >= 400:
                    raise ValueError(f"Google token exchange failed (HTTP {response.status_code})")

                try:
                    data = response.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError("ERP backend returned invalid JSON response") from exc
                erp_token: str | None = data.get("token")
                email: str | None = data.get("email")

                if not erp_token:
                    raise ValueError("Backend did not return a token")
                if not email:
                    raise ValueError("Backend did not return an email")

                # Validate token format before caching.
                if not re.match(r'^[a-zA-Z0-9_.\-]{10,512}$', erp_token):
                    raise ValueError(
                        f"ERP backend returned invalid token format (length={len(erp_token)})"
                    )

                # SEC-02: domain restriction.
                if "@" not in email:
                    raise ValueError("Backend returned email without '@' symbol")
                domain = email.rsplit("@", 1)[-1].lower().strip()
                if domain != self._allowed_domain:
                    raise ValueError(
                        f"Email domain '{domain}' is not allowed.  "
                        f"Only @{self._allowed_domain} accounts may authenticate."
                    )

                result = (erp_token, email)
                await self._token_cache.aput(cache_key, result)
                return result
        finally:
            # Clean up the per-key lock if no other coroutine is waiting on it,
            # preventing unbounded growth of _exchange_locks.
            if not lock.locked():
                self._exchange_locks.pop(cache_key, None)

    # -- generic request helper ---------------------------------------------

    async def _request(
        self,
        method: str,
        endpoint: str,
        token: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request.  Returns a result dict.

        SEC-04: Never raises on HTTP errors -- returns an error dict instead.
        """
        # Endpoint paths are hardcoded in this module; no user-controlled path segments.
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Authorization": f"Token {token}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"

        try:
            response = await self._http.request(
                method,
                url,
                json=data,
                params=params,
                headers=headers,
            )
        except httpx.TransportError as exc:
            logger.warning(
                "ERP API %s %s transport error: %s",
                method,
                endpoint,
                exc,
            )
            return {
                "status": "error",
                "message": "ERP service temporarily unavailable.",
            }
        except Exception as exc:
            logger.warning(
                "ERP API %s %s unexpected error: %s",
                method,
                endpoint,
                exc,
            )
            return {
                "status": "error",
                "message": "An unexpected error occurred. Please try again.",
            }

        # Parse response body.
        try:
            response_data = response.json()
        except (json.JSONDecodeError, ValueError):
            response_data = {"text": response.text[:500]}

        if response.status_code >= 400:
            logger.warning(
                "ERP API %s %s returned status=%d",
                method,
                endpoint,
                response.status_code,
            )
            error_msg = "API error"
            if isinstance(response_data, dict):
                error_msg = (
                    response_data.get("error")
                    or response_data.get("detail")
                    or f"API error: {response.status_code}"
                )
            error_msg = str(error_msg)
            if len(error_msg) > 500:
                error_msg = error_msg[:500] + "..."
            return {
                "status": "error",
                "message": error_msg,
                "status_code": response.status_code,
            }

        return {"status": "success", "data": response_data}

    # -- static helpers (exposed for testing) --------------------------------

    @staticmethod
    def _parse_abbreviated_date(text: str, year: int) -> date | None:
        """Parse abbreviated date like ``'Mon, Jan 12'`` to a date object.

        Returns ``None`` if the format does not match or parsing fails.
        """
        if ", " not in text:
            return None
        try:
            parts = text.split(", ")
            if len(parts) != 2:
                return None
            month_day = parts[1].split()
            if len(month_day) != 2:
                return None
            month_num = _MONTH_MAP.get(month_day[0])
            if month_num is None:
                return None
            day_num = int(month_day[1])
            return date(year, month_num, day_num)
        except (ValueError, TypeError, IndexError):
            return None

    @staticmethod
    def _monday_of(d: date) -> date:
        """Return the Monday of the ISO week containing *d*."""
        return d - timedelta(days=d.weekday())

    @staticmethod
    def _find_week_log_id(
        data: Any,
        target_week: str,
        _depth: int = 0,
    ) -> int | None:
        """Recursively search for the week-log ID matching *target_week*.

        ``target_week`` is ``"YYYY-MM-DD"`` (always a Monday).
        The ERP API sometimes returns ``"Mon, Jan 12"`` format.
        """
        if _depth > _MAX_RECURSION_DEPTH:
            return None
        if data is None:
            return None

        if isinstance(data, dict):
            week_start = data.get("week_starting", "")
            if isinstance(week_start, str) and week_start:
                # Exact ISO match.
                if week_start == target_week:
                    wid = data.get("id")
                    if wid is not None:
                        try:
                            return int(wid)
                        except (ValueError, TypeError):
                            return None

                # Try abbreviated format  "Mon, Jan 12"
                if ", " in week_start and len(target_week) >= 4:
                    year = int(target_week[:4])
                    parsed = ERPClient._parse_abbreviated_date(
                        week_start,
                        year,
                    )
                    if parsed is not None and parsed.isoformat() == target_week:
                        wid = data.get("id")
                        if wid is not None:
                            try:
                                return int(wid)
                            except (ValueError, TypeError):
                                return None

            # Recurse into dict values.
            for val in data.values():
                found = ERPClient._find_week_log_id(val, target_week, _depth=_depth + 1)
                if found is not None:
                    return found

        elif isinstance(data, list):
            for item in data:
                found = ERPClient._find_week_log_id(item, target_week, _depth=_depth + 1)
                if found is not None:
                    return found

        return None

    @staticmethod
    def _unwrap_person_week_logs(data: Any) -> list[dict[str, Any]]:
        """Normalise the person/list response into a flat list of log dicts.

        Handles three shapes:
        - A plain ``list`` of dicts (passthrough).
        - An envelope ``{"person_week_logs": [{"months_log": [...]}]}``.
        - A JSON-encoded string wrapping either of the above.
        """
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return []

        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]

        if isinstance(data, dict) and "person_week_logs" in data:
            out: list[dict[str, Any]] = []
            for month_data in data.get("person_week_logs", []):
                if isinstance(month_data, dict) and "months_log" in month_data:
                    for item in month_data["months_log"]:
                        if isinstance(item, dict):
                            out.append(item)
            return out

        return []

    @staticmethod
    def _extract_day(
        week_data: dict[str, Any],
        target_date: str,
    ) -> dict[str, Any]:
        """Extract logs for a single day from a detailed week-log response."""
        projects_out: list[dict[str, Any]] = []
        total_hours = 0
        total_minutes = 0

        for project in week_data.get("projects", []):
            day_tasks: list[dict[str, Any]] = []
            for task in project.get("tasks", []):
                for day in task.get("days", []):
                    if day.get("date") == target_date:
                        task_info = {
                            "id": task.get("id"),
                            "description": task.get("description", ""),
                            "hours": int(day.get("hours", 0)),
                            "minutes": int(day.get("minutes", 0)),
                            "decimal_hours": float(day.get("decimal_hours", 0)),
                            "label_id": day.get("label"),
                            "label_option": day.get("label_option"),
                        }
                        day_tasks.append(task_info)
                        total_hours += int(task_info["hours"])
                        total_minutes += int(task_info["minutes"])

            if day_tasks:
                proj_total_hours = sum(t["hours"] for t in day_tasks)
                proj_total_minutes = sum(t["minutes"] for t in day_tasks)
                proj_total_hours += proj_total_minutes // 60
                proj_total_minutes = proj_total_minutes % 60
                projects_out.append(
                    {
                        "project_id": project.get("id"),
                        "project_name": (project.get("subteam") or project.get("team", "Unknown")),
                        "team_name": project.get("team", ""),
                        "tasks": day_tasks,
                        "total_hours": proj_total_hours,
                        "total_minutes": proj_total_minutes,
                        "total_decimal_hours": sum(t["decimal_hours"] for t in day_tasks),
                    }
                )

        total_hours += total_minutes // 60
        total_minutes = total_minutes % 60

        return {
            "date": target_date,
            "projects": projects_out,
            "total_logged_time": {
                "hours": total_hours,
                "minutes": total_minutes,
                "decimal_hours": round(sum(p["total_decimal_hours"] for p in projects_out), 2),
            },
            "total_projects": len(projects_out),
            "total_tasks": sum(len(p["tasks"]) for p in projects_out),
        }

    # -- data-query methods (SEC-01: no ``email`` parameter) -----------------

    async def get_active_projects(self, token: str) -> dict[str, Any]:
        return await self._request("GET", "project-logs/person/active_project_list/", token)

    async def get_log_labels(self, token: str) -> dict[str, Any]:
        return await self._request("GET", "project-logs/log_labels/", token)

    async def get_week_logs(
        self,
        token: str,
        week_starting: str,
    ) -> dict[str, Any]:
        """Fetch the detailed week log for *week_starting* (YYYY-MM-DD)."""
        year = date.fromisoformat(week_starting).year

        list_result = await self._request(
            "GET", "project-logs/person/list/", token, params={"year": year}
        )
        if list_result["status"] != "success":
            return list_result

        data = self._unwrap_person_week_logs(list_result.get("data", []))
        week_log_id = self._find_week_log_id(data, week_starting)
        if week_log_id is None:
            return {
                "status": "error",
                "message": f"Week log not found for week starting {week_starting}",
            }

        return await self._request("GET", f"project-logs/person/get/{week_log_id}/", token)

    async def get_day_logs(
        self,
        token: str,
        date_str: str,
    ) -> dict[str, Any]:
        """Derive a single day's logs from its week log."""
        log_date = date.fromisoformat(date_str)
        monday = self._monday_of(log_date)

        week_result = await self.get_week_logs(token, monday.isoformat())
        if week_result.get("status") != "success":
            return week_result

        week_data = week_result.get("data", {})
        day_info = self._extract_day(week_data, date_str)
        day_info["week_starting"] = monday.isoformat()
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
            result = await self._request(
                "GET",
                "project-logs/person/month-list/",
                token,
                params={"year": cursor.year, "month": cursor.month},
            )
            if result.get("status") != "success":
                last_error = result.get("message", "API error")
            else:
                items, _err = self._extract_log_list(result)
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
            ws_date = self._parse_week_starting_to_date(ws, log_year)
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
        return await self._request(
            "GET",
            "project-logs/person/month-list/",
            token,
            # No int() coercion needed: MCP/FastMCP handles type coercion
            # for tool parameters before they reach this method.
            params={"year": year, "month": month},
        )

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
        # No int() coercion needed: MCP/FastMCP handles type coercion
        # for tool parameters before they reach this method.

        # Validate project exists in active projects.
        ap_result = await self._request("GET", "project-logs/person/active_project_list/", token)
        if ap_result["status"] != "success":
            return ap_result

        active_projects = ap_result.get("data", [])
        nsubteam_id, active_team_name = self._find_active_project(active_projects, project_id)

        if nsubteam_id is None:
            return {
                "status": "error",
                "message": (f"Project {project_id} not found in active projects."),
            }

        effective_label = int(label_id) if label_id is not None else _DEFAULT_LABEL_ID

        # Compute week Monday.
        log_date = date.fromisoformat(date_str)
        monday = self._monday_of(log_date)
        monday_str = monday.isoformat()
        year = monday.year

        # Look up existing week log.
        list_result = await self._request(
            "GET", "project-logs/person/list/", token, params={"year": year}
        )

        week_log_id: int | None = None
        if isinstance(list_result, dict) and list_result.get("status") == "success":
            data = self._unwrap_person_week_logs(list_result.get("data", []))
            week_log_id = self._find_week_log_id(data, monday_str)

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
                    "subteam": nsubteam_id,
                    "label_id": effective_label,
                }
            ],
        }
        slack_result = await self._request(
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
        # No int() coercion needed: MCP/FastMCP handles type coercion
        # for tool parameters before they reach this method.
        log_date = date.fromisoformat(date_str)
        monday = self._monday_of(log_date)
        monday_str = monday.isoformat()
        year = monday.year

        list_result = await self._request(
            "GET", "project-logs/person/list/", token, params={"year": year}
        )
        if list_result.get("status") != "success":
            return {
                "status": "error",
                "message": "Could not load week logs list.",
            }

        data = self._unwrap_person_week_logs(list_result.get("data", []))
        week_log_id = self._find_week_log_id(data, monday_str)
        if week_log_id is None:
            return {
                "status": "error",
                "message": (f"No week log for week starting {monday_str}.  Nothing to delete."),
            }

        get_result = await self._request("GET", f"project-logs/person/get/{week_log_id}/", token)
        if get_result.get("status") != "success":
            return get_result

        week_log_data = get_result.get("data", {})
        if "modified_at" not in week_log_data:
            return {
                "status": "error",
                "message": "Week log data missing modified_at.",
            }

        # Resolve active-project team name.
        ap_result = await self._request("GET", "project-logs/person/active_project_list/", token)
        active_team_name: str | None = None
        if ap_result.get("status") == "success":
            _, active_team_name = self._find_active_project(ap_result.get("data", []), project_id)
            active_team_name = active_team_name or None

        project_data = self._match_project_in_week_log(week_log_data, active_team_name)
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

        save_result = await self._request(
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
        list_result = await self._request(
            "GET", "project-logs/person/list/", token, params={"year": year}
        )
        if list_result.get("status") != "success":
            return list_result

        data = self._unwrap_person_week_logs(list_result.get("data", []))
        week_log_id = self._find_week_log_id(data, week_starting)
        if week_log_id is None:
            return {
                "status": "error",
                "message": (f"Week log not found for week starting {week_starting}"),
            }

        return await self._request(
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
        """Batch-create logs.  SEC-05: capped at 31 days."""
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
        monday = self._monday_of(log_date)
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
        project_id_int = int(project_id)

        for project in projects:
            try:
                if int(project.get("id", -1)) == project_id_int:
                    return {
                        "status": "success",
                        "exists": True,
                        "person_week_project_id": project.get("id"),
                        "week_starting": monday_str,
                        "project_id": project_id_int,
                        "message": "PersonWeekProject exists.",
                    }
            except (ValueError, TypeError):
                continue

        return {
            "status": "success",
            "exists": False,
            "week_starting": monday_str,
            "project_id": project_id_int,
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
        get_result = await self._request("GET", f"project-logs/person/get/{week_log_id}/", token)
        if get_result.get("status") != "success":
            return get_result

        week_log_data = get_result.get("data", {})
        if "modified_at" not in week_log_data:
            return {
                "status": "error",
                "message": "Week log data missing modified_at.",
            }

        project_data = self._match_project_in_week_log(week_log_data, active_team_name)
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

        return await self._request(
            "PATCH",
            f"project-logs/person/person-week-log/save/{week_log_id}/",
            token,
            data=week_log_data,
        )

    @staticmethod
    def _find_active_project(
        active_projects: list[Any],
        project_id: int,
    ) -> tuple[int | None, str]:
        """Find a project by ID in the active projects list.

        Returns (subteam_id, team_name) or (None, "") if not found.
        """
        for proj in active_projects:
            if isinstance(proj, dict):
                try:
                    if int(proj.get("id", -1)) == project_id:
                        team_name = proj.get("team") or proj.get("subteam") or ""
                        return int(proj["id"]), team_name
                except (ValueError, TypeError):
                    continue
        return None, ""

    @staticmethod
    def _match_project_in_week_log(
        week_log_data: dict[str, Any],
        active_team_name: str | None,
    ) -> dict[str, Any] | None:
        """Find the project in the week log by matching team name."""
        if not active_team_name:
            return None
        search = active_team_name.strip().lower()

        # First pass: exact match on team or subteam.
        for project in week_log_data.get("projects", []):
            pteam = (project.get("team") or "").strip().lower()
            psub = (project.get("subteam") or "").strip().lower()
            if pteam == search or psub == search:
                return cast(dict[str, Any], project)

        # Second pass: try "team / subteam" combined format.
        for project in week_log_data.get("projects", []):
            pteam = (project.get("team") or "").strip().lower()
            psub = (project.get("subteam") or "").strip().lower()
            combined = f"{pteam} / {psub}".strip()
            if search in combined:
                return cast(dict[str, Any], project)

        return None

    @staticmethod
    def _parse_week_starting_to_date(
        week_start_str: str,
        log_year: int,
    ) -> date | None:
        """Parse ``week_starting`` from API to a :class:`date`."""
        if not week_start_str or not isinstance(week_start_str, str):
            return None
        try:
            return date.fromisoformat(week_start_str.strip())
        except ValueError:
            pass
        return ERPClient._parse_abbreviated_date(week_start_str, log_year)

    @staticmethod
    def _extract_log_list(
        result: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Extract a list of log dicts from a month-list API response."""
        if result.get("status") != "success":
            msg = result.get("message") or "API request failed"
            return [], msg

        data = result.get("data")
        if data is None:
            return [], None

        out: list[dict[str, Any]] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    out.append(item)
            return out, None

        if isinstance(data, dict):
            for key in ("results", "data", "items", "logs", "month_logs"):
                val = data.get(key)
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            out.append(item)
                    return out, None

        return [], None
