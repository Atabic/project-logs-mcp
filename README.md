# ERP MCP Server

MCP (Model Context Protocol) server for Arbisoft's ERP system. Enables AI assistants to read and write project time logs via OAuth-authenticated tool calls.

## Quick Start

### Prerequisites

- Python 3.12+
- Google OAuth Client ID and Secret (Google Workspace)
- Access to Arbisoft ERP API

### Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
export GOOGLE_CLIENT_ID="your-client-id"
export GOOGLE_CLIENT_SECRET="your-client-secret"
export ERP_API_BASE_URL="http://127.0.0.1:8000/api/v1"
export MCP_BASE_URL="http://localhost:8100"
```

### Run

```bash
python server.py
```

The server starts on port 8100 (configurable via `MCP_PORT`).

## Connecting AI Clients

### Claude Code

```bash
claude mcp add --transport http erp-mcp https://dev-workstream.arbisoft.com/mcp
```

### Claude Desktop / Cursor

Add to your MCP config:

```json
{
  "mcpServers": {
    "erp-mcp": {
      "url": "https://dev-workstream.arbisoft.com/mcp"
    }
  }
}
```

### Codex / Gemini CLI

```bash
# Codex
codex mcp add --transport http erp-mcp https://dev-workstream.arbisoft.com/mcp

# Gemini CLI
gemini mcp add --transport http erp-mcp https://dev-workstream.arbisoft.com/mcp
```

## Available Tools

### Read Tools

| Tool | Description |
|------|-------------|
| `get_active_projects` | List active projects/subteams for the authenticated user |
| `get_log_labels` | Get available log labels for categorizing entries |
| `get_week_logs` | Get project logs for a specific week |
| `get_day_logs` | Get detailed logs for a specific day |
| `get_logs_for_date_range` | Get logs for a date range |
| `get_month_logs` | Get all logs for a month |

### Write Tools

| Tool | Description |
|------|-------------|
| `create_or_update_log` | Add or update a time log entry |
| `delete_log` | Remove a time log entry |
| `complete_week_log` | Mark a week as completed (or save draft) |
| `fill_logs_for_days` | Bulk-fill logs for a date range (max 31 days) |

### Diagnostic Tools

| Tool | Description |
|------|-------------|
| `check_person_week_project_exists` | Check if PersonWeekProject exists for a date/project |

## Docker

```bash
docker build -t erp-mcp .
docker run -p 8100:8100 --env-file .env erp-mcp
```

Image is based on `python:3.12.8-slim-bookworm` (~150MB). No Playwright or browser dependencies.

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | (required) |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | (required) |
| `ERP_API_BASE_URL` | ERP API root URL | `https://erp.arbisoft.com/api/v1/` |
| `MCP_BASE_URL` | Public URL for OAuth callbacks | `https://erp.arbisoft.com` |
| `MCP_PORT` | Server port | `8100` |
| `ALLOWED_DOMAIN` | Google Workspace domain restriction | `arbisoft.com` |

## Testing

```bash
pip install pytest pytest-asyncio respx
pytest tests/ -v
```

## License

Internal Arbisoft project. Not for external distribution.
