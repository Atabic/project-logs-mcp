# ERP MCP Server -- Architecture

## System Overview

The ERP MCP Server follows a layered design: AI clients (Claude Desktop, Cursor, Codex CLI, Gemini CLI) connect over the Model Context Protocol to a FastMCP server running behind NGINX. Each tool call flows through an authentication layer that exchanges Google OAuth tokens for ERP session tokens, then delegates to domain-specific clients that call the ERP backend's Django REST API. This separation keeps transport, auth, business logic, and HTTP concerns in distinct modules.

## System Architecture

```mermaid
flowchart LR
    subgraph Clients["AI Clients"]
        claude["Claude Desktop"]
        cursor["Cursor"]
        codex["Codex CLI"]
    end

    nginx["NGINX Reverse Proxy<br/>dev-workstream.arbisoft.com/mcp"]

    subgraph Server["FastMCP Server :8100"]
        entry["server.py<br/>GoogleProvider + Middleware + /health"]
        tools["tools/<br/>timelogs 11 + leaves 10"]
        auth["_auth.py<br/>get_erp_token + domain check"]
        subgraph ClientLayer["clients/"]
            base["BaseERPClient<br/>TTLCache + httpx"]
            tlClient["TimelogsClient"]
            lvClient["LeavesClient"]
        end
    end

    erpBackend["ERP Backend<br/>Django REST API"]
    db[("PostgreSQL")]

    claude --> nginx
    cursor --> nginx
    codex --> nginx
    nginx --> entry
    entry --> tools
    tools --> auth
    auth --> base
    tools --> tlClient
    tools --> lvClient
    tlClient --> base
    lvClient --> base
    base --> erpBackend
    erpBackend --> db

    classDef client fill:#4a90d9,stroke:#2c5f8a,color:#fff
    classDef proxy fill:#f5a623,stroke:#c17d1a,color:#fff
    classDef server fill:#7ed321,stroke:#5a9a18,color:#fff
    classDef backend fill:#9b59b6,stroke:#6c3483,color:#fff
    classDef database fill:#e74c3c,stroke:#a93226,color:#fff

    class claude,cursor,codex client
    class nginx proxy
    class entry,tools,auth,base,tlClient,lvClient server
    class erpBackend backend
    class db database
```

## Authentication Flow

```mermaid
sequenceDiagram
    autonumber

    participant AI as AI Client
    participant MCP as FastMCP Server
    participant Google as Google OAuth
    participant ERP as ERP Backend

    rect rgb(240, 248, 255)
        Note over AI,Google: Phase 1 -- OAuth Authorization
        AI->>MCP: Tool call (no session)
        MCP-->>AI: 401 Unauthorized
        AI->>MCP: GET /authorize
        MCP->>Google: Redirect to consent screen
        Google-->>AI: User grants consent
        AI->>MCP: GET /callback?code=...
        MCP->>Google: Exchange code for tokens
        Google-->>MCP: Access token + ID token
        MCP-->>AI: Session established
    end

    rect rgb(245, 255, 245)
        Note over AI,ERP: Phase 2 -- Authenticated Tool Call
        AI->>MCP: Tool call (with session token)
        MCP->>MCP: get_erp_token()
        Note right of MCP: Extract AccessToken<br/>from request context
        MCP->>MCP: Domain check (hd == arbisoft.com)

        alt Cache hit (SHA-256 key)
            MCP->>MCP: Return cached ERP token
        else Cache miss
            MCP->>ERP: POST /core/google-login/<br/>access_token=...
            ERP-->>MCP: ERP DRF token + email
            MCP->>MCP: Cache token (15 min TTL)
        end

        MCP->>ERP: API call with Authorization header
        ERP-->>MCP: Response data
        MCP-->>AI: Tool result
    end
```

## Tool Request Lifecycle

This sequence shows `timelogs_upsert_entry` as a representative write-path example.

```mermaid
sequenceDiagram
    autonumber

    participant AI as AI Assistant
    participant Tool as tools/timelogs.py
    participant Auth as _auth.py
    participant Reg as ERPClientRegistry
    participant TL as TimelogsClient
    participant Base as BaseERPClient
    participant ERP as ERP Backend

    AI->>Tool: timelogs_upsert_entry(date, project_name, desc, hours)
    Note right of Tool: @mcp.tool + @tool_error_handler

    Tool->>Auth: get_erp_token()
    Auth->>Auth: Extract AccessToken + domain check
    Auth->>Base: exchange_google_token()
    Base-->>Auth: (erp_token, email)
    Auth-->>Tool: (token, email)

    Tool->>Reg: get_registry().timelogs
    Reg-->>Tool: TimelogsClient

    Tool->>TL: resolve_project_id(token, project_name)
    TL->>Base: GET active_project_list/
    Base->>ERP: GET active_project_list/
    ERP-->>Base: project list
    Base-->>TL: project list
    TL-->>Tool: resolved project_id

    Tool->>TL: create_or_update_log(token, date, project_id, ...)
    TL->>Base: GET person/list/ (find week log)
    Base->>ERP: GET person/list/?year=...
    ERP-->>Base: week logs
    Base-->>TL: week logs

    alt Path A -- Week log exists
        TL->>Base: GET person/get/{id}/
        Base->>ERP: GET person/get/{id}/
        ERP-->>Base: week log detail
        Base-->>TL: week log detail
        TL->>TL: Mutate projects/tasks/days in memory
        TL->>Base: PATCH person-week-log/save/{id}/
        Base->>ERP: PATCH save/{id}/
        ERP-->>Base: updated log
    else Path B -- No week log
        TL->>Base: POST person-week-log-from-slack/
        Base->>ERP: POST person-week-log-from-slack/
        ERP-->>Base: created log
    end

    Base-->>TL: result
    TL-->>Tool: result
    Tool->>Tool: check_erp_result()
    Tool-->>AI: Tool response
```

## Code Structure

```mermaid
flowchart TB
    subgraph Entry["Entry Point"]
        server["server.py<br/>FastMCP + GoogleProvider<br/>SecurityHeadersMiddleware + /health"]
    end

    subgraph Tools["Tools Layer"]
        toolInit["tools/__init__.py<br/>load_domains()"]
        toolTL["tools/timelogs.py<br/>11 tools"]
        toolLV["tools/leaves.py<br/>10 tools"]
    end

    subgraph AuthLayer["Auth Layer"]
        authMod["_auth.py<br/>get_erp_token()<br/>tool_error_handler()<br/>check_erp_result()"]
    end

    subgraph ClientLayer["Client Layer"]
        clientInit["clients/__init__.py<br/>ERPClientRegistry"]
        clientBase["clients/_base.py<br/>BaseERPClient + TTLCache"]
        clientTL["clients/timelogs.py<br/>TimelogsClient"]
        clientLV["clients/leaves.py<br/>LeavesClient"]
    end

    constants["_constants.py<br/>MAX_FILL_DAYS, MAX_LEAVE_DAYS, ..."]
    erpAPI["ERP Backend API"]

    server --> toolInit
    toolInit --> toolTL
    toolInit --> toolLV
    toolTL --> authMod
    toolLV --> authMod
    toolTL --> clientInit
    toolLV --> clientInit
    authMod --> clientInit
    clientInit --> clientBase
    clientInit --> clientTL
    clientInit --> clientLV
    clientTL --> clientBase
    clientLV --> clientBase
    clientBase --> erpAPI

    toolTL -.-> constants
    toolLV -.-> constants
    clientBase -.-> constants

    classDef entry fill:#4a90d9,stroke:#2c5f8a,color:#fff
    classDef tools fill:#7ed321,stroke:#5a9a18,color:#fff
    classDef auth fill:#f5a623,stroke:#c17d1a,color:#fff
    classDef clients fill:#9b59b6,stroke:#6c3483,color:#fff
    classDef shared fill:#95a5a6,stroke:#707b7c,color:#fff
    classDef external fill:#e74c3c,stroke:#a93226,color:#fff

    class server entry
    class toolInit,toolTL,toolLV tools
    class authMod auth
    class clientInit,clientBase,clientTL,clientLV clients
    class constants shared
    class erpAPI external
```

**Legend:** Solid edges represent runtime calls. Dotted edges represent constants imports.

## Security Controls

| ID | Control | Implementation |
|----|---------|----------------|
| SEC-01 | No email parameter on tools | Identity derived from Google token claims only (`_auth.py`) |
| SEC-02 | Domain restriction | `hd` claim + email suffix check in `get_erp_token()` |
| SEC-03 | Hashed cache keys | SHA-256 of Google token in `BaseERPClient.exchange_google_token()` |
| SEC-04 | No stack traces in responses | `tool_error_handler()` logs server-side, returns generic `ToolError` |
| SEC-05 | Write guardrails | 31-day cap (timelogs), 90-day cap (leaves), audit logging on all writes |
| SEC-06 | No HTTP redirects | `follow_redirects=False` in httpx client |
| SEC-07 | HTTPS enforcement | `BaseERPClient.__init__` rejects non-HTTPS for non-localhost |

Tests enforce these controls via AST-based auto-discovery in `test_security.py`. Adding a new `@mcp.tool` in any `tools/*.py` file automatically includes it in security checks without manual test registration.

## ERP API Endpoints

| Endpoint | Method | Used By |
|----------|--------|---------|
| `project-logs/person/list/` | GET | `timelogs_get_week`, `timelogs_get_day` |
| `project-logs/person/get/{id}/` | GET | `timelogs_get_week`, `timelogs_upsert_entry`, `timelogs_delete_entry` |
| `project-logs/person/month-list/` | GET | `timelogs_get_month`, `timelogs_get_range` |
| `project-logs/person/active_project_list/` | GET | `timelogs_list_projects`, `timelogs_upsert_entry`, `timelogs_delete_entry` |
| `project-logs/person/person-week-log/save/{id}/` | PATCH | `timelogs_upsert_entry`, `timelogs_delete_entry` |
| `project-logs/person/person-week-log/complete/{id}/` | PATCH | `timelogs_complete_week` |
| `project-logs/person/person-week-log-from-slack/` | POST | `timelogs_upsert_entry` (fallback) |
| `project-logs/log_labels/` | GET | `timelogs_list_labels` |
| `core/google-login/` | POST | Token exchange (all tools) |
| `leaves/choices/get/` | GET | `leaves_get_choices` |
| `leaves/individual_leave_fiscal_years/` | GET | `leaves_get_fiscal_years` |
| `leaves/leave_summary/get/` | GET | `leaves_get_summary` |
| `leaves/person/month_leaves/` | GET | `leaves_list_month` |
| `leaves/holiday_records/` | GET | `leaves_list_month` |
| `leaves/list/` | GET | `leaves_list_mine` |
| `leaves/team_leaves/list/` | GET | `leaves_list_team` |
| `leaves/person-leave-encashments/` | GET/POST | `leaves_list_encashments`, `leaves_create_encashment` |
| `leaves/request/apply/` | POST | `leaves_apply` |
| `leaves/delete_leave/{id}/` | POST | `leaves_cancel` |

## Backend Model Chain

```
Person -> PersonTeam -> PersonWeekLog -> PersonWeekProject -> ProjectTask -> ProjectTaskDayDetail
```

`PersonWeekProject` must exist before time log entries can be added to a week. This record links a `PersonWeekLog` (the week container) to a `PersonTeam` (linking the person to a specific subteam/project). The ERP web application creates `PersonWeekProject` records through its normal team-assignment workflows -- the MCP server cannot create them.

The `timelogs_check_week_project` diagnostic tool verifies this prerequisite. When a user reports that log creation fails for a specific project and week, this tool confirms whether the underlying `PersonWeekProject` exists or needs to be created through the ERP web interface first.
