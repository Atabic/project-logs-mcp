#!/usr/bin/env python3
"""
Standalone MCP Server for Project Logs.

This server communicates with the ERP production API and includes
authentication and authorization to ensure only authorized users can access it.
"""

import asyncio
import json
import os
import sys
from typing import Any, Optional
from pathlib import Path

# Ensure we can import from the same directory (standalone, no parent dependencies)
# Add current directory to path for local imports only
current_dir = Path(__file__).parent.resolve()
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

try:
    from mcp import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool, TextContent
    except ImportError:
        raise ImportError(
            "MCP SDK not installed. Install it with: pip install mcp aiohttp"
        )

# Import from same directory (standalone, no Django dependencies)
from api_client import ProjectLogsAPIClient
from auth import AuthenticationManager
from oauth_server import (
    get_login_url,
    start_oauth_server,
    GOOGLE_CLIENT_ID,
    DEFAULT_OAUTH_PORT,
)
from erp_browser_auth import authenticate_via_erp_browser as _authenticate_via_erp_browser


# Initialize components
BASE_URL = os.getenv("ERP_API_BASE_URL", "https://your-erp.example.com/api/v1/")
auth_manager = AuthenticationManager()
api_client = ProjectLogsAPIClient(BASE_URL, auth_manager)

# Create MCP server
app = Server("project-logs-mcp-standalone")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools for project logs."""
    return [
        Tool(
            name="authenticate_with_token",
            description="Authenticate with ERP system using an Authorization token. Get your token from the browser's network tab (Authorization header value).",
            inputSchema={
                "type": "object",
                "properties": {
                    "token": {
                        "type": "string",
                        "description": "Authorization token value. Can be 'Token xyz' or just 'xyz'. Get this from your browser's network tab when making API calls to your ERP host."
                    },
                    "email": {
                        "type": "string",
                        "description": "Optional email address for identification (used for authorization check if configured)"
                    }
                },
                "required": ["token"]
            }
        ),
        Tool(
            name="authenticate_with_google",
            description="Sign in with Google: pass a Google OAuth access token; MCP calls the ERP backend (core/google-login/) to verify it and obtain an ERP session token. Use this when the user has signed in with Gmail and you have their Google access_token (e.g. from a client app or OAuth flow).",
            inputSchema={
                "type": "object",
                "properties": {
                    "access_token": {
                        "type": "string",
                        "description": "Google OAuth access token from signing in with Google (e.g. from a web/mobile client that did Google Sign-In)"
                    },
                    "email": {
                        "type": "string",
                        "description": "Optional email for identification; if omitted, backend returns the email from Google"
                    }
                },
                "required": ["access_token"]
            }
        ),
        Tool(
            name="authenticate_via_browser",
            description="Sign in via browser (like Figma MCP): returns a URL for the user to open. User signs in with Google in the browser, is redirected back to complete auth, then can close the window and use other MCP tools. Call this when the user wants to log in without copying a token.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="authenticate_via_erp_browser",
            description="Open ERP in your browser (Chrome, Edge, or Chromium); you log in and the MCP captures the token automatically. No copy/paste.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_week_logs",
            description="Get project logs for a specific week. Requires authentication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "week_starting": {
                        "type": "string",
                        "description": "Week starting date in YYYY-MM-DD format",
                        "format": "date"
                    },
                    "email": {
                        "type": "string",
                        "description": "Optional email address (uses current authenticated user if not provided)"
                    }
                },
                "required": ["week_starting"]
            }
        ),
        Tool(
            name="get_day_logs",
            description="Get detailed project logs for a specific day. Returns all projects, tasks, and time logged for that day. Requires authentication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format",
                        "format": "date"
                    },
                    "email": {
                        "type": "string",
                        "description": "Optional email address (uses current authenticated user if not provided)"
                    }
                },
                "required": ["date"]
            }
        ),
        Tool(
            name="get_logs_for_date_range",
            description="Get project logs for a date range. Requires authentication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format",
                        "format": "date"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format",
                        "format": "date"
                    },
                    "email": {
                        "type": "string",
                        "description": "Optional email address (uses current authenticated user if not provided)"
                    }
                },
                "required": ["start_date", "end_date"]
            }
        ),
        Tool(
            name="create_or_update_log",
            description="Add a time log for a date: specify date, project (by id or name), hours, description. Optionally set label (by id or name). Week log is used automatically when it exists; otherwise Slack endpoint is tried. Saving is done via backend Save API when possible.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD", "format": "date"},
                    "project_id": {"type": "integer", "description": "Project/subteam ID (from get_active_projects). Use this or project_name."},
                    "project_name": {"type": "string", "description": "Project/team name (e.g. 'Your Project Name'). Resolved to ID automatically."},
                    "description": {"type": "string", "description": "Description of work done"},
                    "hours": {"type": "number", "description": "Hours (decimal ok, e.g. 2 or 8.5)", "minimum": 0, "maximum": 24},
                    "label_id": {"type": "integer", "description": "Optional label ID (from get_log_labels). Use this or label_name."},
                    "label_name": {"type": "string", "description": "Optional label name (e.g. 'Coding'). Resolved to ID automatically."},
                    "email": {"type": "string", "description": "Optional; uses current user if omitted"}
                },
                "required": ["date", "description", "hours"],
                "anyOf": [{"required": ["project_id"]}, {"required": ["project_name"]}]
            }
        ),
        Tool(
            name="delete_log",
            description="Remove a time log entry for a date. Specify date, project (by id or name), and the exact task description. Requires authentication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format", "format": "date"},
                    "project_id": {"type": "integer", "description": "Project/subteam ID (from get_active_projects). Use this or project_name."},
                    "project_name": {"type": "string", "description": "Project/team name (e.g. 'Your Project Name'). Resolved to ID automatically."},
                    "description": {"type": "string", "description": "Exact task description of the log entry to remove"},
                    "email": {"type": "string", "description": "Optional; uses current user if omitted"}
                },
                "required": ["date", "description"],
                "anyOf": [{"required": ["project_id"]}, {"required": ["project_name"]}]
            }
        ),
        Tool(
            name="complete_week_log",
            description="Mark a week's project logs as completed. Requires authentication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "week_starting": {
                        "type": "string",
                        "description": "Week starting date in YYYY-MM-DD format",
                        "format": "date"
                    },
                    "email": {
                        "type": "string",
                        "description": "Optional email address (uses current authenticated user if not provided)"
                    },
                    "save_draft": {
                        "type": "boolean",
                        "description": "If true, saves as draft without completing. Default is false.",
                        "default": False
                    }
                },
                "required": ["week_starting"]
            }
        ),
        Tool(
            name="get_active_projects",
            description="Get list of active projects/subteams for a person. Requires authentication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Optional email address (uses current authenticated user if not provided)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="check_person_week_project_exists",
            description="Check if PersonWeekProject exists for a given date and project. PersonWeekProject is required before adding logs. It links PersonWeekLog (for the week) and PersonTeam (linking person to subteam). Returns what's needed if it doesn't exist. Requires authentication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format",
                        "format": "date"
                    },
                    "project_id": {
                        "type": ["integer", "string"],
                        "description": "Project/subteam ID (from active_projects) or project name"
                    },
                    "email": {
                        "type": "string",
                        "description": "Optional email address (uses current authenticated user if not provided)"
                    }
                },
                "required": ["date", "project_id"]
            }
        ),
        Tool(
            name="get_log_labels",
            description="Get available log labels for categorizing log entries. Requires authentication.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_month_logs",
            description="Get all project logs for a specific month and year. Requires authentication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "Year (e.g., 2024)"
                    },
                    "month": {
                        "type": "integer",
                        "description": "Month (1-12)"
                    },
                    "email": {
                        "type": "string",
                        "description": "Optional email address (uses current authenticated user if not provided)"
                    }
                },
                "required": ["year", "month"]
            }
        ),
        Tool(
            name="fill_logs_for_days",
            description="Fill logs for multiple days at once. Useful for bulk operations. Requires authentication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format",
                        "format": "date"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format",
                        "format": "date"
                    },
                    "project_id": {
                        "type": ["integer", "string"],
                        "description": "ID of the project/subteam, OR project/team name (e.g., 'Your Project Name'). If a name is provided, the MCP will automatically find the matching project ID."
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Optional: Project/team name to search for. If provided, project_id will be resolved from this name. If both project_id and project_name are provided, project_name takes precedence. Example: 'Your Project Name'"
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of the work done"
                    },
                    "email": {
                        "type": "string",
                        "description": "Optional email address (uses current authenticated user if not provided)"
                    },
                    "hours_per_day": {
                        "type": "number",
                        "description": "Hours to log per day (default: 8)",
                        "default": 8
                    },
                    "label_id": {
                        "type": "integer",
                        "description": "Optional label ID"
                    },
                    "skip_weekends": {
                        "type": "boolean",
                        "description": "Skip weekends (Saturday and Sunday). Default is false.",
                        "default": False
                    }
                },
                "required": ["start_date", "end_date", "project_id", "description"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name == "authenticate_with_token":
            result = await api_client.authenticate_with_token(
                token=arguments["token"],
                email=arguments.get("email")
            )
            return [TextContent(type="text", text=result)]

        if name == "authenticate_with_google":
            result = await api_client.authenticate_with_google(
                access_token=arguments["access_token"],
                email=arguments.get("email")
            )
            return [TextContent(type="text", text=result)]

        if name == "authenticate_via_browser":
            async def _exchange(access_token: str):
                raw = await api_client.authenticate_with_google(access_token=access_token, email=None)
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return {"status": "error", "message": raw or "Unknown error"}
            err = await start_oauth_server(_exchange)
            if err:
                return [TextContent(type="text", text=json.dumps({
                    "status": "error",
                    "message": f"Could not start local sign-in server: {err}"
                }, indent=2))]
            login_url, warning = get_login_url()
            msg = (
                "Open this URL in your browser to sign in with Google. "
                "After signing in you will be redirected back and can close the browser tab; then return to Cursor and use other tools.\n\n"
                f"**Sign-in URL:** {login_url}"
            )
            if warning:
                msg += f"\n\n**Note:** {warning}"
            return [TextContent(type="text", text=msg)]

        if name == "authenticate_via_erp_browser":
            def _store_erp_token(token: str, email: Optional[str]) -> None:
                auth_manager.set_current_token(token, email)

            result = await _authenticate_via_erp_browser(_store_erp_token)
            if result.get("status") == "success":
                return [TextContent(type="text", text=json.dumps({
                    "status": "success",
                    "message": "Signed in via ERP browser. Token captured; you can use other tools now.",
                    "email": result.get("email")
                }, indent=2))]
            if result.get("status") == "deployed":
                msg = (
                    result.get("message", "")
                    + "\n\n**Open this URL in your browser:** "
                    + result.get("url", "")
                )
                return [TextContent(type="text", text=json.dumps({
                    "status": "deployed",
                    "url": result.get("url"),
                    "message": msg,
                }, indent=2))]
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_week_logs":
            result = await api_client.get_week_logs(
                week_starting=arguments["week_starting"],
                email=arguments.get("email")
            )
            return [TextContent(type="text", text=result)]

        elif name == "get_day_logs":
            result = await api_client.get_day_logs(
                date=arguments["date"],
                email=arguments.get("email")
            )
            return [TextContent(type="text", text=result)]

        elif name == "get_logs_for_date_range":
            result = await api_client.get_logs_for_date_range(
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
                email=arguments.get("email")
            )
            return [TextContent(type="text", text=result)]

        elif name == "create_or_update_log":
            # Handle project_id or project_name
            project_id = arguments.get("project_id")
            project_name = arguments.get("project_name")
            
            # If project_name is provided, resolve project_id from it
            if project_name:
                active_projects_result = await api_client.get_active_projects(
                    email=arguments.get("email")
                )
                active_projects_data = json.loads(active_projects_result)
                if active_projects_data.get("status") == "success":
                    active_projects = active_projects_data.get("data", [])
                    # Search for project by name (case-insensitive, partial match)
                    project_name_lower = project_name.lower().strip()
                    found_project = None
                    for proj in active_projects:
                        if isinstance(proj, dict):
                            team_name = proj.get("team", "")
                            if team_name and project_name_lower in team_name.lower():
                                found_project = proj
                                break
                    
                    if found_project:
                        project_id = found_project.get("id")
                    else:
                        available_projects = [p.get("team", "") for p in active_projects if isinstance(p, dict)]
                        return [TextContent(type="text", text=json.dumps({
                            "status": "error",
                            "message": f"Project '{project_name}' not found in active projects. Available projects: {available_projects}"
                        }, indent=2))]
                else:
                    return [TextContent(type="text", text=active_projects_result)]
            
            # If project_id is still a string (could be a name that wasn't found, or a string ID)
            if project_id is None:
                return [TextContent(type="text", text=json.dumps({
                    "status": "error",
                    "message": "Either project_id or project_name must be provided."
                }, indent=2))]
            
            # Try to convert project_id to integer if it's a string
            if isinstance(project_id, str):
                # First check if it's a numeric string
                try:
                    project_id = int(project_id)
                except ValueError:
                    # If it's not numeric, treat it as a project name and search
                    active_projects_result = await api_client.get_active_projects(
                        email=arguments.get("email")
                    )
                    active_projects_data = json.loads(active_projects_result)
                    if active_projects_data.get("status") == "success":
                        active_projects = active_projects_data.get("data", [])
                        project_name_lower = project_id.lower().strip()
                        found_project = None
                        for proj in active_projects:
                            if isinstance(proj, dict):
                                team_name = proj.get("team", "")
                                if team_name and project_name_lower in team_name.lower():
                                    found_project = proj
                                    break
                        
                        if found_project:
                            project_id = found_project.get("id")
                        else:
                            available_projects = [p.get("team", "") for p in active_projects if isinstance(p, dict)]
                            return [TextContent(type="text", text=json.dumps({
                                "status": "error",
                                "message": f"Project '{project_id}' not found in active projects. Available projects: {available_projects}"
                            }, indent=2))]
                    else:
                        return [TextContent(type="text", text=active_projects_result)]
            
            label_id = arguments.get("label_id")
            label_name = arguments.get("label_name")
            if label_name and not label_id:
                labels_result = await api_client.get_log_labels(email=arguments.get("email"))
                try:
                    labels_data = json.loads(labels_result)
                    if labels_data.get("status") == "success":
                        labels = labels_data.get("data") or []
                        name_lower = str(label_name).strip().lower()
                        for lb in labels:
                            if isinstance(lb, dict) and name_lower == (lb.get("name") or "").strip().lower():
                                label_id = lb.get("id")
                                break
                except (json.JSONDecodeError, TypeError):
                    pass
            if label_id is not None and isinstance(label_id, str):
                try:
                    label_id = int(label_id)
                except ValueError:
                    return [TextContent(type="text", text=json.dumps({
                        "status": "error",
                        "message": f"Invalid label_id: {label_id}. Must be an integer."
                    }, indent=2))]
            
            # Ensure hours is a number
            hours = arguments["hours"]
            if isinstance(hours, str):
                try:
                    hours = float(hours)
                except ValueError:
                    return [TextContent(type="text", text=json.dumps({
                        "status": "error",
                        "message": f"Invalid hours: {hours}. Must be a number."
                    }, indent=2))]
            
            result = await api_client.create_or_update_log(
                date=arguments["date"],
                project_id=project_id,
                description=arguments["description"],
                hours=hours,
                email=arguments.get("email"),
                label_id=label_id
            )
            return [TextContent(type="text", text=result)]

        elif name == "delete_log":
            project_id = arguments.get("project_id")
            project_name = arguments.get("project_name")
            if project_name:
                active_projects_result = await api_client.get_active_projects(
                    email=arguments.get("email")
                )
                active_projects_data = json.loads(active_projects_result)
                if active_projects_data.get("status") == "success":
                    active_projects = active_projects_data.get("data", [])
                    project_name_lower = project_name.lower().strip()
                    found_project = None
                    for proj in active_projects:
                        if isinstance(proj, dict):
                            team_name = proj.get("team", "")
                            if team_name and project_name_lower in team_name.lower():
                                found_project = proj
                                break
                    if found_project:
                        project_id = found_project.get("id")
                    else:
                        available_projects = [p.get("team", "") for p in active_projects if isinstance(p, dict)]
                        return [TextContent(type="text", text=json.dumps({
                            "status": "error",
                            "message": f"Project '{project_name}' not found. Available: {available_projects}"
                        }, indent=2))]
                else:
                    return [TextContent(type="text", text=active_projects_result)]
            if project_id is None:
                return [TextContent(type="text", text=json.dumps({
                    "status": "error",
                    "message": "Either project_id or project_name must be provided."
                }, indent=2))]
            if isinstance(project_id, str):
                try:
                    project_id = int(project_id)
                except ValueError:
                    return [TextContent(type="text", text=json.dumps({
                        "status": "error",
                        "message": f"Invalid project_id: {project_id}. Must be an integer or project name."
                    }, indent=2))]
            result = await api_client.delete_log(
                date=arguments["date"],
                project_id=project_id,
                description=arguments["description"],
                email=arguments.get("email")
            )
            return [TextContent(type="text", text=result)]

        elif name == "complete_week_log":
            result = await api_client.complete_week_log(
                week_starting=arguments["week_starting"],
                email=arguments.get("email"),
                save_draft=arguments.get("save_draft", False)
            )
            return [TextContent(type="text", text=result)]

        elif name == "get_active_projects":
            result = await api_client.get_active_projects(
                email=arguments.get("email")
            )
            return [TextContent(type="text", text=result)]

        elif name == "check_person_week_project_exists":
            # Handle project_id or project_name
            project_id = arguments.get("project_id")
            project_name = arguments.get("project_name")
            
            # If project_name is provided or project_id is a string, resolve project_id
            if project_name or (project_id and isinstance(project_id, str) and not project_id.isdigit()):
                active_projects_result = await api_client.get_active_projects(
                    email=arguments.get("email")
                )
                active_projects_data = json.loads(active_projects_result)
                if active_projects_data.get("status") == "success":
                    active_projects = active_projects_data.get("data", [])
                    search_name = project_name or project_id
                    project_name_lower = search_name.lower().strip()
                    found_project = None
                    for proj in active_projects:
                        if isinstance(proj, dict):
                            team_name = proj.get("team", "")
                            if team_name and project_name_lower in team_name.lower():
                                found_project = proj
                                break
                    
                    if found_project:
                        project_id = found_project.get("id")
                    else:
                        available_projects = [p.get("team", "") for p in active_projects if isinstance(p, dict)]
                        return [TextContent(type="text", text=json.dumps({
                            "status": "error",
                            "message": f"Project '{search_name}' not found in active projects. Available projects: {available_projects}"
                        }, indent=2))]
                else:
                    return [TextContent(type="text", text=active_projects_result)]
            
            # Ensure project_id is an integer
            if isinstance(project_id, str):
                try:
                    project_id = int(project_id)
                except ValueError:
                    return [TextContent(type="text", text=json.dumps({
                        "status": "error",
                        "message": f"Invalid project_id: {project_id}. Must be an integer or valid project name."
                    }, indent=2))]
            
            result = await api_client.check_person_week_project_exists(
                date=arguments["date"],
                project_id=project_id,
                email=arguments.get("email")
            )
            return [TextContent(type="text", text=result)]

        elif name == "get_log_labels":
            result = await api_client.get_log_labels(
                email=arguments.get("email")
            )
            return [TextContent(type="text", text=result)]

        elif name == "get_month_logs":
            # Ensure year and month are integers
            year = arguments["year"]
            month = arguments["month"]
            if isinstance(year, str):
                try:
                    year = int(year)
                except ValueError:
                    return [TextContent(type="text", text=json.dumps({
                        "status": "error",
                        "message": f"Invalid year: {year}. Must be an integer."
                    }, indent=2))]
            if isinstance(month, str):
                try:
                    month = int(month)
                except ValueError:
                    return [TextContent(type="text", text=json.dumps({
                        "status": "error",
                        "message": f"Invalid month: {month}. Must be an integer."
                    }, indent=2))]
            
            result = await api_client.get_month_logs(
                year=year,
                month=month,
                email=arguments.get("email")
            )
            return [TextContent(type="text", text=result)]

        elif name == "fill_logs_for_days":
            # Ensure project_id and label_id are integers
            project_id = arguments["project_id"]
            if isinstance(project_id, str):
                try:
                    project_id = int(project_id)
                except ValueError:
                    return [TextContent(type="text", text=json.dumps({
                        "status": "error",
                        "message": f"Invalid project_id: {project_id}. Must be an integer."
                    }, indent=2))]
            
            label_id = arguments.get("label_id")
            if label_id is not None and isinstance(label_id, str):
                try:
                    label_id = int(label_id)
                except ValueError:
                    return [TextContent(type="text", text=json.dumps({
                        "status": "error",
                        "message": f"Invalid label_id: {label_id}. Must be an integer."
                    }, indent=2))]
            
            # Ensure hours_per_day is a number
            hours_per_day = arguments.get("hours_per_day", 8)
            if isinstance(hours_per_day, str):
                try:
                    hours_per_day = float(hours_per_day)
                except ValueError:
                    return [TextContent(type="text", text=json.dumps({
                        "status": "error",
                        "message": f"Invalid hours_per_day: {hours_per_day}. Must be a number."
                    }, indent=2))]
            
            result = await api_client.fill_logs_for_days(
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
                project_id=project_id,
                description=arguments["description"],
                email=arguments.get("email"),
                hours_per_day=hours_per_day,
                label_id=label_id,
                skip_weekends=arguments.get("skip_weekends", False)
            )
            return [TextContent(type="text", text=result)]

        else:
            return [TextContent(
                type="text",
                text=f"Unknown tool: {name}"
            )]

    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        error_msg = f"Error executing tool {name}: {str(e)}\n\nTraceback:\n{error_traceback}"
        return [TextContent(type="text", text=error_msg)]


async def main():
    """Run the MCP server."""
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options()
            )
    except KeyboardInterrupt:
        # Gracefully handle Ctrl+C
        pass
    except Exception as e:
        # Log any unexpected errors but don't crash
        # The MCP SDK should handle JSON parsing errors, but just in case
        import sys
        print(f"Server error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # Check if running in interactive mode (not recommended)
    if sys.stdin.isatty():
        print(
            "Warning: MCP server should be run by an MCP client, not directly in terminal.\n"
            "If you're testing, use an MCP client like Claude Desktop.\n"
            "Do not type in this terminal - all input is treated as JSON-RPC messages.\n",
            file=sys.stderr
        )
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        pass
