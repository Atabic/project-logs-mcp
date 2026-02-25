"""Tests for clients/_base.py -- TTLCache, BaseERPClient, and security requirements.

Migrated from test_erp_client.py with import path updates:
  erp_client.ERPClient -> clients._base.BaseERPClient
  erp_client.TTLCache  -> clients._base.TTLCache
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator
from datetime import date
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
import respx

from clients._base import BaseERPClient, TTLCache

# =========================================================================
# Fixtures
# =========================================================================

BASE_URL = "https://erp.example.com/api/v1"
ALLOWED_DOMAIN = "arbisoft.com"


@pytest.fixture
def cache() -> TTLCache:
    return TTLCache(maxsize=5, ttl=2.0)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[BaseERPClient, None]:
    c = BaseERPClient(base_url=BASE_URL, allowed_domain=ALLOWED_DOMAIN)
    yield c
    await c.close()


# =========================================================================
# TTLCache tests
# =========================================================================


class TestTTLCache:
    """Unit tests for the TTLCache class."""

    def test_insert_and_get(self, cache: TTLCache) -> None:
        cache._put("k1", "v1")
        assert cache._get("k1") == "v1"

    def test_get_missing_key_returns_none(self, cache: TTLCache) -> None:
        assert cache._get("nonexistent") is None

    def test_overwrite_existing_key(self, cache: TTLCache) -> None:
        cache._put("k", "old")
        cache._put("k", "new")
        assert cache._get("k") == "new"
        assert len(cache) == 1

    def test_expiry(self) -> None:
        """Entries expire after TTL seconds (using monotonic mock)."""
        c = TTLCache(maxsize=10, ttl=1.0)
        # Use a fixed monotonic clock.
        with patch("clients._base.time.monotonic", return_value=100.0):
            c._put("k", "v")
        # Still valid at 100.9
        with patch("clients._base.time.monotonic", return_value=100.9):
            assert c._get("k") == "v"
        # Expired at 101.0 (>= now + ttl)
        with patch("clients._base.time.monotonic", return_value=101.0):
            assert c._get("k") is None
        # Entry should have been evicted.
        assert len(c) == 0

    def test_lru_eviction(self) -> None:
        """Least-recently-used entry is evicted when maxsize is reached."""
        c = TTLCache(maxsize=3, ttl=3600.0)
        c._put("a", 1)
        c._put("b", 2)
        c._put("c", 3)

        # Access "a" to make it recently used.
        c._get("a")

        # Inserting "d" should evict "b" (least recently used).
        c._put("d", 4)
        assert len(c) == 3
        assert c._get("b") is None  # evicted
        assert c._get("a") == 1  # still present (was accessed)
        assert c._get("c") == 3
        assert c._get("d") == 4

    def test_maxsize_enforcement(self) -> None:
        """Cache never exceeds maxsize."""
        c = TTLCache(maxsize=3, ttl=3600.0)
        for i in range(10):
            c._put(f"k{i}", i)
        assert len(c) <= 3

    def test_maxsize_one(self) -> None:
        c = TTLCache(maxsize=1, ttl=3600.0)
        c._put("a", 1)
        c._put("b", 2)
        assert len(c) == 1
        assert c._get("a") is None
        assert c._get("b") == 2

    def test_clear(self, cache: TTLCache) -> None:
        cache._put("x", 1)
        cache._put("y", 2)
        cache.clear()
        assert len(cache) == 0
        assert cache._get("x") is None

    def test_invalid_maxsize_raises(self) -> None:
        with pytest.raises(ValueError, match="maxsize"):
            TTLCache(maxsize=0, ttl=1.0)

    def test_invalid_ttl_raises(self) -> None:
        with pytest.raises(ValueError, match="ttl"):
            TTLCache(maxsize=10, ttl=0)

    async def test_aget_returns_value(self, cache: TTLCache) -> None:
        """aget should return cached value via async lock."""
        cache._put("k", "v")
        assert await cache.aget("k") == "v"

    async def test_aget_returns_none_for_missing(self, cache: TTLCache) -> None:
        assert await cache.aget("missing") is None

    async def test_aput_stores_value(self, cache: TTLCache) -> None:
        await cache.aput("k", "v")
        assert cache._get("k") == "v"

    async def test_aclear_empties_cache(self, cache: TTLCache) -> None:
        await cache.aput("a", 1)
        await cache.aput("b", 2)
        await cache.aclear()
        assert len(cache) == 0

    def test_len_excludes_expired(self) -> None:
        """__len__ should not count expired entries."""
        c = TTLCache(maxsize=10, ttl=1.0)
        with patch("clients._base.time.monotonic", return_value=100.0):
            c._put("k1", "v1")
            c._put("k2", "v2")
        # Before expiry
        with patch("clients._base.time.monotonic", return_value=100.5):
            assert len(c) == 2
        # After expiry
        with patch("clients._base.time.monotonic", return_value=101.0):
            assert len(c) == 0


# =========================================================================
# BaseERPClient construction / SEC-07
# =========================================================================


class TestBaseERPClientConstruction:
    """Startup URL scheme checks (SEC-07)."""

    def test_https_remote_allowed(self) -> None:
        client = BaseERPClient("https://erp.example.com/api/v1/", "example.com")
        assert client._base_url == "https://erp.example.com/api/v1"

    def test_http_localhost_allowed(self) -> None:
        client = BaseERPClient("http://localhost:9999/api/v1/", "example.com")
        assert "localhost" in client._base_url

    def test_http_127_allowed(self) -> None:
        BaseERPClient("http://127.0.0.1:8000/api/v1/", "example.com")

    def test_http_remote_rejected(self) -> None:
        with pytest.raises(ValueError, match="Non-HTTPS"):
            BaseERPClient("http://remote-host:9999/api/v1/", "example.com")

    def test_http_remote_ip_rejected(self) -> None:
        with pytest.raises(ValueError, match="Non-HTTPS"):
            BaseERPClient("http://10.0.0.5:8000/api/v1/", "example.com")

    def test_follow_redirects_disabled(self) -> None:
        """SEC-06: follow_redirects must be False."""
        client = BaseERPClient(BASE_URL, ALLOWED_DOMAIN)
        assert client._http.follow_redirects is False

    def test_empty_allowed_domain_rejected(self) -> None:
        with pytest.raises(ValueError, match="allowed_domain must not be empty"):
            BaseERPClient(BASE_URL, allowed_domain="")

    def test_whitespace_allowed_domain_rejected(self) -> None:
        with pytest.raises(ValueError, match="allowed_domain must not be empty"):
            BaseERPClient(BASE_URL, allowed_domain="   ")


# =========================================================================
# exchange_google_token tests
# =========================================================================


class TestExchangeGoogleToken:
    """Tests for the Google token exchange including caching and domain check."""

    @respx.mock
    async def test_successful_exchange(self, client: BaseERPClient) -> None:
        respx.post(f"{BASE_URL}/core/google-login/").mock(
            return_value=httpx.Response(
                200,
                json={"token": "erp-tok-123", "email": "user@arbisoft.com"},
            )
        )
        erp_token, email = await client.exchange_google_token("goog-tok")
        assert erp_token == "erp-tok-123"
        assert email == "user@arbisoft.com"

    @respx.mock
    async def test_cache_key_is_sha256(self, client: BaseERPClient) -> None:
        """SEC-03: cache key must be SHA-256 hash of the Google token."""
        google_token = "my-secret-google-token"
        expected_key = hashlib.sha256(google_token.encode()).hexdigest()

        respx.post(f"{BASE_URL}/core/google-login/").mock(
            return_value=httpx.Response(
                200,
                json={"token": "erp-tok-ab", "email": "u@arbisoft.com"},
            )
        )
        await client.exchange_google_token(google_token)
        # Verify the cache stores the result under the SHA-256 key.
        assert client._token_cache._get(expected_key) == ("erp-tok-ab", "u@arbisoft.com")

    @respx.mock
    async def test_cached_result_returned_on_second_call(self, client: BaseERPClient) -> None:
        route = respx.post(f"{BASE_URL}/core/google-login/").mock(
            return_value=httpx.Response(
                200,
                json={"token": "erp-tok-ab", "email": "u@arbisoft.com"},
            )
        )
        first = await client.exchange_google_token("goog-tok")
        second = await client.exchange_google_token("goog-tok")
        assert first == second
        assert route.call_count == 1  # Only one HTTP call

    @respx.mock
    async def test_domain_restriction_rejects_gmail(self, client: BaseERPClient) -> None:
        """SEC-02: reject non-allowed domain emails."""
        respx.post(f"{BASE_URL}/core/google-login/").mock(
            return_value=httpx.Response(
                200,
                json={"token": "tok-1234567", "email": "user@gmail.com"},
            )
        )
        with pytest.raises(ValueError, match="not allowed"):
            await client.exchange_google_token("goog-tok")

    @respx.mock
    async def test_domain_restriction_rejects_other_domain(self, client: BaseERPClient) -> None:
        respx.post(f"{BASE_URL}/core/google-login/").mock(
            return_value=httpx.Response(
                200,
                json={"token": "tok-1234567", "email": "user@evil.com"},
            )
        )
        with pytest.raises(ValueError, match="not allowed"):
            await client.exchange_google_token("goog-tok")

    async def test_empty_token_raises(self, client: BaseERPClient) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            await client.exchange_google_token("")

    async def test_whitespace_token_raises(self, client: BaseERPClient) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            await client.exchange_google_token("   ")

    @respx.mock
    async def test_backend_4xx_raises(self, client: BaseERPClient) -> None:
        respx.post(f"{BASE_URL}/core/google-login/").mock(
            return_value=httpx.Response(401, json={"error": "Invalid token"})
        )
        with pytest.raises(ValueError, match="Google token exchange failed"):
            await client.exchange_google_token("bad-tok")

    @respx.mock
    async def test_backend_no_token_raises(self, client: BaseERPClient) -> None:
        respx.post(f"{BASE_URL}/core/google-login/").mock(
            return_value=httpx.Response(200, json={"email": "u@arbisoft.com"})
        )
        with pytest.raises(ValueError, match="did not return a token"):
            await client.exchange_google_token("goog-tok")

    @respx.mock
    async def test_backend_no_email_raises(self, client: BaseERPClient) -> None:
        respx.post(f"{BASE_URL}/core/google-login/").mock(
            return_value=httpx.Response(200, json={"token": "tok"})
        )
        with pytest.raises(ValueError, match="did not return an email"):
            await client.exchange_google_token("goog-tok")

    async def test_oversized_token_raises(self, client: BaseERPClient) -> None:
        with pytest.raises(ValueError, match="exceeds maximum length"):
            await client.exchange_google_token("x" * 4097)


# =========================================================================
# _request tests
# =========================================================================


class TestRequest:
    """Tests for the generic _request helper."""

    @respx.mock
    async def test_success_returns_data(self, client: BaseERPClient) -> None:
        respx.get(f"{BASE_URL}/some/endpoint/").mock(
            return_value=httpx.Response(200, json={"foo": "bar"})
        )
        result = await client._request("GET", "some/endpoint/", "my-token")
        assert result["status"] == "success"
        assert result["data"] == {"foo": "bar"}

    @respx.mock
    async def test_4xx_returns_error_dict(self, client: BaseERPClient) -> None:
        respx.get(f"{BASE_URL}/fail/").mock(
            return_value=httpx.Response(403, json={"detail": "Forbidden"})
        )
        result = await client._request("GET", "fail/", "tok")
        assert result["status"] == "error"
        assert result["status_code"] == 403
        assert "Forbidden" in result["message"]

    @respx.mock
    async def test_5xx_returns_error_dict(self, client: BaseERPClient) -> None:
        respx.get(f"{BASE_URL}/crash/").mock(
            return_value=httpx.Response(500, json={"error": "Internal"})
        )
        result = await client._request("GET", "crash/", "tok")
        assert result["status"] == "error"
        assert result["status_code"] == 500

    @respx.mock
    async def test_no_stack_trace_in_error(self, client: BaseERPClient) -> None:
        """SEC-04: error dicts must not contain stack traces."""
        respx.get(f"{BASE_URL}/err/").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        result = await client._request("GET", "err/", "tok")
        result_str = str(result)
        assert "Traceback" not in result_str
        assert "traceback" not in result

    @respx.mock
    async def test_auth_header_sent(self, client: BaseERPClient) -> None:
        route = respx.get(f"{BASE_URL}/check/").mock(return_value=httpx.Response(200, json={}))
        await client._request("GET", "check/", "my-secret-token")
        sent_headers = route.calls.last.request.headers
        assert sent_headers["authorization"] == "Token my-secret-token"

    @respx.mock
    async def test_transport_error_returns_error_dict(self, client: BaseERPClient) -> None:
        respx.get(f"{BASE_URL}/timeout/").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        result = await client._request("GET", "timeout/", "tok")
        assert result["status"] == "error"
        assert result["message"] == "ERP service temporarily unavailable."


# =========================================================================
# Static helper tests
# =========================================================================


class TestFindWeekLogId:
    """Tests for the week-log ID lookup from person/list response data."""

    def test_iso_format_match(self) -> None:
        data = [
            {"id": 100, "week_starting": "2026-01-05"},
            {"id": 200, "week_starting": "2026-01-12"},
        ]
        assert BaseERPClient._find_week_log_id(data, "2026-01-12") == 200

    def test_abbreviated_format_match(self) -> None:
        """Match 'Mon, Jan 12' format."""
        data = [{"id": 300, "week_starting": "Mon, Jan 12"}]
        assert BaseERPClient._find_week_log_id(data, "2026-01-12") == 300

    def test_nested_dict_search(self) -> None:
        """Recursively search nested structures."""
        data: dict[str, Any] = {
            "person_week_logs": [
                {"months_log": [{"id": 50, "week_starting": "2026-03-02"}]}
            ]
        }
        assert BaseERPClient._find_week_log_id(data, "2026-03-02") == 50

    def test_no_match_returns_none(self) -> None:
        data = [{"id": 1, "week_starting": "2026-06-01"}]
        assert BaseERPClient._find_week_log_id(data, "2026-01-01") is None

    def test_none_data_returns_none(self) -> None:
        assert BaseERPClient._find_week_log_id(None, "2026-01-01") is None

    def test_recursion_depth_limit(self) -> None:
        """_find_week_log_id should return None when depth exceeds limit."""
        # Build a deeply nested structure that exceeds _MAX_RECURSION_DEPTH (20).
        data: dict[str, Any] = {"id": 99, "week_starting": "2026-01-05"}
        for _ in range(25):
            data = {"nested": data}
        assert BaseERPClient._find_week_log_id(data, "2026-01-05") is None

    def test_non_monday_no_match(self) -> None:
        """A week_starting that's not a Monday should still match if it appears in data."""
        # The find function doesn't validate Monday -- it just matches strings.
        data = [{"id": 42, "week_starting": "2026-01-07"}]  # Wednesday
        assert BaseERPClient._find_week_log_id(data, "2026-01-07") == 42


class TestExtractDay:
    """Tests for extracting a single day from week-log data."""

    def test_filters_to_target_date(self) -> None:
        week_data: dict[str, Any] = {
            "projects": [
                {
                    "id": 10,
                    "team": "Proj A",
                    "subteam": "Sub A",
                    "tasks": [
                        {
                            "id": 1,
                            "description": "Task 1",
                            "days": [
                                {
                                    "date": "2026-01-05",
                                    "hours": 3,
                                    "minutes": 0,
                                    "decimal_hours": 3.0,
                                },
                                {
                                    "date": "2026-01-06",
                                    "hours": 5,
                                    "minutes": 30,
                                    "decimal_hours": 5.5,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
        result = BaseERPClient._extract_day(week_data, "2026-01-06")
        assert result["date"] == "2026-01-06"
        assert len(result["projects"]) == 1
        assert result["projects"][0]["tasks"][0]["hours"] == 5

    def test_no_logs_for_date(self) -> None:
        week_data: dict[str, Any] = {
            "projects": [
                {
                    "id": 10,
                    "team": "Proj",
                    "tasks": [
                        {
                            "id": 1,
                            "description": "Task",
                            "days": [
                                {
                                    "date": "2026-01-05",
                                    "hours": 1,
                                    "minutes": 0,
                                    "decimal_hours": 1.0,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
        result = BaseERPClient._extract_day(week_data, "2026-01-07")
        assert result["projects"] == []
        assert result["total_logged_time"]["hours"] == 0


class TestUnwrapPersonWeekLogs:
    def test_flat_list_passthrough(self) -> None:
        data = [{"id": 1}, {"id": 2}]
        assert BaseERPClient._unwrap_person_week_logs(data) == data

    def test_envelope_unwrap(self) -> None:
        data = {
            "person_week_logs": [
                {"months_log": [{"id": 10}, {"id": 20}]},
                {"months_log": [{"id": 30}]},
            ]
        }
        result = BaseERPClient._unwrap_person_week_logs(data)
        assert result == [{"id": 10}, {"id": 20}, {"id": 30}]

    def test_string_json_unwrap(self) -> None:
        data = json.dumps([{"id": 1}])
        result = BaseERPClient._unwrap_person_week_logs(data)
        assert result == [{"id": 1}]


class TestParseWeekStartingToDate:
    """Tests for _parse_week_starting_to_date helper."""

    def test_iso_format(self) -> None:
        result = BaseERPClient._parse_week_starting_to_date("2026-01-05", 2026)
        assert result == date(2026, 1, 5)

    def test_abbreviated_format(self) -> None:
        result = BaseERPClient._parse_week_starting_to_date("Mon, Jan 5", 2026)
        assert result == date(2026, 1, 5)

    def test_empty_string_returns_none(self) -> None:
        assert BaseERPClient._parse_week_starting_to_date("", 2026) is None

    def test_none_returns_none(self) -> None:
        # Type ignore since we're testing defensive behavior
        assert BaseERPClient._parse_week_starting_to_date(None, 2026) is None  # type: ignore[arg-type]

    def test_garbage_returns_none(self) -> None:
        assert BaseERPClient._parse_week_starting_to_date("not a date", 2026) is None


class TestMondayOf:
    def test_monday_returns_same(self) -> None:
        assert BaseERPClient._monday_of(date(2026, 1, 5)) == date(2026, 1, 5)

    def test_wednesday(self) -> None:
        assert BaseERPClient._monday_of(date(2026, 1, 7)) == date(2026, 1, 5)

    def test_sunday(self) -> None:
        assert BaseERPClient._monday_of(date(2026, 1, 11)) == date(2026, 1, 5)
