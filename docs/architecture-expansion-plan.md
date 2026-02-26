# ERP MCP Server: Architecture Expansion Plan

**Status:** PROPOSED
**Author:** ARCHITECT (Principal Enterprise Architecture)
**Date:** 2026-02-25
**Scope:** Scale erp-mcp from 7 time-logging tools to 50+ tools across multiple ERP domains

---

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [Architecture Pattern Evaluation](#2-architecture-pattern-evaluation)
3. [Recommended Architecture](#3-recommended-architecture)
4. [Module & File Structure](#4-module--file-structure)
5. [Tool Naming & Discovery Conventions](#5-tool-naming--discovery-conventions)
6. [Client Layer Architecture](#6-client-layer-architecture)
7. [Auth & Security Evolution](#7-auth--security-evolution)
8. [Testing Strategy](#8-testing-strategy)
9. [Error Handling & Observability](#9-error-handling--observability)
10. [Rate Limiting & Caching](#10-rate-limiting--caching)
11. [Deployment Strategy](#11-deployment-strategy)
12. [Phased Rollout Plan](#12-phased-rollout-plan)
13. [Risk Assessment](#13-risk-assessment)
14. [Architecture Decision Records](#14-architecture-decision-records)

---

## 1. Current State Analysis

### 1.1 Current Architecture

The erp-mcp server is a single-file MCP server (`server.py`, ~600 lines) backed by a single HTTP client module (`erp_client.py`, ~1000 lines). It exposes **11 tools** (6 read, 4 write, 1 diagnostic) exclusively for the `project_logs` domain.

```
server.py           # All tool definitions, auth, middleware, health check
erp_client.py       # All ERP HTTP calls, token exchange, caching, date parsing
tests/
  conftest.py       # Env var setup
  test_server.py    # Tool tests with mocked ERPClient
  test_erp_client.py # HTTP client tests with respx
```

**Current tool inventory:**

| Tool | Type | Domain |
|------|------|--------|
| `get_active_projects` | Read | project_logs |
| `get_log_labels` | Read | project_logs |
| `get_week_logs` | Read | project_logs |
| `get_day_logs` | Read | project_logs |
| `get_logs_for_date_range` | Read | project_logs |
| `get_month_logs` | Read | project_logs |
| `create_or_update_log` | Write | project_logs |
| `delete_log` | Write | project_logs |
| `complete_week_log` | Write | project_logs |
| `fill_logs_for_days` | Write | project_logs |
| `check_person_week_project_exists` | Diagnostic | project_logs |

### 1.2 Current Strengths

1. **Security controls are well-enforced.** Seven explicit controls (SEC-01 through SEC-07) with automated test coverage via AST-based tool discovery. New tools are automatically covered by security guard tests.

2. **Auth is clean and centralized.** `_get_erp_token()` is the single chokepoint for auth, domain restriction, and token exchange. Every tool calls it identically.

3. **Error handling is consistent.** The `@_tool_error_handler` + `_check_erp_result` pattern means every tool has the same error contract. SEC-04 (no stack traces) is enforced by design.

4. **CONTRIBUTING.md is excellent.** The 4-file checklist pattern (ERPClient method, MCP tool, server tests, client tests) is well-documented and low-friction.

5. **Test infrastructure is solid.** AST-based auto-discovery means security tests scale automatically. The `mock_erp` / `_patch_token` / `_patch_erp` pattern is clean.

### 1.3 Current Pain Points (at Scale)

1. **`server.py` is a monolith.** At 50+ tools (~30 lines each average), it would reach 1500-2000 lines. Cognitive load, merge conflicts, and code review difficulty will increase linearly.

2. **`erp_client.py` is already complex.** At ~1000 lines for one domain, adding 10+ domains would push it to 5000-10000 lines. Much of the complexity is project-log-specific (week log resolution, date parsing, dual write paths).

3. **The `mock_erp` fixture is fragile at scale.** Every new ERPClient method requires an explicit `return_value` in the fixture. Missing it causes silent mock-returning-mock bugs. With 50+ methods, this becomes a persistent failure mode.

4. **No domain boundaries.** All tools share one namespace. An AI client listing tools sees a flat list of 50+ tools with no grouping, making selection harder.

5. **Single module-level `erp` singleton.** Works for one domain but makes it harder to have domain-specific configuration (e.g., different timeout budgets for leaves vs. invoices).

### 1.4 ERP API Landscape

Analysis of the ERP Django apps (`workstream-app/apps/`) reveals **25+ Django apps** with API endpoints. Each app follows the pattern: `apps/[name]/api/v1/urls.py` with DRF viewsets and views. Final URL pattern: `/api/v1/[app]/[resource]/`.

**High-value domains for MCP exposure (ranked by user frequency and API readiness):**

| Domain | Django App | Endpoint Count | Read APIs | Write APIs | MCP Value |
|--------|-----------|----------------|-----------|------------|-----------|
| Project Logs | `project_logs` | ~30 | High | High | **Already done** |
| Leaves | `leaves` | ~20 | High (balances, records, holidays) | High (apply, approve) | **Very High** |
| Profile/Core | `core` | ~50 | High (profile, teams, people) | Medium (profile update) | **Very High** |
| Expenses | `expenses` | ~15 | High (summary, limits) | Medium (submit, review) | **High** |
| Teams | `teams` | ~30 | High (structure, members) | Low | **High** |
| Training | `training` | ~25 | High (history, upcoming) | Medium (register) | **Medium** |
| Rewards | `rewards` | ~25 | Medium (reviews, bonuses) | Low | **Medium** |
| Competencies | `competencies` | ~10 | Medium (structure, self-review) | Low | **Medium** |
| Helpdesk | `helpdesk` | ~15 | Medium (ticket list) | Medium (create, update) | **Medium** |
| Payroll | `payroll` | ~20 | Read-only for most users | Admin-only writes | **Low** (sensitive) |
| Invoices | `invoices` | ~25 | Admin/lead reports | Admin-only writes | **Low** (sensitive) |
| Forms | `forms` | ~20 | Medium (review forms) | Medium (submit) | **Low** |
| Kanban Board | `kanban_board` | ~5 | Medium | Medium | **Low** |
| Advisory | `advisory` | ~15 | Low | Low | **Low** |
| Stocks | `stocks` | ~5 | Low | Low | **Low** |

---

## 2. Architecture Pattern Evaluation

### 2.1 Option A: Monolithic Server with Domain Modules (Recommended)

Scale the current single-server pattern by splitting tool definitions into domain-specific Python modules that register themselves with the shared `mcp` FastMCP instance.

```
server.py                    # FastMCP instance, auth, middleware, health
tools/
  __init__.py                # Auto-imports all domain modules
  _registry.py               # Shared helpers: _get_erp_token, _tool_error_handler, _check_erp_result
  project_logs.py            # @mcp.tool functions for project logs
  leaves.py                  # @mcp.tool functions for leaves
  profile.py                 # @mcp.tool functions for profile/core
  expenses.py                # ...
clients/
  __init__.py
  _base.py                   # BaseERPClient: shared request(), auth, caching
  project_logs.py            # ProjectLogsClient
  leaves.py                  # LeavesClient
  profile.py                 # ProfileClient
```

**Pros:**
- Simplest incremental migration from current state. Existing tools can be moved file-by-file.
- Single deployment artifact, single auth flow, single health check.
- Domain modules share the same `mcp` instance, so tool discovery is automatic.
- No inter-process communication, no service mesh, no container orchestration complexity.
- Current CONTRIBUTING.md pattern extends naturally: "touch 4 files" becomes "touch 4 files in the right domain directory."

**Cons:**
- All domains must share the same Python process, same memory, same deployment cycle.
- A bug in one domain module can crash the entire server (mitigated by `@_tool_error_handler` catching all exceptions).
- No independent scaling per domain (irrelevant at current scale: single user per request, stateless HTTP).

**Complexity:** Low
**Auth overhead:** None (shared `_get_erp_token`)
**Discoverability:** Good (FastMCP lists all tools; names have domain prefix for grouping)
**Testing:** Good (same pattern, domain-scoped test files)

### 2.2 Option B: Plugin/Registry Architecture

Each domain is a self-contained plugin that declares its tools, client methods, and metadata. A registry discovers and loads plugins at startup.

```python
# tools/leaves.py
class LeavesPlugin:
    name = "leaves"
    version = "1.0"

    @staticmethod
    def register(mcp: FastMCP, client_factory):
        @mcp.tool
        async def leaves_get_balance(...): ...
```

**Pros:**
- Enables third-party plugins (if other teams want to add tools).
- Formal contract between core and plugins.
- Could support dynamic loading/disabling via config.

**Cons:**
- Over-engineered for the current team size (1-2 developers).
- Plugin interface adds an abstraction layer that makes the CONTRIBUTING.md pattern harder to explain.
- FastMCP already provides the registry (`@mcp.tool`). Building another registry on top adds no value.
- Dynamic loading introduces startup-order dependencies and harder debugging.

**Complexity:** Medium
**Auth overhead:** None (same shared auth)
**Discoverability:** Same as Option A
**Testing:** Slightly harder (need to test plugin loading)

### 2.3 Option C: Multiple MCP Servers (Microservice)

Deploy separate MCP servers per domain: `erp-mcp-logs`, `erp-mcp-leaves`, `erp-mcp-expenses`, etc.

**Pros:**
- True independent deployment and scaling per domain.
- Fault isolation: one domain crashing does not affect others.
- Teams can own their domain server independently.

**Cons:**
- **Critical problem: MCP client configuration.** Each AI client (Claude Desktop, Cursor, etc.) must be configured with a separate MCP server URL per domain. Users must manage N server connections instead of one. This is a severe UX penalty.
- Auth duplication: each server needs its own Google OAuth setup, or a shared auth gateway must be introduced.
- Operational overhead: N containers, N health checks, N CI pipelines, N ECR repos, N NGINX locations.
- The ERP backend is a monolith itself -- micro-MCP-servers add distributed systems complexity on the consumer side with no corresponding benefit on the producer side.
- Current scale (one organization, ~500 users) does not justify multi-service complexity.

**Complexity:** High
**Auth overhead:** High (N OAuth flows or shared gateway)
**Discoverability:** Poor (tools split across multiple server connections)
**Testing:** High (integration tests across services)

### 2.4 Option D: Hybrid -- Single Server with Feature Flags

Single server (Option A) with environment-variable feature flags to enable/disable domain modules.

```python
# tools/__init__.py
ENABLED_DOMAINS = os.environ.get("ENABLED_DOMAINS", "project_logs,leaves,profile").split(",")

if "leaves" in ENABLED_DOMAINS:
    from tools.leaves import *  # registers tools with mcp
```

**Pros:**
- Incremental rollout: enable domains one at a time in production.
- Same deployment artifact, but tool surface is configurable per environment.
- Dev environments can enable all domains; production enables only stable ones.

**Cons:**
- Slightly more complex than pure Option A.
- Feature flags can become stale if never removed.

**Complexity:** Low-Medium
**Auth overhead:** None
**Discoverability:** Good (only enabled tools are visible)
**Testing:** Good (test all domains in CI, subset in prod)

### 2.5 Decision

**Recommended: Option D (Hybrid -- Single Server with Feature Flags)**

This is Option A with the addition of domain-level feature flags for controlled rollout. It provides the simplicity of a monolithic server, the modularity of domain files, and the safety of incremental enablement.

**Rationale:**
- Current team size (1-2 developers) and scale (single org, ~500 users) do not justify microservice overhead.
- FastMCP is the tool registry. Building another abstraction on top adds no value.
- Feature flags cost almost nothing to implement and provide a critical safety valve for production rollout.
- The architecture is reversible: if a domain grows too large or too different, it can be extracted to its own server later with minimal friction (the domain module already encapsulates its tools and client).

---

## 3. Recommended Architecture

### 3.1 High-Level Architecture

```
                            +--------------------------+
                            |    AI Clients            |
                            | (Claude, Cursor, etc.)   |
                            +-------------|------------+
                                          |  MCP (streamable-http)
                                          v
                            +---------------------------+
                            |     NGINX Reverse Proxy   |
                            |   /mcp -> localhost:8100  |
                            +-------------|-------------+
                                          |
                            +-------------|-------------+
                            |   FastMCP v3 Server       |
                            |                           |
                            |  server.py (entry point)  |
                            |    - GoogleProvider auth  |
                            |    - SecurityHeaders MW   |
                            |    - /health endpoint     |
                            |    - Feature flag loader  |
                            |                           |
                            |  tools/                   |
                            |    _registry.py (shared)  |
                            |    project_logs.py        |
                            |    leaves.py              |
                            |    profile.py             |
                            |    expenses.py            |
                            |    teams.py               |
                            |    training.py            |
                            |    helpdesk.py            |
                            |                           |
                            |  clients/                 |
                            |    _base.py (shared HTTP) |
                            |    project_logs.py        |
                            |    leaves.py              |
                            |    profile.py             |
                            |    expenses.py            |
                            |    teams.py               |
                            |    training.py            |
                            |    helpdesk.py            |
                            +-------------|-------------+
                                          |
                                          |  HTTPS (Token auth)
                                          v
                            +---------------------------+
                            |   ERP Django Backend      |
                            |  /api/v1/[app]/[resource] |
                            +---------------------------+
```

### 3.2 Module Interaction Diagram

```
server.py
  |
  |-- Creates FastMCP instance (`mcp`)
  |-- Configures GoogleProvider auth
  |-- Runs _lifespan: initializes clients
  |-- Imports tools/__init__.py
  |       |
  |       |-- Reads ENABLED_DOMAINS env var
  |       |-- For each enabled domain:
  |       |     imports tools/{domain}.py
  |       |     which calls @mcp.tool to register functions
  |       |
  |       |-- tools/_registry.py provides:
  |       |     get_erp_token()
  |       |     tool_error_handler()
  |       |     check_erp_result()
  |       |     get_client(domain) -> domain-specific client
  |
  |-- clients/__init__.py
        |-- _base.py: BaseERPClient (shared httpx, auth, request)
        |-- Each domain client inherits BaseERPClient
        |-- ClientManager: lazy-init, holds all domain clients
```

---

## 4. Module & File Structure

### 4.1 Proposed Directory Layout

```
erp-mcp/
  server.py                         # Entry point: FastMCP, auth, middleware, lifespan

  clients/                          # ERP HTTP client layer
    __init__.py                     # Exports ClientManager
    _base.py                        # BaseERPClient: httpx, request(), token exchange, caching
    project_logs.py                 # ProjectLogsClient(BaseERPClient)
    leaves.py                       # LeavesClient(BaseERPClient)
    profile.py                      # ProfileClient(BaseERPClient)
    expenses.py                     # ExpensesClient(BaseERPClient)
    teams.py                        # TeamsClient(BaseERPClient)
    training.py                     # TrainingClient(BaseERPClient)
    helpdesk.py                     # HelpdeskClient(BaseERPClient)

  tools/                            # MCP tool definitions
    __init__.py                     # Domain loader with feature flags
    _registry.py                    # Shared: get_erp_token, tool_error_handler, check_erp_result
    project_logs.py                 # @mcp.tool functions for project logs
    leaves.py                       # @mcp.tool functions for leaves
    profile.py                      # @mcp.tool functions for profile/core
    expenses.py                     # @mcp.tool functions for expenses
    teams.py                        # @mcp.tool functions for teams
    training.py                     # @mcp.tool functions for training
    helpdesk.py                     # @mcp.tool functions for helpdesk

  tests/
    conftest.py                     # Shared env vars, fixtures
    clients/
      __init__.py
      conftest.py                   # Shared client fixtures (respx)
      test_base.py                  # BaseERPClient tests (token exchange, caching, security)
      test_project_logs.py          # ProjectLogsClient tests
      test_leaves.py                # LeavesClient tests
      ...
    tools/
      __init__.py
      conftest.py                   # Shared tool fixtures (mock clients, tokens)
      test_project_logs.py          # Project log tool tests
      test_leaves.py                # Leave tool tests
      test_security.py              # Cross-cutting SEC-01 through SEC-07 tests (AST-based)
      test_audit.py                 # WRITE_OP audit log coverage
      ...

  docs/
    adr/
      0001-domain-module-architecture.md
      0002-single-container-deployment.md
      0003-feature-flag-rollout.md
    architecture-expansion-plan.md  # This document
```

### 4.2 Migration Strategy from Current State

The migration is file-level refactoring with no behavioral changes. Each step is independently deployable:

**Step 1: Extract shared utilities (no behavior change)**
- Move `_get_erp_token`, `_tool_error_handler`, `_check_erp_result` from `server.py` to `tools/_registry.py`
- `server.py` imports from `tools/_registry.py`
- All existing tools still in `server.py`

**Step 2: Extract BaseERPClient (no behavior change)**
- Move `TTLCache`, `ERPClient.__init__`, `request`, `exchange_google_token`, `close` to `clients/_base.py`
- Move project-log-specific methods to `clients/project_logs.py`
- `erp_client.py` becomes a thin re-export (backward compat) then gets removed

**Step 3: Extract project_logs tools (no behavior change)**
- Move existing tool functions from `server.py` to `tools/project_logs.py`
- `server.py` becomes just the entry point

**Step 4: Add feature flag loader**
- `tools/__init__.py` reads `ENABLED_DOMAINS` and conditionally imports
- Default: `project_logs` (backward compatible)

**Step 5: Add new domains one by one**
- Each new domain is a new `tools/{domain}.py` + `clients/{domain}.py` + test files
- Enable via `ENABLED_DOMAINS` env var

### 4.3 Key Design Constraint: Dockerfile COPY

The current Dockerfile explicitly lists files:
```dockerfile
COPY --chown=root:root --chmod=644 server.py erp_client.py ./
```

With the package structure, this changes to:
```dockerfile
COPY --chown=root:root --chmod=644 server.py ./
COPY --chown=root:root --chmod=644 clients/ ./clients/
COPY --chown=root:root --chmod=644 tools/ ./tools/
```

This is more maintainable -- new domain files within `clients/` or `tools/` are automatically included without Dockerfile changes.

---

## 5. Tool Naming & Discovery Conventions

### 5.1 Naming Convention

With 50+ tools in a flat namespace, a consistent naming scheme is essential for AI client discoverability.

**Pattern: `{domain}_{verb}_{resource}`**

```
# Project Logs (existing, grandfathered names)
get_active_projects
get_log_labels
get_week_logs
get_day_logs
get_logs_for_date_range
get_month_logs
create_or_update_log
delete_log
complete_week_log
fill_logs_for_days
check_person_week_project_exists

# Leaves (new)
leaves_get_balance           # My current leave balance by type
leaves_get_records           # My leave history (with filters)
leaves_get_holidays          # Upcoming holidays
leaves_apply                 # Apply for leave
leaves_check_sandwich        # Check if leave creates a sandwich
leaves_get_approvals_pending # Leaves pending my approval

# Profile (new)
profile_get_my_info          # My profile details
profile_get_person           # Look up another person's basic info
profile_get_announcements    # Current announcements
profile_get_reminders        # My pending reminders/todos

# Expenses (new)
expenses_get_summary         # My expense summary (limits + used)
expenses_get_records         # My expense records
expenses_submit              # Submit a new expense

# Teams (new)
teams_get_my_teams           # Teams I belong to
teams_get_members            # Members of a specific team
teams_get_structure          # Team hierarchy/tree

# Training (new)
training_get_ongoing         # My ongoing trainings
training_get_history         # Completed trainings
training_get_upcoming        # Upcoming published trainings
training_register            # Register for a training

# Helpdesk (new)
helpdesk_list_tickets        # My helpdesk tickets
helpdesk_create_ticket       # Raise a new ticket
helpdesk_get_categories      # Available ticket categories
```

### 5.2 Naming Rules

1. **Existing tools keep their names.** Renaming would break AI client caches and user workflows. The 11 existing project_logs tools are grandfathered.

2. **New domains use `{domain}_` prefix.** This groups tools visually in tool listings and reduces ambiguity.

3. **Verbs are standardized:**
   - `get` = fetch/read single item or computed result
   - `list` = fetch/read a collection
   - `apply` / `submit` / `create` = create a new record
   - `update` = modify an existing record
   - `delete` / `cancel` = remove or cancel

4. **No `email` in any tool parameter name.** SEC-01 applies to all domains.

5. **Tool docstrings are the AI-facing description.** The first line must be a clear, imperative sentence. Use `Args:` sections for parameters. This is what Claude/Cursor reads to decide which tool to call.

### 5.3 Tool Count Targets per Phase

| Phase | Domains | Tool Count | Cumulative |
|-------|---------|------------|------------|
| Current | project_logs | 11 | 11 |
| Phase 1 | + leaves, profile | +10-12 | ~22 |
| Phase 2 | + expenses, teams | +8-10 | ~32 |
| Phase 3 | + training, helpdesk | +8-10 | ~42 |
| Phase 4 | + rewards, competencies | +6-8 | ~50 |

---

## 6. Client Layer Architecture

### 6.1 BaseERPClient

Extract the domain-agnostic parts of the current `ERPClient` into a base class:

```python
# clients/_base.py

class BaseERPClient:
    """Shared HTTP client infrastructure for all ERP domains.

    Provides:
    - httpx.AsyncClient with security defaults (SEC-06, SEC-07)
    - request() method with error handling (SEC-04)
    - Token exchange and caching (SEC-02, SEC-03)
    - Lifecycle management (close/context manager)
    """

    def __init__(self, base_url: str, allowed_domain: str = "arbisoft.com"):
        # SEC-07: HTTPS enforcement
        # SEC-06: follow_redirects=False
        # Token cache (TTLCache)
        ...

    async def exchange_google_token(self, google_token: str) -> tuple[str, str]:
        """SEC-02 + SEC-03: domain-restricted token exchange with hashed cache."""
        ...

    async def request(self, method, endpoint, token, *, data=None, params=None) -> dict:
        """SEC-04: authenticated request with error-dict return contract."""
        ...

    async def close(self): ...
```

### 6.2 Domain Clients

Each domain client inherits `BaseERPClient` and adds domain-specific methods:

```python
# clients/leaves.py

class LeavesClient(BaseERPClient):
    """ERP API client for leave management."""

    async def get_leave_balance(self, token: str, fiscal_year_id: int) -> dict[str, Any]:
        return await self.request(
            "GET", "leaves/individual_fiscal_summary/{}/".format(fiscal_year_id), token
        )

    async def get_leave_records(self, token: str, **filters) -> dict[str, Any]:
        return await self.request("GET", "leaves/list/", token, params=filters)

    async def apply_leave(self, token: str, data: dict) -> dict[str, Any]:
        return await self.request("POST", "leaves/request/apply/", token, data=data)
```

### 6.3 ClientManager

A manager class holds all domain clients and shares a single `httpx.AsyncClient`:

```python
# clients/__init__.py

class ClientManager:
    """Manages all domain-specific ERP clients.

    All clients share the same httpx.AsyncClient and token cache,
    so connection pooling and token exchange are efficient.
    """

    def __init__(self, base_url: str, allowed_domain: str):
        self._base_url = base_url
        self._allowed_domain = allowed_domain
        self._http: httpx.AsyncClient | None = None
        self._token_cache: TTLCache | None = None
        self._clients: dict[str, BaseERPClient] = {}

    async def start(self):
        """Initialize shared httpx client and token cache."""
        self._http = httpx.AsyncClient(follow_redirects=False, ...)
        self._token_cache = TTLCache(maxsize=500)

    def get_client(self, domain: str) -> BaseERPClient:
        """Return domain client, lazily constructed."""
        if domain not in self._clients:
            cls = _DOMAIN_CLIENTS[domain]
            self._clients[domain] = cls(
                base_url=self._base_url,
                allowed_domain=self._allowed_domain,
                http=self._http,           # shared
                token_cache=self._token_cache,  # shared
            )
        return self._clients[domain]

    async def close(self):
        if self._http:
            await self._http.aclose()
```

### 6.4 Shared vs. Separate httpx Clients

**Decision: Share a single `httpx.AsyncClient` across all domain clients.**

Rationale:
- All domain clients call the same ERP backend (`ERP_API_BASE_URL`).
- Sharing the client means shared connection pooling (fewer TCP connections, less TLS handshake overhead).
- The token cache should also be shared (same Google token -> same ERP token regardless of domain).
- If a domain needs different timeouts, it can override per-request using `httpx.Timeout` on individual calls.

---

## 7. Auth & Security Evolution

### 7.1 Security Controls Mapping

All seven existing security controls carry forward unchanged:

| ID | Control | Current | Expansion Impact |
|----|---------|---------|------------------|
| SEC-01 | No email parameter | Per-tool enforcement | Same. AST scanner runs across all `tools/*.py` files. |
| SEC-02 | Domain restriction | `_get_erp_token()` | Same. Single chokepoint in `tools/_registry.py`. |
| SEC-03 | Hashed cache keys | `TTLCache` SHA-256 | Same. Shared cache in `ClientManager`. |
| SEC-04 | No stack traces | `@_tool_error_handler` | Same. Shared decorator in `tools/_registry.py`. |
| SEC-05 | Write guardrails | Date caps, audit log | Extended per domain. Each domain defines its own caps. |
| SEC-06 | No redirects | `httpx.AsyncClient` | Same. Shared httpx client in `ClientManager`. |
| SEC-07 | HTTPS enforcement | Constructor check | Same. `BaseERPClient.__init__` enforces it. |

### 7.2 Read-Heavy vs. Write-Heavy Domain Security

Different domains have different risk profiles:

**Read-only domains (profile, teams, training history):**
- SEC-05 write guardrails do not apply.
- Lower audit logging requirements (can log at DEBUG level).
- No date-range caps needed (but query limits for API performance are still wise).

**Write domains (leaves, expenses, helpdesk):**
- Full SEC-05 compliance: audit log every write with `WRITE_OP tool=... user=... ...`.
- Domain-specific input validation and caps.
- Consider adding a confirmation pattern for destructive writes (e.g., leave cancellation).

**Sensitive read domains (payroll, invoices):**
- These expose compensation and financial data.
- Consider adding an additional permission check: the ERP backend already enforces role-based access, but the MCP layer should document that these tools may return 403 for non-authorized users.
- SEC-08 candidate: **Sensitive data classification** -- tools that return compensation/financial data should have their docstrings note this, and could optionally be behind a stricter feature flag.

### 7.3 New Security Control Candidates

| ID | Control | Purpose |
|----|---------|---------|
| SEC-08 | Sensitive domain flag | Tools in sensitive domains (`payroll`, `invoices`) are behind an explicit opt-in feature flag (`ENABLE_SENSITIVE_DOMAINS=true`). Prevents accidental exposure. |
| SEC-09 | Write rate limiting | Per-user, per-domain write rate limits (e.g., max 10 leave applications per hour). Prevents bulk operations from overwhelming the ERP backend. Implemented as a TTLCache-based counter in the MCP layer. |

### 7.4 ERP Backend as the Authority

The MCP layer intentionally does NOT replicate the ERP's role-based access control (RBAC). The ERP backend enforces who can see what. If a user calls `leaves_get_approvals_pending` but is not a leave approver, the ERP returns 403, which `request()` converts to an error dict, which `_check_erp_result()` converts to a `ToolError`.

This is by design: the MCP layer handles authentication (who are you?) and the ERP backend handles authorization (what can you do?). Duplicating RBAC in the MCP layer would create a consistency risk.

---

## 8. Testing Strategy

### 8.1 Current Test Infrastructure (Preserved)

The existing test infrastructure scales well:

1. **AST-based auto-discovery** (`_discover_tool_names`, `_discover_write_tool_names`) -- needs no change; it parses `server.py` and all imported `tools/*.py` files. As long as tools use `@mcp.tool`, they are discovered.

2. **Security guard tests** (`TestNoEmailParameter`, `TestNoStackTraces`, `TestAuditLogCoverage`) -- these run against the discovered tool set. New tools are automatically covered.

3. **Mock pattern** (`_patch_token`, `_patch_erp`) -- evolves to `_patch_token` + `_patch_clients(domain, mock)`.

### 8.2 Scaled Test Organization

```
tests/
  conftest.py                     # Env vars, shared fixtures

  clients/                         # ERPClient unit tests (respx-mocked HTTP)
    conftest.py                   # Shared: client fixtures, respx setup
    test_base.py                  # BaseERPClient: token exchange, caching, SEC-02/03/06/07
    test_project_logs.py          # ProjectLogsClient: each method has HTTP mock
    test_leaves.py                # LeavesClient
    test_profile.py               # ProfileClient
    ...

  tools/                           # MCP tool unit tests (mocked clients)
    conftest.py                   # Shared: mock clients, token fixtures
    test_project_logs.py          # Project log tool happy/error paths
    test_leaves.py                # Leave tool happy/error paths
    test_profile.py               # Profile tool happy/error paths
    ...
    test_security.py              # Cross-cutting: SEC-01 through SEC-09
    test_audit.py                 # WRITE_OP coverage guard
```

### 8.3 Fixture Evolution

The current `mock_erp` fixture becomes domain-specific:

```python
# tests/tools/conftest.py

@pytest.fixture
def mock_clients() -> dict[str, AsyncMock]:
    """Return mocked clients for all domains."""
    clients = {}

    # Project logs
    pl = AsyncMock()
    pl.get_active_projects.return_value = {"status": "success", "data": []}
    # ... all project_logs methods
    clients["project_logs"] = pl

    # Leaves
    lv = AsyncMock()
    lv.get_leave_balance.return_value = {"status": "success", "data": {...}}
    clients["leaves"] = lv

    return clients

@pytest.fixture
def mock_project_logs_client(mock_clients):
    """Shorthand for project_logs mock."""
    return mock_clients["project_logs"]
```

### 8.4 AST Scanner Evolution

The AST-based tool discovery currently scans only `server.py`. It needs to scan `tools/*.py`:

```python
def _discover_tool_names() -> list[str]:
    """Parse all tools/*.py files and return @mcp.tool function names."""
    names = []
    tools_dir = Path(__file__).parent.parent / "tools"
    for py_file in tools_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                if any(_is_mcp_tool_decorator(dec) for dec in node.decorator_list):
                    names.append(node.name)
    return names
```

### 8.5 Additional Test Categories

1. **Domain integration tests** (optional, future): Test tool -> client -> respx-mocked HTTP in one flow, verifying that the tool's input validation + client's HTTP call + response parsing work end-to-end without a real ERP.

2. **Feature flag tests**: Verify that disabled domains do not register tools. Verify that `ENABLED_DOMAINS=project_logs` results in exactly the current 11 tools.

3. **Contract tests**: For write tools, maintain a snapshot of the expected ERP request payload. If the payload shape changes, the test breaks, alerting developers to potential breaking changes in the ERP API contract.

---

## 9. Error Handling & Observability

### 9.1 Structured Error Hierarchy

Currently, all errors flow through `_check_erp_result` (dict -> ToolError) and `@_tool_error_handler` (exception -> ToolError). This pattern scales well but needs structured error metadata:

```python
# tools/_registry.py

class ERPToolError(ToolError):
    """Extended ToolError with structured metadata for observability."""

    def __init__(self, message: str, *, domain: str, tool: str, error_code: str | None = None):
        super().__init__(message)
        self.domain = domain
        self.tool = tool
        self.error_code = error_code
```

This enables structured logging without leaking details to the client (SEC-04 preserved).

### 9.2 Logging Strategy

**Current:** Unstructured log lines with `logger.info("WRITE_OP tool=... user=... ...")`.

**Proposed:** Structured JSON logging for production, human-readable for dev:

```python
# Log format (production)
{
    "timestamp": "2026-02-25T15:30:00Z",
    "level": "INFO",
    "logger": "erp_mcp.tools.leaves",
    "event": "WRITE_OP",
    "tool": "leaves_apply",
    "user": "user@arbisoft.com",
    "domain": "leaves",
    "params": {"leave_type": "casual", "start_date": "2026-03-01"},
    "duration_ms": 342
}
```

Implementation: Use Python's `logging` with a JSON formatter in production. The `structlog` library is a good option but adds a dependency -- evaluate whether the built-in `logging` module with a custom JSON formatter is sufficient.

### 9.3 Metrics (Future Phase)

For 50+ tools, basic metrics become valuable:

| Metric | Type | Purpose |
|--------|------|---------|
| `erp_mcp_tool_calls_total` | Counter | Tool call volume by domain/tool/status |
| `erp_mcp_tool_duration_seconds` | Histogram | Latency distribution by domain/tool |
| `erp_mcp_erp_requests_total` | Counter | Upstream ERP API calls by endpoint/status |
| `erp_mcp_token_cache_hits_total` | Counter | Token cache effectiveness |
| `erp_mcp_auth_failures_total` | Counter | SEC-02 domain violations |

Implementation options:
- **Lightweight:** Python `logging` with structured fields, parsed by CloudWatch Logs Insights or similar.
- **Full metrics:** `prometheus_client` library exposing `/metrics` endpoint, scraped by CloudWatch Agent or Prometheus. This adds a dependency and a new endpoint to secure.

**Recommendation:** Start with structured logging (Phase 1-2). Add Prometheus metrics when tool count exceeds 30 and operational visibility becomes a bottleneck (Phase 3+).

### 9.4 Request Tracing

For debugging tool calls that span multiple ERP API requests (e.g., `create_or_update_log` makes 2-4 HTTP calls):

```python
# tools/_registry.py

import uuid

async def get_erp_token() -> tuple[str, str, str]:
    """Returns (erp_token, email, request_id)."""
    request_id = str(uuid.uuid4())[:8]
    # ... existing logic ...
    return erp_token, email, request_id
```

Pass `request_id` through to `request()` as a log correlation field. No distributed tracing infrastructure needed -- just log correlation within a single request lifecycle.

---

## 10. Rate Limiting & Caching

### 10.1 Per-Domain Caching Strategy

Different domains have different caching characteristics:

| Domain | Cache Candidate | TTL | Rationale |
|--------|----------------|-----|-----------|
| Profile | `profile_get_my_info` | 5 min | Profile changes are rare; frequent lookups by AI clients |
| Teams | `teams_get_my_teams`, `teams_get_structure` | 5 min | Team assignments change weekly at most |
| Leaves | `leaves_get_holidays` | 1 hour | Holiday list changes very rarely |
| Leaves | `leaves_get_balance` | 1 min | Balance changes after each leave application |
| Training | `training_get_upcoming` | 5 min | Training schedule changes rarely intraday |
| Project Logs | None | N/A | Already low-latency; data changes frequently |

**Implementation:** Use the existing `TTLCache` class (already proven, async-safe, bounded LRU) with per-endpoint caches:

```python
# clients/leaves.py

class LeavesClient(BaseERPClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._holiday_cache = TTLCache(maxsize=10, ttl=3600)  # 1 hour

    async def get_holidays(self, token: str) -> dict[str, Any]:
        cache_key = hashlib.sha256(token.encode()).hexdigest()
        cached = await self._holiday_cache.aget(cache_key)
        if cached:
            return cached
        result = await self.request("GET", "leaves/holiday_records/", token)
        if result.get("status") == "success":
            await self._holiday_cache.aput(cache_key, result)
        return result
```

### 10.2 Write Rate Limiting

For write operations, implement a per-user, per-domain rate limiter to prevent runaway AI loops:

```python
# tools/_registry.py

_write_rate_limits: dict[str, TTLCache] = {}

async def check_write_rate_limit(domain: str, email: str, max_per_hour: int = 20):
    """Raise ToolError if user has exceeded write rate limit for this domain."""
    if domain not in _write_rate_limits:
        _write_rate_limits[domain] = TTLCache(maxsize=1000, ttl=3600)

    cache = _write_rate_limits[domain]
    key = hashlib.sha256(f"{email}:{domain}".encode()).hexdigest()
    count = await cache.aget(key) or 0

    if count >= max_per_hour:
        raise ToolError(f"Rate limit exceeded: max {max_per_hour} write operations per hour for {domain}.")

    await cache.aput(key, count + 1)
```

**Domain-specific limits:**

| Domain | Writes/Hour | Rationale |
|--------|-------------|-----------|
| project_logs | 50 | Bulk fill can generate many writes |
| leaves | 10 | Leave applications are infrequent |
| expenses | 20 | Expense submissions are batch-ey |
| helpdesk | 10 | Ticket creation is infrequent |

### 10.3 ERP Backend Rate Limiting

The MCP server should respect the ERP backend's rate limits. Currently, the ERP backend does not have explicit rate limiting on the API layer (it's a Django app behind Gunicorn). However, the MCP server should:

1. **Implement exponential backoff** on 429 (Too Many Requests) responses from the ERP, if they start occurring.
2. **Cap concurrent requests** per user using `asyncio.Semaphore` in the `ClientManager` to prevent a single user's bulk operation from monopolizing the connection pool.

---

## 11. Deployment Strategy

### 11.1 Single Container (Maintained)

**Decision: Stay with single container deployment.**

Rationale:
- All domain modules share the same Python process, same auth flow, same connection pool.
- Docker image size increase is minimal (~100KB per domain module).
- Operational complexity stays constant regardless of domain count.
- Health check stays simple: `/health` on port 8100.
- CI/CD pipeline stays simple: one build, one push, one deploy.

### 11.2 Feature Flags for Domain Enablement

```bash
# Environment variable
ENABLED_DOMAINS=project_logs,leaves,profile,expenses

# Default (backward compatible):
ENABLED_DOMAINS=project_logs
```

The `tools/__init__.py` loader:

```python
# tools/__init__.py
import os
import importlib
import logging

logger = logging.getLogger("erp_mcp.tools")

_DEFAULT_DOMAINS = "project_logs"
_AVAILABLE_DOMAINS = {
    "project_logs",
    "leaves",
    "profile",
    "expenses",
    "teams",
    "training",
    "helpdesk",
    "rewards",
    "competencies",
}

def load_tools(mcp_instance):
    """Import and register tool modules for enabled domains."""
    enabled = os.environ.get("ENABLED_DOMAINS", _DEFAULT_DOMAINS)
    domains = [d.strip() for d in enabled.split(",") if d.strip()]

    unknown = set(domains) - _AVAILABLE_DOMAINS
    if unknown:
        logger.warning("Unknown domains in ENABLED_DOMAINS (ignored): %s", unknown)

    for domain in domains:
        if domain in _AVAILABLE_DOMAINS:
            module = importlib.import_module(f"tools.{domain}")
            logger.info("Loaded tool domain: %s (%d tools)", domain,
                       getattr(module, "TOOL_COUNT", "?"))

    logger.info("Total enabled domains: %d", len(domains))
```

### 11.3 Dockerfile Changes

```dockerfile
# Replace single-file COPY with directory COPY
COPY --chown=root:root --chmod=644 server.py ./
COPY --chown=root:root --chmod=644 clients/ ./clients/
COPY --chown=root:root --chmod=644 tools/ ./tools/
```

### 11.4 CI/CD Considerations

- **Test matrix**: CI runs tests for ALL domains regardless of feature flags. Broken domains are caught before deployment even if they are not yet enabled in production.
- **Image tagging**: No change. The same image contains all domains; feature flags control activation.
- **Rollback**: Disable a domain by removing it from `ENABLED_DOMAINS` and restarting the container. No code change needed.

---

## 12. Phased Rollout Plan

### Phase 0: Refactor (Pre-requisite, ~2-3 days)

**Goal:** Restructure the codebase into the module layout without adding any new tools. Zero behavior change.

| Task | Files Changed | Risk |
|------|---------------|------|
| Extract `tools/_registry.py` from `server.py` | `server.py`, `tools/_registry.py` | Low |
| Extract `clients/_base.py` from `erp_client.py` | `erp_client.py`, `clients/_base.py` | Medium |
| Extract `clients/project_logs.py` | `erp_client.py`, `clients/project_logs.py` | Medium |
| Extract `tools/project_logs.py` | `server.py`, `tools/project_logs.py` | Low |
| Add `tools/__init__.py` with feature flag loader | New file | Low |
| Update Dockerfile | `Dockerfile` | Low |
| Reorganize tests | `tests/` | Low |
| Verify all existing tests pass | CI | Gate |

**Acceptance criteria:** All 11 existing tools work identically. All existing tests pass. `ENABLED_DOMAINS=project_logs` is the default.

### Phase 1: Leaves + Profile (~1-2 weeks)

**Goal:** Add the two highest-value domains for daily employee use.

**Leaves domain (6-8 tools):**

| Tool | Type | ERP Endpoint | Priority |
|------|------|-------------|----------|
| `leaves_get_balance` | Read | `leaves/individual_fiscal_summary/<id>/` | P0 |
| `leaves_get_holidays` | Read | `leaves/holiday_records/` | P0 |
| `leaves_get_records` | Read | `leaves/list/` | P0 |
| `leaves_apply` | Write | `leaves/request/apply/` | P1 |
| `leaves_check_sandwich` | Read | `leaves/request/check-sandwich/` | P1 |
| `leaves_get_approvals_pending` | Read | `leaves/approval/leaves/list/` | P2 |
| `leaves_get_fiscal_years` | Read | `leaves/individual_leave_fiscal_years/` | P2 |

**Profile domain (4-5 tools):**

| Tool | Type | ERP Endpoint | Priority |
|------|------|-------------|----------|
| `profile_get_my_info` | Read | `core/profile/` | P0 |
| `profile_get_announcements` | Read | `core/home/announcements/` | P0 |
| `profile_get_person` | Read | `core/person/detail/` | P1 |
| `profile_get_reminders` | Read | `core/person/reminders/` | P1 |
| `profile_get_notifications` | Read | `core/notification/list/` | P2 |

**Rollout steps:**
1. Develop and test locally with `ENABLED_DOMAINS=project_logs,leaves,profile`
2. Deploy to dev environment, enable new domains
3. Internal testing for 1 week
4. Enable in production

### Phase 2: Expenses + Teams (~1-2 weeks)

**Expenses domain (4-5 tools):**

| Tool | Type | ERP Endpoint | Priority |
|------|------|-------------|----------|
| `expenses_get_summary` | Read | `expenses/limits-and-availed/` | P0 |
| `expenses_get_records` | Read | `expenses/person_expense/` (viewset list) | P0 |
| `expenses_submit` | Write | `expenses/person_expense/` (viewset create) | P1 |
| `expenses_get_recreational_trip_status` | Read | `expenses/recreational-trip/status/` | P2 |

**Teams domain (4-5 tools):**

| Tool | Type | ERP Endpoint | Priority |
|------|------|-------------|----------|
| `teams_get_my_teams` | Read | `teams/person_teams/list/` | P0 |
| `teams_get_members` | Read | `teams/team_members/<id>/` | P0 |
| `teams_get_structure` | Read | `teams/structure/` (viewset list) | P1 |
| `teams_get_my_leads` | Read | `teams/person-leads/` | P1 |

### Phase 3: Training + Helpdesk (~1-2 weeks)

**Training domain (4-5 tools):**

| Tool | Type | ERP Endpoint | Priority |
|------|------|-------------|----------|
| `training_get_ongoing` | Read | `training/ongoing/list/` | P0 |
| `training_get_history` | Read | `training/history/list/` | P0 |
| `training_get_recommended` | Read | `training/person-recommended-trainings/` | P1 |
| `training_register` | Write | `training/register/` | P1 |

**Helpdesk domain (3-4 tools):**

| Tool | Type | ERP Endpoint | Priority |
|------|------|-------------|----------|
| `helpdesk_list_tickets` | Read | `helpdesk/request/list/` | P0 |
| `helpdesk_create_ticket` | Write | `helpdesk/request/create/` | P0 |
| `helpdesk_get_categories` | Read | `helpdesk/categories-list/` | P1 |

### Phase 4: Rewards + Competencies (~1-2 weeks)

**Rewards domain (3-4 tools):**

| Tool | Type | ERP Endpoint | Priority |
|------|------|-------------|----------|
| `rewards_get_my_teams` | Read | `rewards/person/teams/list/` | P0 |
| `rewards_get_presentations` | Read | `rewards/person/presentations/` | P1 |
| `rewards_get_team_review` | Read | `rewards/person/team_review/get/` | P1 |

**Competencies domain (3-4 tools):**

| Tool | Type | ERP Endpoint | Priority |
|------|------|-------------|----------|
| `competencies_get_my_competencies` | Read | `competencies/person/competencies/` | P0 |
| `competencies_get_structure` | Read | `competencies/structure/` | P1 |
| `competencies_get_roles` | Read | `competencies/roles/list/` | P2 |

### Phase 5+ (Future): Sensitive Domains

Payroll, invoices, and financial reporting tools are **deferred** due to:
- Sensitivity of compensation data (requires SEC-08).
- Mostly admin/lead-only APIs (limited user base).
- Higher risk of misuse by AI clients.

These should only be added after the governance framework is mature and there is demonstrated demand.

---

## 13. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| **ERP API instability during expansion** -- backend APIs may change or break | Medium | High | Contract tests (request payload snapshots). Pin ERP API version expectations in client docstrings. |
| **Tool naming confusion** -- AI clients select wrong tool | Medium | Medium | Domain prefix convention. Descriptive docstrings as first line. Fewer tools per domain (start minimal, add on demand). |
| **Mock fixture explosion** -- test maintenance burden grows with tool count | Medium | Medium | Domain-scoped fixtures. Factory functions for mock clients. Consider auto-mock from client method signatures. |
| **Feature flag staleness** -- domains never promoted from dev to prod | Low | Low | Quarterly review of `ENABLED_DOMAINS` across environments. Dashboard showing which domains are enabled where. |
| **Single-process crash from domain bug** | Low | High | `@_tool_error_handler` catches all exceptions per tool. Python process crash requires something very unusual (segfault, OOM). Docker restart policy handles crash recovery. |
| **AI client tool overload** -- 50+ tools degrades AI tool selection quality | Medium | Medium | Start minimal (P0 tools only per domain). Add P1/P2 tools only when user demand is demonstrated. Consider MCP resource hints for tool grouping if the protocol supports it in the future. |
| **Token cache coherence with new domains** -- different domain clients might conflict | Low | Low | Shared token cache by design. Token exchange is domain-agnostic (one ERP token works for all domains). |
| **ERP rate limiting** -- bulk MCP usage overwhelms ERP backend | Low | High | Per-user write rate limits in MCP layer. Connection pool size limits in httpx. Exponential backoff on 429 responses. |

---

## 14. Architecture Decision Records

### ADR-0001: Domain Module Architecture over Microservices

**Status:** Proposed
**Context:** The erp-mcp server needs to grow from 11 tools (1 domain) to 50+ tools (8+ domains). We evaluated four architecture patterns: monolithic with modules, plugin/registry, multiple MCP servers, and hybrid with feature flags.
**Decision:** Use a monolithic server with domain modules and environment-variable feature flags (Option D).
**Consequences:**
- (+) Simplest migration from current state. Zero operational overhead increase.
- (+) Single auth flow, single deployment, single container.
- (+) Feature flags enable safe incremental rollout.
- (-) All domains share one process; a catastrophic bug in one domain could crash the server (mitigated by error handlers and Docker restart).
- (-) Cannot independently scale domains (irrelevant at current scale).
- Reversibility: HIGH. Any domain module can be extracted to a separate MCP server later with minimal friction.

### ADR-0002: Shared httpx Client and Token Cache

**Status:** Proposed
**Context:** Domain clients need an HTTP client and token cache. Options: shared instance, per-domain instances, or factory-per-request.
**Decision:** Share a single `httpx.AsyncClient` and `TTLCache` across all domain clients via `ClientManager`.
**Consequences:**
- (+) Connection pooling efficiency. One token exchange serves all domains.
- (+) Simpler lifecycle management (one `close()` call).
- (-) A hung connection in one domain's request could exhaust the pool for others (mitigated by per-request timeouts).
- Reversibility: HIGH. Domain clients can be given their own httpx instances by changing `ClientManager` construction.

### ADR-0003: Feature Flag Domain Loading

**Status:** Proposed
**Context:** We need a mechanism to enable/disable domain tool sets without code changes or separate deployments.
**Decision:** Use `ENABLED_DOMAINS` environment variable parsed at startup to conditionally import domain modules.
**Consequences:**
- (+) Zero-downtime domain activation/deactivation via container restart.
- (+) CI tests all domains; production enables a subset.
- (+) Rollback is trivial: remove domain from env var.
- (-) Flag management adds a small operational concern. Mitigated by documenting the flag in `.env.example` and the deployment runbook.
- Reversibility: HIGH. Remove the flag and import all domains unconditionally.

### ADR-0004: Grandfathered Tool Names for Existing Tools

**Status:** Proposed
**Context:** Existing 11 tools use names without domain prefix (e.g., `get_week_logs` instead of `project_logs_get_week_logs`). New domains will use prefixed names.
**Decision:** Do NOT rename existing tools. New domains use `{domain}_{verb}_{resource}` convention.
**Consequences:**
- (+) Zero breaking changes for existing AI client configurations and user workflows.
- (+) No migration period or dual-name support needed.
- (-) Inconsistency between old and new naming. Acceptable because the project_logs tools are well-established.
- Reversibility: N/A. Names are part of the public API contract.

---

## Summary

This plan recommends evolving the erp-mcp server through **modular domain decomposition within a single container**, controlled by **environment-variable feature flags**. The approach prioritizes simplicity, incremental delivery, and reversibility.

**Key architectural principles:**
1. **One server, many modules.** Domain tools are Python modules that register with a shared FastMCP instance.
2. **Shared infrastructure, domain-specific logic.** Auth, HTTP client, caching, and error handling are shared. Business logic (tool definitions, API calls) is domain-scoped.
3. **Feature flags for safety.** New domains are developed behind `ENABLED_DOMAINS`, tested in dev, then promoted to production.
4. **Security controls scale automatically.** AST-based test discovery ensures every new tool is covered by SEC-01 through SEC-07 without manual registration.
5. **Start minimal, add on demand.** Each domain starts with 3-5 high-value read tools. Write tools and secondary reads are added based on demonstrated user demand.

**Timeline estimate:**
- Phase 0 (Refactor): 2-3 days
- Phase 1 (Leaves + Profile): 1-2 weeks
- Phase 2 (Expenses + Teams): 1-2 weeks
- Phase 3 (Training + Helpdesk): 1-2 weeks
- Phase 4 (Rewards + Competencies): 1-2 weeks

Total: **5-9 weeks** to reach ~50 tools across 8 domains, with each phase independently deployable and delivering user value.
