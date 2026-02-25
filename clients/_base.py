"""Base ERP client with TTL cache and HTTP transport.

Provides ``BaseERPClient`` -- the stateless async HTTP client for the
Arbisoft ERP time-logging API.  Every data-query method takes ``token: str``
as the first positional argument.  The token is forwarded as
``Authorization: Token <value>`` on every request.  No method accepts an
``email`` parameter (SEC-01).

Security controls implemented:
    SEC-01  No ``email`` parameter on data-query methods.
    SEC-02  Domain restriction in ``exchange_google_token``.
    SEC-03  SHA-256 hashed cache keys with bounded LRU eviction (500 entries).
    SEC-04  ``_request`` returns error dicts for 4xx/5xx -- never raises.
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

from _constants import MAX_RECURSION_DEPTH, MAX_TOKEN_LENGTH

__all__ = ["BaseERPClient", "TTLCache"]

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
# BaseERPClient
# ---------------------------------------------------------------------------


class BaseERPClient:
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

    async def __aenter__(self) -> BaseERPClient:
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

        if len(google_token) > MAX_TOKEN_LENGTH:
            raise ValueError(f"google_token exceeds maximum length ({MAX_TOKEN_LENGTH})")

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
        if _depth > MAX_RECURSION_DEPTH:
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
                    parsed = BaseERPClient._parse_abbreviated_date(
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
                found = BaseERPClient._find_week_log_id(val, target_week, _depth=_depth + 1)
                if found is not None:
                    return found

        elif isinstance(data, list):
            for item in data:
                found = BaseERPClient._find_week_log_id(item, target_week, _depth=_depth + 1)
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
        return BaseERPClient._parse_abbreviated_date(week_start_str, log_year)

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
