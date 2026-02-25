# Contributing to ERP MCP Server

This guide walks you through adding new MCP tools that expose ERP API functionality.

## Architecture Overview

```
server.py        MCP tool definitions, OAuth, input validation, audit logging
erp_client.py    ERPClient — HTTP calls to ERP API via _request() helper
tests/
  conftest.py    Sets env vars before module imports
  test_server.py      Server tool tests (mocked ERPClient)
  test_erp_client.py  ERPClient tests (respx-mocked HTTP)
```

**Data flow for every tool call:**

```
Client (Claude/Cursor/etc.)
  → FastMCP (@mcp.tool function in server.py)
    → _get_erp_token() extracts Google token, enforces domain, exchanges for ERP token
    → ERPClient method (erp_client.py) makes HTTP call to ERP API
    → _check_erp_result() converts error dicts to ToolError
  → Response returned to client
```

## Adding a Read Tool

Read tools fetch data from the ERP API. Touch **4 files** (2 source + 2 test).

### Step 1: Add ERPClient method (`erp_client.py`)

Simple GET methods are one-liners that delegate to `_request()`:

```python
async def get_user_profile(self, token: str) -> dict[str, Any]:
    """Fetch the authenticated user's profile."""
    return await self._request("GET", "project-logs/person/profile/", token)
```

For methods with query parameters:

```python
async def get_team_members(self, token: str, team_id: int) -> dict[str, Any]:
    return await self._request(
        "GET", "project-logs/team/members/", token, params={"team_id": team_id}
    )
```

**Key rules:**
- First parameter is always `token: str`
- Return type is always `dict[str, Any]`
- `_request()` handles auth headers, error responses, and JSON parsing
- Endpoint strings are relative to `ERP_API_BASE_URL` (e.g. `project-logs/person/...`)

### Step 2: Add MCP tool function (`server.py`)

Add to the "Read tools" section:

```python
@mcp.tool
@_tool_error_handler("Failed to fetch user profile. Please try again.")
async def get_user_profile() -> dict[str, Any]:
    """Get the authenticated user's ERP profile.

    Returns profile information including name, team assignments, and role.
    """
    token, _email = await _get_erp_token()
    return _check_erp_result(await _get_erp().get_user_profile(token))
```

**Pattern breakdown:**
- `@mcp.tool` — registers the function as an MCP tool (the docstring becomes the tool description shown to AI clients)
- `@_tool_error_handler("...")` — catches exceptions, converts to `ToolError` with generic message (SEC-04: no stack traces leak to clients)
- `await _get_erp_token()` — extracts Google token from request context, enforces domain check (SEC-02), exchanges for ERP token
- `_check_erp_result(...)` — converts `{"status": "error", "message": "..."}` dicts from ERPClient into `ToolError`
- `_get_erp()` — returns the ERPClient singleton (raises `RuntimeError` if server hasn't started)

**Tool parameters:**
- No `email` parameter — identity comes from the OAuth token (SEC-01)
- Use type hints for all parameters — FastMCP uses them to generate the tool schema
- Add an `Args:` section to the docstring for each parameter

### Step 3: Add server tests (`tests/test_server.py`)

Create a test class following the existing pattern:

```python
class TestGetUserProfile:
    async def test_calls_erp_client(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_user_profile

        with _patch_token(valid_token), _patch_erp(mock_erp):
            result = await get_user_profile()

        assert result["status"] == "success"
        mock_erp.get_user_profile.assert_awaited_once_with("erp-token-abc")

    async def test_unexpected_error_raises_tool_error(
        self, mock_erp: AsyncMock, valid_token: AccessToken
    ) -> None:
        from server import get_user_profile

        mock_erp.get_user_profile.side_effect = RuntimeError("DB down")
        with _patch_token(valid_token), _patch_erp(mock_erp):
            with pytest.raises(ToolError, match="Failed to fetch user profile"):
                await get_user_profile()
```

**Important:** Update the `mock_erp` fixture to include a return value for your new method:

```python
client.get_user_profile.return_value = {"status": "success", "data": {"name": "Test"}}
```

If you forget this, the `AsyncMock` silently returns another mock instead of a dict, and your `_check_erp_result` call won't catch it.

### Step 4: Add client tests (`tests/test_erp_client.py`)

Use `respx` to mock the HTTP endpoint:

```python
@respx.mock
async def test_get_user_profile(client: ERPClient) -> None:
    respx.get(f"{BASE_URL}/project-logs/person/profile/").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "Test User"})
    )
    result = await client.get_user_profile(token="tok")
    assert result["status"] == "success"
    assert result["data"]["name"] == "Test User"
```

## Adding a Write Tool

Write tools modify data. They require **additional steps** beyond read tools.

### Extra requirements for write tools

**1. Audit logging** — Emit a structured log line after every successful write:

```python
logger.info(
    "WRITE_OP tool=your_tool_name user=%s param1=%s param2=%s",
    email, param1, param2,
)
```

Use `token, email = await _get_erp_token()` (not `_email`) to capture the email for the log.

**2. Input validation** — Validate user inputs before calling ERPClient:

```python
# Description length
if len(description) > _MAX_DESCRIPTION_LEN:
    raise ToolError(f"Description too long (max {_MAX_DESCRIPTION_LEN} characters)")

# Hours bounds
if hours <= 0 or hours > 24:
    raise ValueError(f"hours must be between 0 (exclusive) and 24 (inclusive), got {hours}")

# Date range cap (SEC-05)
if day_count > _MAX_FILL_DAYS:
    raise ValueError(f"Date range spans {day_count} days, exceeding the maximum of {_MAX_FILL_DAYS} days.")
```

**3. Project/label resolution** — If the tool accepts a project or label, offer both ID and name:

```python
async def your_write_tool(
    # ... other params ...
    project_id: int | None = None,
    project_name: str | None = None,
    label_id: int | None = None,
    label_name: str | None = None,
) -> dict[str, Any]:
    # ...
    client = _get_erp()
    resolved_project_id = await client.resolve_project_id(token, project_id, project_name)
    resolved_label_id = await client.resolve_label_id(token, label_id, label_name)
```

**4. Audit log test** — Add your tool to `TestAuditLogCoverage.WRITE_TOOLS`:

```python
WRITE_TOOLS = [
    # ... existing entries ...
    ("your_tool_name", {"param1": "value1", "param2": "value2"}),
]
```

The guard test `test_all_write_tools_covered` will fail if you add a `WRITE_OP` log line but forget to add the tool to this list.

### Complete write tool example

```python
@mcp.tool
@_tool_error_handler("Failed to archive project log. Please try again.")
async def archive_project_log(
    date: str,
    project_id: int | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Archive a project log entry for a specific date.

    Args:
        date: Date in YYYY-MM-DD format.
        project_id: Project/subteam ID. Use this or project_name.
        project_name: Project/team name. Resolved automatically.
    """
    token, email = await _get_erp_token()
    client = _get_erp()
    resolved_project_id = await client.resolve_project_id(token, project_id, project_name)
    result = _check_erp_result(
        await client.archive_project_log(token, date_str=date, project_id=resolved_project_id)
    )
    logger.info(
        "WRITE_OP tool=archive_project_log user=%s date=%s project_id=%s",
        email, date, resolved_project_id,
    )
    return result
```

## Security Compliance Checklist

Every tool must comply with these controls. The test suite enforces them automatically.

| Control | Rule | Enforced by |
|---------|------|-------------|
| SEC-01 | No `email` parameter on any tool | `TestNoEmailParameter` (AST + runtime inspection) |
| SEC-02 | Domain restriction via Google token | `_get_erp_token()` checks `hd` claim + email suffix |
| SEC-03 | Hashed cache keys | `TTLCache` uses SHA-256 internally |
| SEC-04 | No stack traces in responses | `@_tool_error_handler` catches all exceptions; `TestNoStackTraces` verifies |
| SEC-05 | Date range caps on bulk operations | Constants `_MAX_FILL_DAYS` (31) and `_MAX_QUERY_DAYS` (366) |
| SEC-06 | No HTTP redirects | `follow_redirects=False` in ERPClient |
| SEC-07 | HTTPS enforcement | ERPClient rejects non-HTTPS for non-localhost targets |

**What this means for you:**
- Never add an `email` parameter to a tool function — the test suite will catch it
- Never include exception details in `ToolError` messages — use the `@_tool_error_handler` decorator
- If your tool accepts a date range, enforce `_MAX_FILL_DAYS` or `_MAX_QUERY_DAYS`

## Updating the Dockerfile

The Dockerfile explicitly lists application files on line 33:

```dockerfile
COPY --chown=root:root --chmod=644 server.py erp_client.py ./
```

If you add a new Python module (e.g. `utils.py`), you **must** update this line. The Docker build will succeed without it, but the container will fail at runtime with `ModuleNotFoundError`.

## Development Workflow

```bash
# Setup
python3 -m venv ~/.virtualenvs/erp-mcp
source ~/.virtualenvs/erp-mcp/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check .
ruff format --check .

# Type check
mypy server.py erp_client.py
```

All three must pass before submitting a PR. CI runs `pytest` automatically on push and PR.

## Checklist Before Submitting

- [ ] ERPClient method added with `token: str` as first parameter
- [ ] MCP tool uses `@mcp.tool` + `@_tool_error_handler` decorators
- [ ] Tool docstring describes what it does (this is the AI-facing description)
- [ ] `_get_erp_token()` used for auth (not manual token handling)
- [ ] `_check_erp_result()` wraps the ERPClient call
- [ ] `mock_erp` fixture updated with return value for new method
- [ ] Server tests: happy path + error path (RuntimeError -> generic ToolError)
- [ ] Client tests: respx-mocked HTTP test
- [ ] **Write tools only:** `WRITE_OP` audit log emitted after success
- [ ] **Write tools only:** Entry added to `TestAuditLogCoverage.WRITE_TOOLS`
- [ ] **Write tools only:** Input validation (description length, hours bounds, date caps)
- [ ] No `email` parameter on the tool function (SEC-01)
- [ ] `ruff check .` and `pytest tests/ -v` pass
- [ ] Dockerfile COPY line updated if new modules added
- [ ] README.md tools table updated
