# Standalone Project Logs MCP Server

A **completely standalone** Model Context Protocol (MCP) server for interacting with project logs in the ERP system. This server communicates with the production API and includes authentication and authorization to ensure only authorized users can access it.

## Features

- ✅ **Completely Independent**: No Django dependencies, runs as a standalone service
- ✅ **Production API**: Configure your ERP API base URL via `ERP_API_BASE_URL` 
- ✅ **Token-Based Authentication**: Use your Authorization token directly from browser (no password needed!)
- ✅ **Authorization**: Only authorized users can use the server
- ✅ **All Project Logs Operations**: Create, read, update, complete logs
- ✅ **Simplified Usage**: Email is optional once authenticated with token

## Installation

### Prerequisites

- Python 3.12+
- Internet access to your ERP API host

### Setup

**Important**: This MCP server is completely standalone and isolated from Django. It has its own virtual environment and dependencies.

1. **Create a virtual environment** (in the MCP server directory):
```bash
cd /path/to/project-logs-mcp
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. **Install dependencies** (only MCP SDK and aiohttp - no Django):
```bash
pip install -r requirements.txt
```

**Note**: The `requirements.txt` only contains `mcp` and `aiohttp`. No Django packages are required or installed.

3. **Configure authorized users**:

   Option A: Edit `authorized_users.json`:
   ```json
   {
     "authorized_emails": [
       "user1@example.com",
       "user2@example.com"
     ]
   }
   ```

   Option B: Set environment variable:
   ```bash
   export MCP_AUTHORIZED_USERS="user1@example.com,user2@example.com"
   ```

## Usage

### Running the Server

**Important**: The MCP server should be run by an MCP client (like Claude Desktop), not directly in a terminal. If you run it directly, do not type anything in that terminal - all input is treated as JSON-RPC messages and will cause errors.

To test the server is working:
```bash
cd /path/to/project-logs-mcp
source venv/bin/activate
python server.py
# Do NOT type anything in this terminal - it will cause JSON parsing errors
# Press Ctrl+C to stop
```

The server is designed to be invoked by MCP clients automatically. See "Connecting to an LLM" section below.

### Environment Variables

- `ERP_API_BASE_URL`: Override API base URL (default: `https://your-erp.example.com/api/v1/` — set to your actual ERP API URL)
- `MCP_AUTHORIZED_USERS`: Comma-separated list of authorized emails
- `MCP_GOOGLE_CLIENT_ID`: Google OAuth client ID (same as Django `EXTERNAL_GOOGLE_CLIENT_ID`). Required for **browser sign-in** (`authenticate_via_browser`). Get it from your Google Cloud Console and add `http://127.0.0.1:8765/callback` to **Authorized redirect URIs** (or `http://127.0.0.1:<MCP_OAUTH_PORT>/callback` if you change the port).
- `MCP_OAUTH_PORT`: Port for the local sign-in server (default: `8765`). Must match the redirect URI whitelisted in Google Cloud Console.
- `MCP_SSE_HOST`: When running the SSE server (`server_sse.py`), bind address (default: `0.0.0.0`).

### Connecting to an LLM

#### Claude Desktop Configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "project-logs": {
      "command": "/path/to/project-logs-mcp/venv/bin/python",
      "args": [
        "/path/to/project-logs-mcp/server.py"
      ],
      "env": {
        "ERP_API_BASE_URL": "https://your-erp.example.com/api/v1/",
        "MCP_AUTHORIZED_USERS": "user1@example.com,user2@example.com",
        "MCP_GOOGLE_CLIENT_ID": "YOUR_GOOGLE_OAUTH_CLIENT_ID"
      }
    }
  }
}
```

For **browser sign-in** (`authenticate_via_browser`), set `MCP_GOOGLE_CLIENT_ID` to your Google OAuth client ID and add `http://127.0.0.1:8765/callback` to Authorized redirect URIs in Google Cloud Console.

**Note**: Use the path to this project's virtual environment. The server is fully standalone and can be moved anywhere.

#### Cursor Configuration

In Cursor, add to your MCP settings (usually in Cursor settings or `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "project-logs": {
      "command": "/path/to/project-logs-mcp/venv/bin/python",
      "args": [
        "/path/to/project-logs-mcp/server.py"
      ],
      "env": {
        "ERP_API_BASE_URL": "https://your-erp.example.com/api/v1/",
        "MCP_AUTHORIZED_USERS": "user1@example.com,user2@example.com"
      }
    }
  }
}
```

**Alternative**: Using the wrapper script (ensures correct venv):
```json
{
  "mcpServers": {
    "project-logs": {
      "command": "/path/to/project-logs-mcp/run_server.sh",
      "env": {
        "ERP_API_BASE_URL": "https://your-erp.example.com/api/v1/",
        "MCP_AUTHORIZED_USERS": "user1@example.com,user2@example.com"
      }
    }
  }
}
```

**Important**: Use this project's `venv` directory so dependencies stay isolated.

### Deploying with a URL (remote MCP)

To expose this MCP over HTTP so **any AI app can add it via an MCP URL** (e.g. `https://your-app.example.com/sse`):

1. **Install SSE dependencies** (in the same venv):
   ```bash
   pip install -r requirements-sse.txt
   ```

2. **Run the SSE server** (locally or on a host):
   ```bash
   python server_sse.py
   # Or: python server_sse.py --port 8000
   ```
   The MCP endpoint is **`http://<host>:<port>/sse`**. Clients that support "MCP URL" or "remote MCP" can use this URL.

3. **Deploy to a free host** (optional). You need a long-running process and a public URL with HTTPS. Options:

   | Platform | Free tier | Notes |
   |----------|-----------|--------|
   | [Render](https://render.com) | Yes (free tier) | See **Deploy on Render** below. Free tier sleeps after inactivity; first request may be slow. |
   | [Railway](https://railway.app) | $5 free credit/month | Deploy from GitHub; set start command to `pip install -r requirements-sse.txt && python server_sse.py`. Good for always-on. |
   | [Fly.io](https://fly.io) | Free allowance for small VMs | Use a `Dockerfile` or `fly.toml` to run `server_sse.py` (e.g. with uvicorn). Gives you a URL like `https://your-app.fly.dev`. |
   | [Google Cloud Run](https://cloud.run) | Free tier (request-based) | Containerize the app; Cloud Run can scale to zero. Suited if the AI app sends requests (SSE may need careful configuration). |

   **Security**: If you deploy publicly, restrict who can call your API (e.g. auth, IP allowlist, or keep it for personal use). Set `MCP_AUTHORIZED_USERS` so only allowed emails can use the tools.

   **Deploy on Render (step-by-step):**
   1. In [Render Dashboard](https://dashboard.render.com), click **New** → **Web Service**.
   2. Connect your Git provider (GitHub/GitLab) and select this repository.
   3. Configure:
      - **Name:** e.g. `project-logs-mcp`
      - **Region:** choose closest to you
      - **Root Directory:** leave blank (app is at repo root)
      - **Runtime:** Python 3
      - **Build Command:** `pip install -r requirements-sse.txt`
      - **Start Command:** `python server_sse.py`
   4. Under **Environment**, add:
      - `ERP_API_BASE_URL` = your ERP API base URL (e.g. `https://your-erp.example.com/api/v1/`)
      - `MCP_AUTHORIZED_USERS` = your allowed emails (comma-separated)
   5. Click **Create Web Service**. Wait for the first deploy to finish.
   6. Your MCP URL is: **`https://<your-service-name>.onrender.com/sse`** (use this in AI apps that support remote MCP URL). The server reads `PORT` from the environment automatically.

4. **Use the URL in an AI app**: In the app’s MCP or “add server” settings, enter your deployed URL (e.g. `https://your-app.onrender.com/sse`). Support for **remote MCP URL** depends on the app (Claude Desktop uses local stdio; some web apps support adding an MCP URL).

### Backend compatibility (ERP API)

This MCP uses the following project-logs API endpoints. When pulling new ERP commits, check for changes to these:

| Endpoint | MCP use |
|----------|--------|
| `GET/POST project-logs/person/list/` | Week logs list |
| `GET project-logs/person/get/<id>/` | Week log detail |
| `GET project-logs/person/month-list/` | Month list |
| `GET project-logs/person/active_project_list/` | Active projects |
| `PATCH project-logs/person/person-week-log/save/<id>/` | Save week log |
| `PATCH project-logs/person/person-week-log/complete/<id>/` | Complete week (body: `save_draft`) |
| `POST project-logs/person/person-week-log-from-slack/` | Create log (Slack-style) |
| `GET project-logs/log_labels/` | Log labels |

**Checked (after syncing with origin):** Recent project-logs commits (ProjectLogCompleteView PATCH fix, export crash fix, team-logged-hours response change) were backend-only or for endpoints this MCP does not use. No MCP implementation changes were required. If the ERP adds or changes any of the endpoints above, update `api_client.py` and optionally this table.

## Available Tools

### 1. `authenticate_with_token` ⭐ **RECOMMENDED**
Authenticate with ERP system using an Authorization token. **This is the recommended method!**

**How to get your token:**
1. Open your browser's Developer Tools (F12)
2. Go to Network tab
3. Make any API call to your ERP host (navigate or refresh the app)
4. Look at the request headers
5. Copy the value from the `Authorization` header (e.g., `Token abc123...`)

**Parameters:**
- `token` (string, required): Authorization token value (can be "Token xyz" or just "xyz")
- `email` (string, optional): Email address for identification/authorization check

**Example:**
```json
{
  "token": "Token your-token-here"
}
```

**Note:** Once authenticated with a token, you don't need to provide email for subsequent operations.

### 1b. `authenticate_with_google` (external sign-in with Gmail)
Authenticate via **Google sign-in** using the existing Django API. The user signs in with Gmail (e.g. in a web or mobile app) and obtains a **Google OAuth access token**. That token is passed to this tool; MCP calls the backend **POST core/google-login/** which validates the token with Google and returns an ERP token. MCP then stores the ERP token for subsequent project-logs (and other) API calls.

**Flow:**
1. User signs in with Google in a client (browser OAuth, mobile SDK, or any app that can get a Google access token).
2. Client passes the Google `access_token` to this MCP tool (e.g. via the LLM).
3. MCP POSTs `{"platform": "google", "access_token": "<token>"}` to `core/google-login/`.
4. Backend verifies the token with Google, finds the user by email, and returns `{ "token": "<erp_token>", "email": "..." }`.
5. MCP stores the ERP token; all later tools use it.

**Parameters:**
- `access_token` (string, required): Google OAuth access token from the client after sign-in.
- `email` (string, optional): For identification; if omitted, the backend returns the email from Google.

**Note:** The backend must have the user registered (same as token login). If authorization is configured, the returned email must be in the authorized list.

### 1c. `authenticate_via_browser` (Figma-style: sign in in browser, then return to Cursor)
Sign in by opening a URL in your browser (like Figma MCP). No token copying.

**Flow:**
1. User (or LLM) calls the tool `authenticate_via_browser`.
2. MCP starts a small local HTTP server on `127.0.0.1:8765` (or `MCP_OAUTH_PORT`) and returns a **sign-in URL** (e.g. `http://127.0.0.1:8765/login`).
3. User opens that URL in the browser → clicks “Continue with Google” → signs in with Google.
4. Google redirects back to the local server with an access token; the server exchanges it with the ERP backend (`core/google-login/`) and stores the ERP token.
5. The browser shows “Authentication complete. You can close this window and return to Cursor.”
6. User closes the tab and continues in Cursor; other MCP tools use the stored session.

**Setup:** Set `MCP_GOOGLE_CLIENT_ID` to your Google OAuth client ID (same as Django’s `EXTERNAL_GOOGLE_CLIENT_ID`). In Google Cloud Console, add `http://127.0.0.1:8765/callback` to **Authorized redirect URIs** (or the same with your `MCP_OAUTH_PORT`).

**Parameters:** None.

### 1d. `authenticate_via_erp_browser` (open ERP in your browser, token captured automatically)
The MCP opens the ERP site in a browser, you log in, and the MCP reads the token from the page—no copy/paste.

**Flow:**
1. User (or LLM) calls the tool `authenticate_via_erp_browser`.
2. A browser window opens to your ERP site (your installed **Chrome** or **Edge** is used when available; otherwise Playwright’s Chromium).
3. You log in on that page (email or Google, same as normal ERP).
4. The MCP detects the token on the page and stores it; the browser can close.
5. Other MCP tools then use that token automatically.

**Requirements:** Playwright. The MCP prefers your installed Chrome or Edge; if you have neither, install Chromium: `pip install playwright` then `playwright install chromium`. No Google Client ID.

**Parameters:** None.

### 2. `get_week_logs`
Get project logs for a specific week.

**Parameters:**
- `week_starting` (string, required): Week starting date (YYYY-MM-DD)
- `email` (string, optional): Email address (uses current authenticated user if not provided)

### 3. `get_day_logs` ⭐ **NEW**
Get detailed project logs for a specific day. Returns all projects, tasks, and time logged for that day with full details.

**Parameters:**
- `date` (string, required): Date in YYYY-MM-DD format
- `email` (string, optional): Email address (uses current authenticated user if not provided)

**Returns:**
- Detailed breakdown of all projects and tasks logged on that day
- Total hours, minutes, and decimal hours for the day
- Project names, task descriptions, labels, and time breakdowns
- Week log metadata

**Example:**
```json
{
  "date": "2026-01-05",
  "projects": [
    {
      "project_id": 123,
      "project_name": "Your Project Name",
      "tasks": [
        {
          "description": "Feature development",
          "hours": 8,
          "minutes": 0,
          "decimal_hours": 8.0,
          "label_id": 5
        }
      ],
      "total_hours": 8,
      "total_minutes": 0,
      "total_decimal_hours": 8.0
    }
  ],
  "total_logged_time": {
    "hours": 8,
    "minutes": 0,
    "decimal_hours": 8.0
  }
}
```

### 4. `get_logs_for_date_range`
Get project logs for a date range.

**Parameters:**
- `start_date` (string, required): Start date (YYYY-MM-DD)
- `end_date` (string, required): End date (YYYY-MM-DD)
- `email` (string, optional): Email address (uses current authenticated user if not provided)

### 5. `create_or_update_log`
Add a time log for a date. Project and label can be specified by **ID** (from `get_active_projects` / `get_log_labels`) or by **name**; names are resolved automatically. When a week log exists for that week, the backend **Save API** (PATCH `person-week-log/save/<id>/`) is used; otherwise the **Slack endpoint** is tried. The backend does **not** auto-create week logs (neither the Save API nor the Slack endpoint creates `PersonWeekLog`/`PersonWeekProject`). If the week has no log yet, create one entry for that week in the web interface once, then you can add more here.

**Parameters:**
- `date` (string, required): Date (YYYY-MM-DD)
- `project_id` (integer) **or** `project_name` (string): Project/subteam (ID or name, e.g. "Your Project Name")
- `description` (string, required): Work description
- `hours` (number, required): Hours (decimal ok, e.g. 2 or 8.5)
- `label_id` (integer, optional) **or** `label_name` (string, optional): Label (e.g. "Coding" or ID)
- `email` (string, optional): Email (uses current user if omitted)

### 6. `complete_week_log`
Mark a week's logs as completed.

**Parameters:**
- `week_starting` (string, required): Week starting date (YYYY-MM-DD)
- `email` (string, optional): Email address (uses current authenticated user if not provided)
- `save_draft` (boolean, optional): Save as draft (default: false)

### 7. `get_active_projects`
Get list of active projects for a person.

**Parameters:**
- `email` (string, optional): Email address (uses current authenticated user if not provided)

### 8. `get_log_labels`
Get available log labels.

**Parameters:**
- `email` (string, optional): Email address (uses current authenticated user if not provided)

### 9. `get_month_logs`
Get all logs for a specific month.

**Parameters:**
- `year` (integer, required): Year (e.g., 2024)
- `month` (integer, required): Month (1-12)
- `email` (string, optional): Email address (uses current authenticated user if not provided)

### 10. `fill_logs_for_days`
Fill logs for multiple days at once.

**Parameters:**
- `start_date` (string, required): Start date (YYYY-MM-DD)
- `end_date` (string, required): End date (YYYY-MM-DD)
- `project_id` (integer, required): Project ID
- `description` (string, required): Work description
- `email` (string, optional): Email address (uses current authenticated user if not provided)
- `hours_per_day` (number, optional): Hours per day (default: 8)
- `label_id` (integer, optional): Label ID
- `skip_weekends` (boolean, optional): Skip weekends (default: false)

## Security

### Authentication
- Users must authenticate with an Authorization token (from browser network tab)
- Authentication tokens are stored in memory (not persisted)
- Each request requires valid authentication
- Token-based authentication is secure and doesn't require password storage

### Authorization
- Only emails in `authorized_users.json` or `MCP_AUTHORIZED_USERS` can use the server
- Authorization is checked before authentication
- Unauthorized users receive an error message

### Best Practices
1. **Never commit `authorized_users.json`** with real emails to version control
2. **Use environment variables** for sensitive configuration
3. **Rotate passwords** regularly
4. **Monitor usage** through ERP system logs

## Example LLM Interactions

### Example 1: Authenticate with Token and Get Logs
```
User: "Authenticate me with token 'Token your-token-here' and get my logs for December 2025"

LLM will:
1. Call authenticate_with_token with the token
2. Call get_month_logs with year=2025, month=12
3. Display results in readable format
```

### Example 2: Fill Logs After Authentication
```
User: "Fill my logs for last week (8 hours per day) for project ID 123 with description 'Development work'"

LLM will:
1. Check if authenticated (if not, ask for token)
2. Call fill_logs_for_days with appropriate parameters
3. Confirm completion
```

### Example 3: Get Monthly Summary
```
User: "Show me all my project logs for January 2024"

LLM will:
1. Check if authenticated (if not, ask for token)
2. Call get_month_logs with year=2024, month=1
3. Display results in readable format
```

## How to Get Your Authorization Token

1. **Open ERP in your browser**: Go to your ERP app URL
2. **Open Developer Tools**: Press `F12` or `Cmd+Option+I` (Mac) / `Ctrl+Shift+I` (Windows/Linux)
3. **Go to Network tab**: Click on the "Network" tab
4. **Make any API call**: Navigate the ERP app or refresh the page
5. **Find an API request**: Look for requests to your ERP API (e.g. `/api/v1/`)
6. **Copy Authorization header**:
   - Click on a request
   - Go to "Headers" section
   - Find "Authorization" header
   - Copy the value (e.g., `Token your-token-here`)
7. **Use in MCP**: Provide this token value to the `authenticate_with_token` tool

## Architecture

The standalone server consists of:

1. **server.py**: Main MCP server with tool definitions
2. **api_client.py**: HTTP client for ERP API calls
3. **auth.py**: Authentication and authorization manager
4. **authorized_users.json**: List of authorized emails

## Troubleshooting

### Authentication Errors
- Ensure email/password are correct
- Check if email is in authorized list
- Verify ERP API is accessible

### API Errors
- Check network connectivity to your ERP API host
- Verify API endpoints haven't changed
- Check ERP system status

### Authorization Errors
- Verify email is in `authorized_users.json` or environment variable
- Check email format (case-insensitive)

## Standalone and Isolated

This MCP server is **completely independent** from the Django backend:

| Feature | Standalone MCP Server | Django Backend |
|---------|----------------------|----------------|
| **Dependencies** | Only `mcp` + `aiohttp` | Django + many packages |
| **Virtual Environment** | Own `venv/` directory | Separate `venv/` directory |
| **Python Path** | Only adds current directory | Django project structure |
| **Database Access** | Via HTTP API only | Direct ORM access |
| **Deployment** | Can run anywhere | Must be with Django |
| **Authentication** | API-based (token) | Django sessions |
| **Authorization** | Config file (`authorized_users.json`) | Django permissions |
| **Communication** | HTTP REST API calls | Direct Django ORM |

**Key Points:**
- ✅ No Django imports or dependencies
- ✅ Own isolated virtual environment
- ✅ Can be moved/copied to any location
- ✅ No shared Python packages with Django
- ✅ Communicates with Django only via HTTP API


## License

This MCP server is part of the ERP project and follows the same license.
