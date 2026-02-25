# Contributing to ERP MCP Server

This guide walks you through adding new MCP tools that expose ERP API functionality.

## Architecture Overview

```
server.py           FastMCP instance, GoogleProvider auth, middleware, health check, lifespan
_auth.py            Auth helpers — get_erp_token(), tool_error_handler(), check_erp_result()
_constants.py       Shared constants — MAX_FILL_DAYS, MAX_QUERY_DAYS, MAX_DESCRIPTION_LEN, etc.
clients/
  __init__.py       ERPClientRegistry (dataclass), get_registry(), set_registry()
  _base.py          BaseERPClient + TTLCache — HTTP transport, token exchange, static helpers
  timelogs.py       TimelogsClient — composition over inheritance, delegates to BaseERPClient._request()
tools/
  __init__.py       Feature flag loader — AVAILABLE_DOMAINS, SENSITIVE_DOMAINS, load_domains(mcp)
  timelogs.py       11 timelog tools inside register(mcp) pattern
tests/
  conftest.py                Sets env vars before module imports
  test_security.py           AST-based security tests (auto-discovers @mcp.tool across tools/*.py)
  test_feature_flags.py      Feature flag loader tests
  test_clients_base.py       BaseERPClient + TTLCache tests (respx-mocked HTTP)
  test_clients_timelogs.py   TimelogsClient tests (respx-mocked HTTP)
  test_tools_timelogs.py     Timelog tool tests (mocked registry + token)
```

**Data flow for every tool call:**

```
Client (Claude/Cursor/etc.)
  -> FastMCP (@mcp.tool closure in tools/{domain}.py)
    -> get_erp_token() extracts Google token, enforces domain, exchanges for ERP token
    -> get_registry().{domain}.{method}() makes HTTP call to ERP API via BaseERPClient._request()
    -> check_erp_result() converts error dicts to ToolError
  -> Response returned to client
```

## Adding a New Domain

A "domain" is a vertical feature area (e.g., `timelogs`, `payroll`, `invoices`). Adding one touches **6 files** plus tests.

### Step 1: Create the domain client (`clients/payroll.py`)

Use composition -- hold a reference to `BaseERPClient` for HTTP transport:

```python
"""Domain client for ERP payroll operations."""
from __future__ import annotations

from typing import Any

from clients._base import BaseERPClient

__all__ = ["PayrollClient"]


class PayrollClient:
    """High-level operations on ERP payroll data.

    All public methods take ``token: str`` as the first argument (SEC-01).
    """

    def __init__(self, base: BaseERPClient) -> None:
        self._base = base

    async def get_payslips(self, token: str, year: int, month: int) -> dict[str, Any]:
        return await self._base._request(
            "GET", "payroll/payslips/", token, params={"year": year, "month": month}
        )
```

**Rules:**
- First parameter is always `token: str`
- Return type is always `dict[str, Any]`
- Delegate all HTTP I/O to `self._base._request()` -- never create your own `httpx` client
- Use `BaseERPClient` static helpers (e.g., `_parse_abbreviated_date()`) when needed

### Step 2: Register the client in the registry (`clients/__init__.py`)

Add a field to `ERPClientRegistry` and wire it up in `__post_init__`:

```python
from clients.payroll import PayrollClient

@dataclass
class ERPClientRegistry:
    base: BaseERPClient
    timelogs: TimelogsClient = field(init=False)
    payroll: PayrollClient = field(init=False)       # <-- add

    def __post_init__(self) -> None:
        self.timelogs = TimelogsClient(self.base)
        self.payroll = PayrollClient(self.base)      # <-- add
```

### Step 3: Create the tools module (`tools/payroll.py`)

Each domain gets a `register(mcp)` function containing `@mcp.tool` closures:

```python
"""Payroll MCP tools — payslip and compensation operations."""
from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP

from _auth import check_erp_result, get_erp_token, tool_error_handler
from clients import get_registry

logger = logging.getLogger("erp_mcp.server")

__all__ = ["register"]


def register(mcp: FastMCP) -> None:
    """Register all payroll tools on the given FastMCP instance."""

    @mcp.tool
    @tool_error_handler("Failed to fetch payslips. Please try again.")
    async def payroll_list_payslips(year: int, month: int) -> dict[str, Any]:
        """Get payslips for a specific month.

        Args:
            year: Year (e.g. 2024).
            month: Month number (1-12).
        """
        token, _email = await get_erp_token()
        return check_erp_result(
            await get_registry().payroll.get_payslips(token, year, month)
        )
```

### Step 4: Register the domain in the feature flag loader (`tools/__init__.py`)

```python
AVAILABLE_DOMAINS: dict[str, str] = {
    "timelogs": "tools.timelogs",
    "payroll": "tools.payroll",       # <-- add
}
```

If the domain handles PII or financial data, also add it to `SENSITIVE_DOMAINS` (SEC-08):

```python
SENSITIVE_DOMAINS: frozenset[str] = frozenset({"payroll", "invoices"})
```

Sensitive domains require `ENABLE_SENSITIVE_DOMAINS=true` at runtime. Without it, the server exits on startup.

### Step 5: Update the Dockerfile

The Dockerfile copies `clients/` and `tools/` as directories, so new files inside them are picked up automatically. Only update the Dockerfile if you add a new **top-level** `.py` file outside those directories:

```dockerfile
# Top-level files must be listed explicitly:
COPY --chown=root:root --chmod=644 server.py _auth.py _constants.py ./
# Directories are copied whole:
COPY --chown=root:root --chmod=644 clients/ ./clients/
COPY --chown=root:root --chmod=644 tools/ ./tools/
```

### Step 6: Create test files

Create `tests/test_clients_payroll.py` (HTTP-level) and `tests/test_tools_payroll.py` (tool-level). See [Test Patterns](#test-patterns) below.

## Adding a Read Tool to an Existing Domain

Touch **4 files** (2 source + 2 test).

### Step 1: Add client method (`clients/timelogs.py`)

```python
async def get_team_members(self, token: str, team_id: int) -> dict[str, Any]:
    """Fetch team members for a project."""
    return await self._base._request(
        "GET", "project-logs/team/members/", token, params={"team_id": team_id}
    )
```

### Step 2: Add tool closure (`tools/timelogs.py`)

Add inside the existing `register(mcp)` function:

```python
@mcp.tool
@tool_error_handler("Failed to fetch team members. Please try again.")
async def timelogs_list_team_members(team_id: int) -> dict[str, Any]:
    """Get team members for a project.

    Args:
        team_id: Project/subteam ID (from timelogs_list_projects).
    """
    token, _email = await get_erp_token()
    return check_erp_result(
        await get_registry().timelogs.get_team_members(token, team_id)
    )
```

### Step 3: Add client test (`tests/test_clients_timelogs.py`)

```python
@respx.mock
async def test_get_team_members(self, timelogs_client: TimelogsClient) -> None:
    respx.get(f"{BASE_URL}/project-logs/team/members/").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "Alice"}])
    )
    result = await timelogs_client.get_team_members(token="tok", team_id=42)
    assert result["status"] == "success"
    assert result["data"][0]["name"] == "Alice"
```

### Step 4: Add tool test (`tests/test_tools_timelogs.py`)

```python
class TestTimelogsListTeamMembers:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        mock_timelogs.get_team_members.return_value = {"status": "success", "data": []}
        set_registry(mock_registry)
        fn = _get_tool_fn("timelogs_list_team_members")
        with _patch_token(valid_token):
            result = await fn(team_id=42)
        assert result["status"] == "success"
        mock_timelogs.get_team_members.assert_awaited_once_with("erp-token-abc", 42)
```

**Important:** Update the `mock_timelogs` fixture with a return value for your new method. Without this, `AsyncMock` returns another mock instead of a dict, and `check_erp_result()` silently passes.

## Adding a Write Tool

Same steps as a read tool, **plus** three additional requirements:

### 1. Audit logging

Emit a structured `WRITE_OP` log line after every successful write:

```python
result = check_erp_result(await client.some_write(token, ...))
logger.info(
    "WRITE_OP tool=payroll_update_entry user=%s param1=%s param2=%s",
    email, param1, param2,
)
return result
```

Use `token, email = await get_erp_token()` (not `_email`) to capture the email for the log.

### 2. Input validation

Validate before calling the client:

```python
# Description length
if len(description) > MAX_DESCRIPTION_LEN:
    raise ToolError(f"Description too long (max {MAX_DESCRIPTION_LEN} characters)")

# Hours bounds
if hours <= 0 or hours > 24:
    raise ValueError(f"hours must be between 0 (exclusive) and 24 (inclusive), got {hours}")

# Date range cap (SEC-05)
if day_count > MAX_FILL_DAYS:
    raise ValueError(f"Date range spans {day_count} days, exceeding the maximum of {MAX_FILL_DAYS} days.")
```

Constants are defined in `_constants.py`.

### 3. Audit log test coverage

Add your tool to `TestAuditLogCoverage.WRITE_TOOLS` in the tool test file:

```python
WRITE_TOOLS = [
    # ... existing entries ...
    ("payroll_update_entry", {"param1": "value1", "param2": "value2"}),
]
```

The guard test `test_all_write_tools_covered` auto-discovers `WRITE_OP` strings in `tools/*.py` via AST and will fail if your tool is missing from this list.

## Tool Naming Convention

All tools follow the pattern: **`{domain}_{verb}_{resource}`**

Standard verbs: `list`, `get`, `upsert`, `delete`, `complete`, `fill`, `check`

The `test_security.py::TestToolNamingConvention` test enforces that every tool in `tools/{domain}.py` starts with `{domain}_`. Adding a tool named `get_payslips` in `tools/payroll.py` will fail the test -- it must be `payroll_get_payslips`.

## Tool Decorator Pattern

Every tool inside `register(mcp)` follows this exact stack:

```python
@mcp.tool
@tool_error_handler("Generic error message.")
async def domain_verb_resource(...) -> dict[str, Any]:
    """Docstring becomes the tool description shown to AI clients.

    Args:
        param: Description for AI clients.
    """
    token, email = await get_erp_token()         # SEC-01/02: identity from OAuth only
    return check_erp_result(                       # error dict -> ToolError
        await get_registry().domain.method(token, ...)
    )
```

- `@mcp.tool` -- registers the function as an MCP tool
- `@tool_error_handler("...")` -- catches exceptions, converts to `ToolError` with generic message (SEC-04)
- `get_erp_token()` -- extracts Google token, enforces domain check (SEC-02), exchanges for ERP token
- `check_erp_result()` -- converts `{"status": "error", ...}` dicts to `ToolError`
- `get_registry()` -- returns `ERPClientRegistry` (raises `RuntimeError` if lifespan hasn't run)

**Tool parameters:**
- No `email` parameter -- identity comes from the OAuth token (SEC-01)
- Use type hints for all parameters -- FastMCP uses them to generate the tool schema
- Add an `Args:` section to the docstring for each parameter

## Test Patterns

### Tool tests (`tests/test_tools_{domain}.py`)

Tool tests mock the entire client layer and the OAuth token, testing only the tool function logic:

```python
# Retrieve the registered tool function by name
fn = _get_tool_fn("timelogs_list_projects")

# _get_tool_fn looks up the function in mcp.local_provider._components
```

**Key fixtures and helpers:**

| Name | Purpose |
|------|---------|
| `_get_tool_fn(name)` | Retrieves tool function from `mcp.local_provider._components` |
| `_make_access_token()` | Builds a fake `AccessToken` with GoogleProvider claims structure |
| `_patch_token(token)` | Patches `_auth.get_access_token` to return a fake token |
| `mock_timelogs` | `AsyncMock` with return values for every `TimelogsClient` method |
| `mock_registry` | `AsyncMock` wrapping `mock_timelogs` + mocked `base.exchange_google_token` |
| `set_registry(mock_registry)` | Injects mock registry into the global slot |

**Pattern for each test class:**

```python
class TestTimelogsListProjects:
    async def test_calls_client(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        set_registry(mock_registry)
        fn = _get_tool_fn("timelogs_list_projects")
        with _patch_token(valid_token):
            result = await fn()
        assert result["status"] == "success"
        mock_timelogs.get_active_projects.assert_awaited_once_with("erp-token-abc")

    async def test_unexpected_error_raises_tool_error(
        self, mock_timelogs: AsyncMock, mock_registry: AsyncMock, valid_token: AccessToken
    ) -> None:
        mock_timelogs.get_active_projects.side_effect = RuntimeError("DB down")
        set_registry(mock_registry)
        fn = _get_tool_fn("timelogs_list_projects")
        with _patch_token(valid_token):
            with pytest.raises(ToolError, match="Failed to fetch active projects"):
                await fn()
```

### Client HTTP tests (`tests/test_clients_{domain}.py`)

Client tests use `respx` to mock HTTP calls and test actual client logic:

```python
BASE_URL = "https://erp.example.com/api/v1"

@pytest_asyncio.fixture
async def base_client() -> AsyncGenerator[BaseERPClient, None]:
    c = BaseERPClient(base_url=BASE_URL, allowed_domain="arbisoft.com")
    yield c
    await c.close()

@pytest.fixture
def timelogs_client(base_client: BaseERPClient) -> TimelogsClient:
    return TimelogsClient(base_client)

@respx.mock
async def test_get_active_projects(self, timelogs_client: TimelogsClient) -> None:
    respx.get(f"{BASE_URL}/project-logs/person/active_project_list/").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "team": "Alpha"}])
    )
    result = await timelogs_client.get_active_projects(token="tok")
    assert result["status"] == "success"
```

### Security tests (`tests/test_security.py`)

Security tests auto-discover tools via AST scanning of `tools/*.py`. You do not need to register new tools manually -- the following are enforced automatically:

- **SEC-01 (no email param):** `TestSEC01NoEmailParam` scans both AST and runtime signatures
- **Tool naming:** `TestToolNamingConvention` verifies `{domain}_` prefix on every tool

### Feature flag tests (`tests/test_feature_flags.py`)

Tests for `tools/__init__.py` -- domain loading, unknown domain handling, and sensitive domain gating.

## Feature Flags

Two environment variables control which domains are loaded:

| Variable | Purpose | Default |
|----------|---------|---------|
| `ENABLED_DOMAINS` | Comma-separated list of domains to activate | `timelogs` |
| `ENABLE_SENSITIVE_DOMAINS` | Must be `true` to load domains in `SENSITIVE_DOMAINS` | (unset) |

Behavior:
- Unknown domain names in `ENABLED_DOMAINS` are logged and skipped
- If **no** valid domains load, the server exits (`SystemExit(1)`)
- Requesting a sensitive domain without `ENABLE_SENSITIVE_DOMAINS=true` exits immediately

Example: `ENABLED_DOMAINS=timelogs,payroll ENABLE_SENSITIVE_DOMAINS=true`

## Security Compliance Checklist

Every tool must comply with these controls. The test suite enforces them automatically.

| Control | Rule | Enforced by |
|---------|------|-------------|
| SEC-01 | No `email` parameter on any tool | `TestSEC01NoEmailParam` (AST + runtime inspection) |
| SEC-02 | Domain restriction via Google token | `get_erp_token()` checks `hd` claim + email suffix |
| SEC-03 | Hashed cache keys | `TTLCache` uses SHA-256 internally |
| SEC-04 | No stack traces in responses | `@tool_error_handler` catches all exceptions; generic message to client |
| SEC-05 | Date range caps on bulk operations | `MAX_FILL_DAYS` (31) and `MAX_QUERY_DAYS` (366) in `_constants.py` |
| SEC-06 | No HTTP redirects | `follow_redirects=False` in `BaseERPClient` |
| SEC-07 | HTTPS enforcement | `BaseERPClient` rejects non-HTTPS for non-localhost targets |
| SEC-08 | Sensitive domain gate | `SENSITIVE_DOMAINS` in `tools/__init__.py` requires `ENABLE_SENSITIVE_DOMAINS=true` |

**What this means for you:**
- Never add an `email` parameter to a tool function -- the test suite will catch it
- Never include exception details in `ToolError` messages -- use the `@tool_error_handler` decorator
- If your tool accepts a date range, enforce `MAX_FILL_DAYS` or `MAX_QUERY_DAYS` from `_constants.py`
- If your domain handles PII/financial data, add it to `SENSITIVE_DOMAINS`

## Pre-Submit Checklist

### All tools

- [ ] Client method added in `clients/{domain}.py` with `token: str` as first parameter
- [ ] `@mcp.tool` + `@tool_error_handler` decorators on the tool closure
- [ ] Tool name follows `{domain}_{verb}_{resource}` convention
- [ ] Tool docstring describes what it does (this is the AI-facing description)
- [ ] `get_erp_token()` used for auth (not manual token handling)
- [ ] `check_erp_result()` wraps the client call
- [ ] `mock_timelogs` (or equivalent domain mock) fixture updated with return value
- [ ] Tool test: happy path + error path (`RuntimeError` -> generic `ToolError`)
- [ ] Client test: `respx`-mocked HTTP test
- [ ] No `email` parameter on the tool function (SEC-01)
- [ ] `pytest tests/ -v`, `ruff check .`, `ruff format --check .`, and `mypy` pass

### Write tools only

- [ ] `WRITE_OP` audit log emitted after success
- [ ] Entry added to `TestAuditLogCoverage.WRITE_TOOLS` in tool test file
- [ ] Input validation (description length, hours bounds, date caps)

### New domains only

- [ ] Client class in `clients/{domain}.py` using composition (`self._base: BaseERPClient`)
- [ ] Field added to `ERPClientRegistry` in `clients/__init__.py`
- [ ] Tool module `tools/{domain}.py` with `register(mcp)` function
- [ ] Entry in `AVAILABLE_DOMAINS` in `tools/__init__.py`
- [ ] Added to `SENSITIVE_DOMAINS` if PII/financial data (SEC-08)
- [ ] Test files: `tests/test_clients_{domain}.py` and `tests/test_tools_{domain}.py`
- [ ] Dockerfile updated if new top-level `.py` file added outside `clients/` or `tools/`
- [ ] README.md tools table updated
