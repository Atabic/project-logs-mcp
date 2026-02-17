"""
API Client for Project Logs.

Handles all HTTP requests to the ERP production API.
"""

import json
import os
import asyncio
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Any
import aiohttp


class ProjectLogsAPIClient:
    """Client for interacting with the ERP Project Logs API."""

    def __init__(self, base_url: str, auth_manager):
        """
        Initialize the API client.
        
        Args:
            base_url: Base URL of the ERP API (e.g., https://your-erp.example.com/api/v1/)
            auth_manager: AuthenticationManager instance
        """
        self.base_url = base_url.rstrip("/")
        self.auth_manager = auth_manager
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self.session is None or self.session.closed:
            # Create session with SSL verification
            # For production, verify SSL certificates properly
            import ssl
            import certifi
            
            # Try to use certifi for certificate bundle, fallback to default
            try:
                ssl_context = ssl.create_default_context(cafile=certifi.where())
            except:
                ssl_context = ssl.create_default_context()
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self.session

    async def _close_session(self):
        """Close HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()

    def _get_headers(self, email: Optional[str] = None) -> Dict[str, str]:
        """
        Get HTTP headers with authentication.
        
        Args:
            email: Optional user identifier. If not provided, uses current token.
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # Get token (prefer current token, fallback to user-specific)
        token = self.auth_manager.get_token(email) if email else self.auth_manager.get_token()
        
        if token:
            # Token should already be the value (not "Token xyz"), but handle both cases
            if token.startswith("Token ") or token.startswith("Bearer "):
                headers["Authorization"] = token
            else:
                headers["Authorization"] = f"Token {token}"
        
        # Also try to get session cookies
        session_data = self.auth_manager.get_session(email) if email else self.auth_manager.get_session()
        if session_data and "cookies" in session_data:
            headers["Cookie"] = session_data["cookies"]
        
        return headers

    async def authenticate_with_token(self, token: str, email: Optional[str] = None) -> str:
        """
        Authenticate with ERP API using an Authorization token.
        
        Args:
            token: Authorization token (can be "Token xyz" or just "xyz")
            email: Optional email for identification (for authorization check)
            
        Returns:
            JSON string with authentication result
        """
        # Check authorization if email is provided
        if email and not self.auth_manager.is_authorized(email):
            return json.dumps({
                "status": "error",
                "message": f"Email {email} is not authorized to use this MCP server. Contact administrator."
            }, indent=2)

        # Store the token
        self.auth_manager.set_current_token(token, email)
        
        # Optionally verify the token by making a test API call
        try:
            session = await self._get_session()
            # Test token by calling a simple endpoint
            test_url = f"{self.base_url}/core/person/"
            headers = self._get_headers()
            
            async with session.get(test_url, headers=headers) as response:
                if response.status == 200 or response.status == 401:
                    # 200 = valid, 401 = invalid (but we tried)
                    if response.status == 200:
                        return json.dumps({
                            "status": "success",
                            "message": "Token authenticated successfully",
                            "email": email or "authenticated user"
                        }, indent=2)
                    else:
                        return json.dumps({
                            "status": "error",
                            "message": "Token appears to be invalid or expired. Please check your token."
                        }, indent=2)
                else:
                    # Other status codes - assume token is valid
                    return json.dumps({
                        "status": "success",
                        "message": "Token stored successfully",
                        "email": email or "authenticated user",
                        "note": "Token validation returned status " + str(response.status)
                    }, indent=2)
        except Exception as e:
            # If validation fails, still store the token (user might have network issues)
            return json.dumps({
                "status": "success",
                "message": "Token stored successfully (validation skipped due to error)",
                "email": email or "authenticated user",
                "warning": f"Could not validate token: {str(e)}"
            }, indent=2)

    async def authenticate(self, email: str, password: str) -> str:
        """
        Authenticate with the ERP API using email and password (legacy method).
        
        Args:
            email: User email
            password: User password
            
        Returns:
            JSON string with authentication result
        """
        # Check authorization first
        if not self.auth_manager.is_authorized(email):
            return json.dumps({
                "status": "error",
                "message": f"Email {email} is not authorized to use this MCP server. Contact administrator."
            }, indent=2)

        try:
            session = await self._get_session()
            login_url = f"{self.base_url}/core/login/"
            
            async with session.post(
                login_url,
                json={"email": email, "password": password},
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Extract token if available
                    token = None
                    if "token" in data:
                        token = data["token"]
                    elif "auth_token" in data:
                        token = data["auth_token"]
                    
                    # Extract session cookies
                    cookies = response.headers.get("Set-Cookie", "")
                    if not cookies:
                        # Try to get cookies from response
                        cookie_header = "; ".join([f"{k}={v}" for k, v in response.cookies.items()])
                        if cookie_header:
                            cookies = cookie_header
                    
                    # Store authentication
                    if token:
                        self.auth_manager.set_token(email, token)
                    
                    if cookies:
                        self.auth_manager.set_session(email, {"cookies": cookies})
                    
                    return json.dumps({
                        "status": "success",
                        "message": "Authentication successful",
                        "email": email
                    }, indent=2)
                else:
                    error_text = await response.text()
                    return json.dumps({
                        "status": "error",
                        "message": f"Authentication failed: {error_text}",
                        "status_code": response.status
                    }, indent=2)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "message": f"Authentication error: {str(e)}"
            }, indent=2)

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        email: Optional[str] = None,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Make an authenticated API request.
        
        Args:
            method: HTTP method (GET, POST, PATCH, etc.)
            endpoint: API endpoint (relative to base_url)
            email: Optional user email for authentication (uses current token if not provided)
            data: Request body data
            params: Query parameters
            
        Returns:
            Response data as dictionary
        """
        # Check if user is authenticated
        token = self.auth_manager.get_token(email) if email else self.auth_manager.get_token()
        session_data = self.auth_manager.get_session(email) if email else self.auth_manager.get_session()
        
        if not token and not session_data:
            return {
                "status": "error",
                "message": "Not authenticated. Please call authenticate_with_token tool first with your Authorization token."
            }

        try:
            session = await self._get_session()
            url = f"{self.base_url}/{endpoint.lstrip('/')}"
            headers = self._get_headers(email)
            
            async with session.request(
                method,
                url,
                json=data,
                params=params,
                headers=headers
            ) as response:
                # Handle response data
                if response.content_type and "application/json" in response.content_type:
                    try:
                        response_data = await response.json()
                    except Exception:
                        # If JSON parsing fails, get text
                        response_text = await response.text()
                        try:
                            response_data = json.loads(response_text)
                        except json.JSONDecodeError:
                            response_data = {"text": response_text}
                else:
                    response_text = await response.text()
                    try:
                        response_data = json.loads(response_text)
                    except json.JSONDecodeError:
                        response_data = {"text": response_text}
                
                if response.status >= 400:
                    error_msg = "Unknown error"
                    if isinstance(response_data, dict):
                        error_msg = response_data.get("error") or response_data.get("detail") or f"API error: {response.status}"
                    elif isinstance(response_data, str):
                        error_msg = response_data
                    return {
                        "status": "error",
                        "message": error_msg,
                        "status_code": response.status,
                        "data": response_data
                    }
                
                return {
                    "status": "success",
                    "data": response_data
                }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }

    async def _request_no_auth(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make an API request without sending Authorization (e.g. for login endpoints)."""
        try:
            session = await self._get_session()
            url = f"{self.base_url}/{endpoint.lstrip('/')}"
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            async with session.request(
                method, url, json=data, params=params, headers=headers
            ) as response:
                if response.content_type and "application/json" in response.content_type:
                    try:
                        response_data = await response.json()
                    except Exception:
                        response_text = await response.text()
                        try:
                            response_data = json.loads(response_text)
                        except json.JSONDecodeError:
                            response_data = {"text": response_text}
                else:
                    response_text = await response.text()
                    try:
                        response_data = json.loads(response_text)
                    except json.JSONDecodeError:
                        response_data = {"text": response_text}
                if response.status >= 400:
                    error_msg = "Unknown error"
                    if isinstance(response_data, dict):
                        error_msg = response_data.get("error") or response_data.get("detail") or str(response_data)
                    return {
                        "status": "error",
                        "message": error_msg,
                        "status_code": response.status,
                        "data": response_data
                    }
                return {"status": "success", "data": response_data}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def authenticate_with_google(self, access_token: str, email: Optional[str] = None) -> str:
        """
        Authenticate with ERP using a Google OAuth access token.
        Calls the existing Django API POST core/google-login/ which validates the token
        with Google, finds the user by email, and returns an ERP token.
        """
        if not access_token or not access_token.strip():
            return json.dumps({
                "status": "error",
                "message": "Google access_token is required."
            }, indent=2)
        access_token = access_token.strip()
        result = await self._request_no_auth(
            "POST",
            "core/google-login/",
            data={"platform": "google", "access_token": access_token}
        )
        if result["status"] != "success":
            return json.dumps(result, indent=2)
        data = result.get("data", {})
        erp_token = data.get("token")
        user_email = data.get("email") or email
        if not erp_token:
            return json.dumps({
                "status": "error",
                "message": "Backend did not return a token."
            }, indent=2)
        if user_email and self.auth_manager.authorized_emails and not self.auth_manager.is_authorized(user_email):
            return json.dumps({
                "status": "error",
                "message": f"Email {user_email} is not authorized to use this MCP server."
            }, indent=2)
        self.auth_manager.set_current_token(erp_token, user_email)
        return json.dumps({
            "status": "success",
            "message": "Signed in with Google and linked to ERP.",
            "email": user_email,
            "name": data.get("name")
        }, indent=2)

    async def get_week_logs(self, week_starting: str, email: Optional[str] = None) -> str:
        """Get project logs for a specific week."""
        # First, get the week log ID from person/list endpoint
        year = datetime.strptime(week_starting, "%Y-%m-%d").year
        # Ensure year is an integer (not string) as Django expects
        result = await self._make_request(
            "GET",
            "project-logs/person/list/",
            email,
            params={"year": int(year)}
        )
        
        if result["status"] != "success":
            return json.dumps(result, indent=2)
        
        # Find the specific week log ID
        # The person/list endpoint returns a list of week logs with their IDs
        week_log_id = None
        week_starting_date = datetime.strptime(week_starting, "%Y-%m-%d").date()
        week_starting_str = week_starting_date.strftime("%Y-%m-%d")
        
        data = result.get("data", [])
        if isinstance(data, list):
            for log in data:
                # Check if this log matches the week_starting
                # The API might return week_starting in different formats
                log_week_start = log.get("week_starting")
                if log_week_start:
                    # Try to match the date
                    try:
                        if isinstance(log_week_start, str):
                            # Try parsing as date string
                            if log_week_start == week_starting_str:
                                week_log_id = log.get("id")
                                break
                            # Also try parsing "Mon, Dec 29" format
                            if ", " in log_week_start:
                                parts = log_week_start.split(", ")
                                if len(parts) == 2:
                                    month_day = parts[1].split()
                                    if len(month_day) == 2:
                                        month_name = month_day[0]
                                        day = int(month_day[1])
                                        month_map = {
                                            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                                            "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
                                        }
                                        month_num = month_map.get(month_name)
                                        if month_num and datetime(year, month_num, day).date() == week_starting_date:
                                            week_log_id = log.get("id")
                                            break
                    except (ValueError, KeyError, IndexError):
                        continue
        
        if not week_log_id:
            return json.dumps({
                "status": "error",
                "message": f"Week log not found for week starting {week_starting}"
            }, indent=2)
        
        # Get the detailed week log using the ID in the URL path
        result = await self._make_request(
            "GET",
            f"project-logs/person/get/{week_log_id}/",
            email
        )
        
        return json.dumps(result, indent=2)

    async def get_day_logs(self, date: str, email: Optional[str] = None) -> str:
        """
        Get detailed project logs for a specific day.
        
        Args:
            date: Date in YYYY-MM-DD format
            email: Optional email address (uses current authenticated user if not provided)
            
        Returns:
            JSON string with day logs including all projects, tasks, and details for that day
        """
        # Calculate week starting date (Monday is start of week)
        log_date = datetime.strptime(date, "%Y-%m-%d").date()
        days_since_monday = log_date.weekday()  # 0 = Monday, 6 = Sunday
        week_starting = log_date - timedelta(days=days_since_monday)
        week_starting_str = week_starting.strftime("%Y-%m-%d")
        
        # Get the week log details
        result = await self.get_week_logs(week_starting_str, email)
        week_log_data = json.loads(result)
        
        if week_log_data.get("status") != "success":
            return json.dumps(week_log_data, indent=2)
        
        # Extract day-specific logs from the week log
        day_logs = {
            "date": date,
            "week_starting": week_starting_str,
            "projects": []
        }
        
        week_data = week_log_data.get("data", {})
        projects = week_data.get("projects", [])
        
        total_hours = 0
        total_minutes = 0
        
        for project in projects:
            project_id = project.get("id")
            project_name = project.get("subteam") or project.get("team", "Unknown Project")
            team_name = project.get("team", "")
            
            # Filter tasks that have logs for this specific day
            day_tasks = []
            for task in project.get("tasks", []):
                task_days = task.get("days", [])
                day_detail = None
                
                # Find the day detail for our target date
                for day in task_days:
                    if day.get("date") == date:
                        day_detail = day
                        break
                
                # If this task has logs for this day, include it
                if day_detail:
                    task_info = {
                        "id": task.get("id"),
                        "description": task.get("description", ""),
                        "hours": day_detail.get("hours", 0),
                        "minutes": day_detail.get("minutes", 0),
                        "decimal_hours": float(day_detail.get("decimal_hours", 0)),
                        "label_id": day_detail.get("label"),
                        "label_option": day_detail.get("label_option")
                    }
                    day_tasks.append(task_info)
                    
                    # Accumulate totals
                    total_hours += task_info["hours"]
                    total_minutes += task_info["minutes"]
            
            # Only include project if it has tasks for this day
            if day_tasks:
                project_info = {
                    "project_id": project_id,
                    "project_name": project_name,
                    "team_name": team_name,
                    "tasks": day_tasks,
                    "total_hours": sum(t["hours"] for t in day_tasks),
                    "total_minutes": sum(t["minutes"] for t in day_tasks),
                    "total_decimal_hours": sum(t["decimal_hours"] for t in day_tasks)
                }
                day_logs["projects"].append(project_info)
        
        # Convert minutes to hours if needed
        total_hours += total_minutes // 60
        total_minutes = total_minutes % 60
        
        day_logs["total_logged_time"] = {
            "hours": total_hours,
            "minutes": total_minutes,
            "decimal_hours": round(sum(
                p["total_decimal_hours"] for p in day_logs["projects"]
            ), 2)
        }
        
        day_logs["total_projects"] = len(day_logs["projects"])
        day_logs["total_tasks"] = sum(len(p["tasks"]) for p in day_logs["projects"])
        
        # Add week log metadata
        day_logs["week_log"] = {
            "id": week_data.get("id"),
            "person_id": week_data.get("person_id"),
            "person_name": week_data.get("person_name"),
            "is_completed": week_data.get("is_completed", False),
            "week_starting": week_data.get("week_starting"),
            "week_ending": week_data.get("week_ending")
        }
        
        return json.dumps({
            "status": "success",
            "data": day_logs
        }, indent=2)

    def _extract_log_list_from_response(self, result: Dict[str, Any]) -> tuple[list, Optional[str]]:
        """
        Extract list of log dicts from month-list (or similar) API result.
        Returns (list_of_logs, error_message). error_message is set if status is not success.
        """
        if result.get("status") != "success":
            msg = result.get("message") or result.get("detail") or "API request failed"
            if "data" in result and isinstance(result["data"], dict):
                detail = result["data"].get("detail") or result["data"].get("error")
                if detail:
                    msg = str(detail)
            return [], msg

        data = result.get("data")
        if data is None:
            return [], None

        out = []
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
            # Single key that is a list (e.g. some wrappers)
            for val in data.values():
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    for item in val:
                        if isinstance(item, dict):
                            out.append(item)
                    return out, None
        if isinstance(data, str):
            try:
                parsed = json.loads(data)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict):
                            out.append(item)
                    return out, None
                if isinstance(parsed, dict) and "data" in parsed and isinstance(parsed["data"], list):
                    for item in parsed["data"]:
                        if isinstance(item, dict):
                            out.append(item)
                    return out, None
            except json.JSONDecodeError:
                pass
        return [], None

    def _parse_week_starting_to_date(self, week_start_str: str, log_year: int) -> Optional[date]:
        """Parse week_starting from API (e.g. 'Mon, Jan 27' or '2026-01-27') to date."""
        if not week_start_str or not isinstance(week_start_str, str):
            return None
        try:
            return datetime.strptime(week_start_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
        try:
            parts = week_start_str.split(", ")
            if len(parts) == 2:
                month_day = parts[1].split()
                if len(month_day) == 2:
                    month_name = month_day[0]
                    day = int(month_day[1])
                    month_map = {
                        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
                    }
                    month_num = month_map.get(month_name)
                    if month_num:
                        return datetime(log_year, month_num, day).date()
        except (ValueError, KeyError, IndexError):
            pass
        return None

    async def get_logs_for_date_range(
        self,
        start_date: str,
        end_date: str,
        email: Optional[str] = None
    ) -> str:
        """Get project logs for a date range."""
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        start_date_obj = start.date()
        end_date_obj = end.date()

        all_logs = []
        last_error = None
        current_date = start

        while current_date <= end:
            year = current_date.year
            month = current_date.month
            result = await self._make_request(
                "GET",
                "project-logs/person/month-list/",
                email,
                params={"year": int(year), "month": int(month)}
            )
            items, err = self._extract_log_list_from_response(result)
            if err:
                last_error = err
            else:
                all_logs.extend(items)
            if month == 12:
                current_date = datetime(year + 1, 1, 1)
            else:
                current_date = datetime(year, month + 1, 1)

        # If every month failed, return the error instead of empty data
        if not all_logs and last_error:
            return json.dumps({
                "status": "error",
                "message": f"Could not load logs for date range: {last_error}",
                "start_date": start_date,
                "end_date": end_date
            }, indent=2)

        # Filter by date range (week must overlap [start_date, end_date])
        filtered_logs = []
        for log in all_logs:
            if not isinstance(log, dict):
                continue
            log_year = log.get("year", start.year)
            if isinstance(log_year, str):
                try:
                    log_year = int(log_year)
                except ValueError:
                    log_year = start.year
            week_start_str = log.get("week_starting", "")
            week_start_date = self._parse_week_starting_to_date(week_start_str, log_year)
            if week_start_date is None:
                continue
            week_end_date = week_start_date + timedelta(days=6)
            if week_start_date <= end_date_obj and week_end_date >= start_date_obj:
                filtered_logs.append(log)

        return json.dumps({
            "status": "success",
            "data": filtered_logs,
            "count": len(filtered_logs),
            "start_date": start_date,
            "end_date": end_date
        }, indent=2)

    async def create_or_update_log(
        self,
        date: str,
        project_id: int,
        description: str,
        hours: float,
        email: Optional[str] = None,
        label_id: Optional[int] = None
    ) -> str:
        """
        Create or update a project log entry.
        
        This method first tries to use SlackAppProjectLogSaveView which creates logs directly.
        If that fails because PersonWeekProject doesn't exist, it falls back to the
        traditional method that requires the week log to exist.
        """
        try:
            # Ensure project_id is an integer (nsubteam_id from active_projects)
            try:
                project_id = int(project_id)
            except (ValueError, TypeError) as e:
                return json.dumps({
                    "status": "error",
                    "message": f"Invalid project_id: {project_id}. Must be an integer. Error: {str(e)}"
                }, indent=2)
            
            if label_id is not None:
                try:
                    label_id = int(label_id)
                except (ValueError, TypeError) as e:
                    return json.dumps({
                        "status": "error",
                        "message": f"Invalid label_id: {label_id}. Must be an integer. Error: {str(e)}"
                    }, indent=2)
            
            # Convert hours to time format (HH:MM)
            try:
                hours_float = float(hours)
            except (ValueError, TypeError):
                return json.dumps({
                    "status": "error",
                    "message": f"Invalid hours value: {hours}. Must be a number."
                }, indent=2)
            
            hours_int = int(hours_float)
            minutes_int = int((hours_float - hours_int) * 60)
            time_str = f"{hours_int:02d}:{minutes_int:02d}"
            
            # Get active projects to find the nsubteam_id
            # The active_projects endpoint returns id which is nsubteam.id (not person_team.id)
            active_projects_result = await self._make_request(
                "GET",
                "project-logs/person/active_project_list/",
                email
            )
            
            if active_projects_result["status"] != "success":
                return json.dumps(active_projects_result, indent=2)
            
            active_projects = active_projects_result.get("data", [])
            
            # Find the project in active projects (id = nsubteam_id, team = complete_name)
            nsubteam_id = None
            active_project_team_name = None
            for proj in active_projects:
                if isinstance(proj, dict):
                    proj_id = proj.get("id")
                    if proj_id is not None:
                        try:
                            if int(proj_id) == project_id:
                                nsubteam_id = proj_id
                                active_project_team_name = proj.get("team") or proj.get("subteam") or ""
                                break
                        except (ValueError, TypeError):
                            continue
            
            if nsubteam_id is None:
                return json.dumps({
                    "status": "error",
                    "message": f"Project {project_id} not found in active projects. Please verify the project ID."
                }, indent=2)
            
            nsubteam_id_int = int(nsubteam_id)
            effective_label_id = int(label_id) if label_id is not None else 66
            
            # Prefer Save API when week log exists (same as frontend). Get week log first.
            log_date = datetime.strptime(date, "%Y-%m-%d").date()
            days_since_monday = log_date.weekday()
            week_starting = log_date - timedelta(days=days_since_monday)
            week_starting_str = week_starting.strftime("%Y-%m-%d")
            year = int(week_starting.year)
            
            result = await self._make_request(
                "GET",
                "project-logs/person/list/",
                email,
                params={"year": year}
            )
            
            if not isinstance(result, dict):
                return json.dumps({
                    "status": "error",
                    "message": f"Unexpected response format from API. Expected dict, got {type(result).__name__}."
                }, indent=2)
            
            week_log_id = None
            if result.get("status") == "success":
                data = result.get("data", {})
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        pass
                if isinstance(data, dict) and "person_week_logs" in data:
                    all_logs = []
                    for month_data in data.get("person_week_logs", []):
                        if isinstance(month_data, dict) and "months_log" in month_data:
                            month_logs = month_data.get("months_log", [])
                            if isinstance(month_logs, list):
                                all_logs.extend(month_logs)
                    data = all_logs
                
                def _find_week_log_id(data_item, target_week):
                    if isinstance(data_item, dict):
                        week_start = data_item.get("week_starting", "")
                        if week_start == target_week:
                            wid = data_item.get("id")
                            if wid is not None:
                                try:
                                    return int(wid)
                                except (ValueError, TypeError):
                                    return wid
                        if ", " in str(week_start):
                            parts = str(week_start).split(", ")
                            if len(parts) == 2:
                                month_day = parts[1].split()
                                if len(month_day) == 2:
                                    month_map = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                                                 "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
                                    try:
                                        day = int(month_day[1])
                                        month_num = month_map.get(month_day[0])
                                        if month_num and len(target_week) >= 4:
                                            y = int(target_week[:4])
                                            parsed = datetime(y, month_num, day).date()
                                            if parsed.strftime("%Y-%m-%d") == target_week:
                                                wid = data_item.get("id")
                                                if wid is not None:
                                                    return int(wid) if isinstance(wid, int) else int(wid)
                                    except (ValueError, TypeError, KeyError):
                                        pass
                        for v in data_item.values():
                            found = _find_week_log_id(v, target_week)
                            if found is not None:
                                return found
                    elif isinstance(data_item, list):
                        for item in data_item:
                            found = _find_week_log_id(item, target_week)
                            if found is not None:
                                return found
                    return None
                
                week_log_id = _find_week_log_id(data, week_starting_str)
            
            if week_log_id is not None:
                try:
                    week_log_id = int(week_log_id)
                except (ValueError, TypeError):
                    week_log_id = None
            
            # Path 1: Week log exists -> use Save API (PATCH person-week-log/save/<id>/)
            if week_log_id and week_log_id != 0:
                get_result = await self._make_request(
                    "GET",
                    f"project-logs/person/get/{week_log_id}/",
                    email
                )
                if get_result.get("status") != "success":
                    return json.dumps(get_result, indent=2)
                
                week_log_data = get_result.get("data", {})
                if "modified_at" not in week_log_data:
                    return json.dumps({
                        "status": "error",
                        "message": "Week log data missing modified_at (required for save)."
                    }, indent=2)
                
                # Match project by team name (week log has "team" = complete_name, "subteam" = name)
                project_data = None
                for project in week_log_data.get("projects", []):
                    pteam = (project.get("team") or "").strip()
                    psub = (project.get("subteam") or "").strip()
                    if active_project_team_name and (pteam == active_project_team_name or psub == active_project_team_name or active_project_team_name in pteam or active_project_team_name in psub):
                        project_data = project
                        break
                
                if not project_data:
                    return json.dumps({
                        "status": "error",
                        "message": f"Project not found in week log for week starting {week_starting_str}. "
                                  f"Add at least one log for this project in that week via the web interface first."
                    }, indent=2)
                
                # Find or create task with description
                task_data = None
                for task in project_data.get("tasks", []):
                    if task.get("description") == description:
                        task_data = task
                        break
                
                hours_int = int(hours_float)
                minutes_int_min = int((hours_float - hours_int) * 60)
                
                if task_data:
                    day_detail = None
                    for day in task_data.get("days", []):
                        if day.get("date") == date:
                            day_detail = day
                            break
                    if day_detail:
                        day_detail["hours"] = hours_int
                        day_detail["minutes"] = minutes_int_min
                        day_detail["decimal_hours"] = round(float(hours), 2)
                        if effective_label_id:
                            day_detail["label"] = effective_label_id
                    else:
                        new_day = {
                            "date": date,
                            "hours": hours_int,
                            "minutes": minutes_int_min,
                            "decimal_hours": round(float(hours), 2)
                        }
                        if effective_label_id:
                            new_day["label"] = effective_label_id
                        task_data.setdefault("days", []).append(new_day)
                else:
                    new_task = {
                        "description": description,
                        "days": [{
                            "date": date,
                            "hours": hours_int,
                            "minutes": minutes_int_min,
                            "decimal_hours": round(float(hours), 2)
                        }]
                    }
                    if effective_label_id:
                        new_task["days"][0]["label"] = effective_label_id
                    project_data.setdefault("tasks", []).append(new_task)
                
                save_result = await self._make_request(
                    "PATCH",
                    f"project-logs/person/person-week-log/save/{week_log_id}/",
                    email,
                    data=week_log_data
                )
                return json.dumps(save_result, indent=2)
            
            # Path 2: No week log in person/list -> try Slack endpoint.
            # Backend SlackAppProjectLogSaveView does NOT create PersonWeekLog or PersonWeekProject;
            # it only creates ProjectTask/ProjectTaskDayDetail when PersonWeekProject already exists
            # (same requirement as Save API). So this try only succeeds if week log was created
            # elsewhere (e.g. team assignment or a previous web entry).
            log_payload = {
                "email": email or self.auth_manager.current_user,
                "logs": [{
                    "date": date,
                    "time_spent": time_str,
                    "description": description,
                    "subteam": nsubteam_id_int,
                    "label_id": effective_label_id
                }]
            }
            slack_result = await self._make_request(
                "POST",
                "project-logs/person/person-week-log-from-slack/",
                email,
                data=log_payload
            )
            if isinstance(slack_result, dict) and (slack_result.get("status") == "success" or slack_result.get("status_code") == 201):
                return json.dumps({
                    "status": "success",
                    "message": f"Log entry added for {date}.",
                    "data": slack_result.get("data", {})
                }, indent=2)
            # Slack returns 400 UNAUTHORIZED_PROJECT_ACCESS when PersonWeekProject is missing (no auto-creation)
            return json.dumps({
                "status": "error",
                "message": (
                    f"Week log for week starting {week_starting_str} does not exist. "
                    "The backend does not auto-create week logs (neither for the web Save API nor for the Slack endpoint). "
                    "Create the week log once by logging time for that week in the web interface, then you can add more entries here."
                )
            }, indent=2)
        except ValueError as e:
            return json.dumps({
                "status": "error",
                "message": f"Value error: {str(e)}. Please check that all parameters are of the correct type."
            }, indent=2)
        except TypeError as e:
            return json.dumps({
                "status": "error",
                "message": f"Type error: {str(e)}. This usually means a parameter has the wrong type. Please check project_id and label_id are integers."
            }, indent=2)
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            return json.dumps({
                "status": "error",
                "message": f"Unexpected error: {str(e)}. Please check the date format (YYYY-MM-DD) and that all required parameters are provided.",
                "traceback": error_traceback
            }, indent=2)

    async def delete_log(
        self,
        date: str,
        project_id: int,
        description: str,
        email: Optional[str] = None
    ) -> str:
        """
        Remove a project log entry by re-saving the week log without that day.
        Uses GET person/get/<id>/ then PATCH person-week-log/save/<id>/ with the day removed.
        """
        try:
            project_id = int(project_id)
        except (ValueError, TypeError):
            return json.dumps({
                "status": "error",
                "message": f"Invalid project_id: {project_id}. Must be an integer."
            }, indent=2)

        log_date = datetime.strptime(date, "%Y-%m-%d").date()
        days_since_monday = log_date.weekday()
        week_starting = log_date - timedelta(days=days_since_monday)
        week_starting_str = week_starting.strftime("%Y-%m-%d")
        year = int(week_starting.year)

        result = await self._make_request(
            "GET",
            "project-logs/person/list/",
            email,
            params={"year": year}
        )
        if not isinstance(result, dict) or result.get("status") != "success":
            return json.dumps({
                "status": "error",
                "message": "Could not load week logs list."
            }, indent=2)

        data = result.get("data", {})
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                pass
        if isinstance(data, dict) and "person_week_logs" in data:
            all_logs = []
            for month_data in data.get("person_week_logs", []):
                if isinstance(month_data, dict) and "months_log" in month_data:
                    month_logs = month_data.get("months_log", [])
                    if isinstance(month_logs, list):
                        all_logs.extend(month_logs)
            data = all_logs

        def _find_week_log_id(data_item, target_week):
            if isinstance(data_item, dict):
                week_start = data_item.get("week_starting", "")
                if week_start == target_week:
                    wid = data_item.get("id")
                    if wid is not None:
                        return int(wid) if isinstance(wid, int) else int(wid)
                if ", " in str(week_start):
                    parts = str(week_start).split(", ")
                    if len(parts) == 2:
                        month_day = parts[1].split()
                        if len(month_day) == 2:
                            month_map = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                                         "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
                            try:
                                day = int(month_day[1])
                                month_num = month_map.get(month_day[0])
                                if month_num and len(target_week) >= 4:
                                    y = int(target_week[:4])
                                    parsed = datetime(y, month_num, day).date()
                                    if parsed.strftime("%Y-%m-%d") == target_week:
                                        wid = data_item.get("id")
                                        if wid is not None:
                                            return int(wid) if isinstance(wid, int) else int(wid)
                            except (ValueError, TypeError, KeyError):
                                pass
                for v in data_item.values():
                    found = _find_week_log_id(v, target_week)
                    if found is not None:
                        return found
            elif isinstance(data_item, list):
                for item in data_item:
                    found = _find_week_log_id(item, target_week)
                    if found is not None:
                        return found
            return None

        week_log_id = _find_week_log_id(data, week_starting_str)
        if not week_log_id:
            return json.dumps({
                "status": "error",
                "message": f"No week log for week starting {week_starting_str}. Nothing to delete."
            }, indent=2)

        get_result = await self._make_request(
            "GET",
            f"project-logs/person/get/{week_log_id}/",
            email
        )
        if get_result.get("status") != "success":
            return json.dumps(get_result, indent=2)

        week_log_data = get_result.get("data", {})
        if "modified_at" not in week_log_data:
            return json.dumps({
                "status": "error",
                "message": "Week log data missing modified_at (required for save)."
            }, indent=2)

        active_projects_result = await self._make_request(
            "GET",
            "project-logs/person/active_project_list/",
            email
        )
        if active_projects_result.get("status") != "success":
            return json.dumps(active_projects_result, indent=2)
        active_projects = active_projects_result.get("data", [])
        active_project_team_name = None
        for proj in active_projects:
            if isinstance(proj, dict) and proj.get("id") is not None:
                try:
                    if int(proj.get("id")) == project_id:
                        active_project_team_name = proj.get("team") or proj.get("subteam") or ""
                        break
                except (ValueError, TypeError):
                    continue

        project_data = None
        for project in week_log_data.get("projects", []):
            pteam = (project.get("team") or "").strip()
            psub = (project.get("subteam") or "").strip()
            if active_project_team_name and (pteam == active_project_team_name or psub == active_project_team_name or
                                            active_project_team_name in pteam or active_project_team_name in psub):
                project_data = project
                break

        if not project_data:
            return json.dumps({
                "status": "error",
                "message": f"Project not found in week log for week starting {week_starting_str}."
            }, indent=2)

        tasks = project_data.get("tasks", [])
        task_index = None
        for i, task in enumerate(tasks):
            if (task.get("description") or "").strip() == (description or "").strip():
                task_index = i
                break

        if task_index is None:
            desc_preview = (description or "")[:50] + ("..." if len(description or "") > 50 else "")
            return json.dumps({
                "status": "error",
                "message": f"No task with description matching \"{desc_preview}\" found in that project for the week."
            }, indent=2)

        task = tasks[task_index]
        days = task.get("days", [])
        day_dates = []
        for d in days:
            dt = d.get("date")
            if dt is not None:
                day_dates.append(str(dt) if not isinstance(dt, str) else dt)
            else:
                day_dates.append(None)

        if date not in day_dates:
            return json.dumps({
                "status": "error",
                "message": f"No log entry for date {date} in that task."
            }, indent=2)

        new_days = [d for d in days if str(d.get("date")) != date]
        if not new_days:
            tasks.pop(task_index)
        else:
            task["days"] = new_days

        save_result = await self._make_request(
            "PATCH",
            f"project-logs/person/person-week-log/save/{week_log_id}/",
            email,
            data=week_log_data
        )
        if save_result.get("status") == "success":
            return json.dumps({
                "status": "success",
                "message": f"Log entry removed for {date}.",
                "data": save_result.get("data", {})
            }, indent=2)
        return json.dumps(save_result, indent=2)

    async def complete_week_log(
        self,
        week_starting: str,
        email: Optional[str] = None,
        save_draft: bool = False
    ) -> str:
        """Mark a week's project logs as completed."""
        # Get week log ID first
        year = datetime.strptime(week_starting, "%Y-%m-%d").year
        # Ensure year is an integer (not string) as Django expects
        result = await self._make_request(
            "GET",
            "project-logs/person/list/",
            email,
            params={"year": int(year)}
        )
        
        # Find the week log ID - use same logic as get_week_logs
        week_log_id = None
        if result["status"] == "success":
            data = result.get("data", [])
            week_starting_date = datetime.strptime(week_starting, "%Y-%m-%d").date()
            week_starting_str = week_starting_date.strftime("%Y-%m-%d")
            
            def find_week_log_id_in_data(data_item, target_week_starting, target_date):
                """Recursively search for week log ID in data structure."""
                if isinstance(data_item, dict):
                    week_start = data_item.get("week_starting", "")
                    if week_start:
                        # Try exact match first
                        if week_start == target_week_starting:
                            return data_item.get("id")
                        # Try parsing "Mon, Dec 29" format
                        try:
                            if ", " in week_start:
                                parts = week_start.split(", ")
                                if len(parts) == 2:
                                    month_day = parts[1].split()
                                    if len(month_day) == 2:
                                        month_name = month_day[0]
                                        day = int(month_day[1])
                                        month_map = {
                                            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                                            "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
                                        }
                                        month_num = month_map.get(month_name)
                                        if month_num:
                                            parsed_date = datetime(target_date.year, month_num, day).date()
                                            if parsed_date == target_date:
                                                return data_item.get("id")
                        except (ValueError, KeyError, IndexError):
                            pass
                    # Recursively search in nested structures
                    for value in data_item.values():
                        result_id = find_week_log_id_in_data(value, target_week_starting, target_date)
                        if result_id:
                            return result_id
                elif isinstance(data_item, list):
                    for item in data_item:
                        result_id = find_week_log_id_in_data(item, target_week_starting, target_date)
                        if result_id:
                            return result_id
                return None
            
            week_log_id = find_week_log_id_in_data(data, week_starting_str, week_starting_date)
        
        if not week_log_id:
            return json.dumps({
                "status": "error",
                "message": f"Week log not found for week starting {week_starting}"
            }, indent=2)
        
        # Ensure save_draft is a boolean (Django expects boolean, not string)
        result = await self._make_request(
            "PATCH",
            f"project-logs/person/person-week-log/complete/{week_log_id}/",
            email,
            data={"save_draft": bool(save_draft)}
        )
        
        return json.dumps(result, indent=2)

    async def get_active_projects(self, email: Optional[str] = None) -> str:
        """Get list of active projects for a person."""
        # The Django API accepts optional email query parameter
        params = {}
        if email:
            params["email"] = email
        
        result = await self._make_request(
            "GET",
            "project-logs/person/active_project_list/",
            email,
            params=params
        )
        
        return json.dumps(result, indent=2)
    
    async def check_person_week_project_exists(
        self,
        date: str,
        project_id: int,
        email: Optional[str] = None
    ) -> str:
        """
        Check if PersonWeekProject exists for a given date and project.
        
        PersonWeekProject is required before adding logs. It links:
        - PersonWeekLog (for the week containing the date)
        - PersonTeam (linking person to the subteam/project)
        
        Args:
            date: Date in YYYY-MM-DD format
            project_id: Project/subteam ID (from active_projects)
            email: Optional email address
            
        Returns:
            JSON string indicating if PersonWeekProject exists and what's needed if it doesn't.
        """
        try:
            # Calculate week starting date (Monday)
            log_date = datetime.strptime(date, "%Y-%m-%d").date()
            days_since_monday = log_date.weekday()
            week_starting = log_date - timedelta(days=days_since_monday)
            week_starting_str = week_starting.strftime("%Y-%m-%d")
            
            # Get the week log to check if PersonWeekProject exists
            week_log_result = await self.get_week_logs(
                week_starting=week_starting_str,
                email=email
            )
            week_log_data = json.loads(week_log_result)
            
            if week_log_data.get("status") != "success":
                return json.dumps({
                    "status": "error",
                    "message": f"Could not retrieve week log for {week_starting_str}",
                    "details": week_log_data.get("message", "Unknown error"),
                    "required": {
                        "week_starting": week_starting_str,
                        "project_id": project_id,
                        "note": "PersonWeekLog may need to be created first through the web interface"
                    }
                }, indent=2)
            
            week_log_info = week_log_data.get("data", {})
            projects = week_log_info.get("projects", [])
            
            # Check if project exists in the week log
            project_id_int = int(project_id)
            project_found = False
            person_week_project_id = None
            
            for project in projects:
                # The project_id from active_projects is nsubteam.id
                # We need to check if this project exists in the week log
                # The week log returns PersonWeekProject objects with "id" field
                # But we need to match by the underlying person_team's nsubteam_id
                # Since the serializer doesn't expose nsubteam_id directly, we'll check by project_id
                # from active_projects which should match
                project_week_id = project.get("id")
                if project_week_id:
                    try:
                        # Try to match - this is approximate since we don't have direct nsubteam_id
                        # We'll need to get active projects and match
                        active_projects_result = await self.get_active_projects(email)
                        active_projects_data = json.loads(active_projects_result)
                        if active_projects_data.get("status") == "success":
                            active_projects = active_projects_data.get("data", [])
                            for ap in active_projects:
                                if isinstance(ap, dict) and int(ap.get("id", 0)) == project_id_int:
                                    # Found matching active project, check if it's in week log
                                    # We can't directly match, so we'll return what we know
                                    project_found = True
                                    person_week_project_id = project_week_id
                                    break
                    except (ValueError, TypeError):
                        continue
            
            if project_found:
                return json.dumps({
                    "status": "success",
                    "exists": True,
                    "person_week_project_id": person_week_project_id,
                    "week_starting": week_starting_str,
                    "project_id": project_id_int,
                    "message": "PersonWeekProject exists. You can add logs for this date and project."
                }, indent=2)
            else:
                return json.dumps({
                    "status": "success",
                    "exists": False,
                    "week_starting": week_starting_str,
                    "project_id": project_id_int,
                    "required": {
                        "person_week_log": {
                            "week_starting": week_starting_str,
                            "exists": week_log_info.get("id") is not None,
                            "id": week_log_info.get("id")
                        },
                        "person_team": {
                            "nsubteam_id": project_id_int,
                            "note": "Should exist if project is in active_projects"
                        },
                        "person_week_project": {
                            "required_fields": {
                                "person_week_log_id": week_log_info.get("id"),
                                "person_team_id": "Must match PersonTeam with nsubteam_id=" + str(project_id_int)
                            },
                            "note": "PersonWeekProject must exist before adding logs. Create it through the web interface by adding at least one log entry for this project and week."
                        }
                    },
                    "message": "PersonWeekProject does not exist. You need to create it first before adding logs."
                }, indent=2)
                
        except ValueError as e:
            return json.dumps({
                "status": "error",
                "message": f"Invalid date format: {str(e)}. Expected YYYY-MM-DD."
            }, indent=2)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "message": f"Error checking PersonWeekProject: {str(e)}"
            }, indent=2)

    async def get_log_labels(self, email: Optional[str] = None) -> str:
        """Get available log labels."""
        result = await self._make_request(
            "GET",
            "project-logs/log_labels/",
            email
        )
        
        return json.dumps(result, indent=2)

    async def get_month_logs(
        self,
        year: int,
        month: int,
        email: Optional[str] = None
    ) -> str:
        """Get all project logs for a specific month."""
        # Ensure year and month are integers (not strings) as Django expects
        result = await self._make_request(
            "GET",
            "project-logs/person/month-list/",
            email,
            params={"year": int(year), "month": int(month)}
        )
        
        return json.dumps(result, indent=2)

    async def fill_logs_for_days(
        self,
        start_date: str,
        end_date: str,
        project_id: int,
        description: str,
        email: Optional[str] = None,
        hours_per_day: float = 8,
        label_id: Optional[int] = None,
        skip_weekends: bool = False
    ) -> str:
        """Fill logs for multiple days at once."""
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        
        current_date = start
        results = []
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        while current_date <= end:
            # Skip weekends if requested
            if skip_weekends and current_date.weekday() >= 5:
                skipped_count += 1
                current_date += timedelta(days=1)
                continue
            
            # Create log for this date
            result = await self.create_or_update_log(
                date=current_date.strftime("%Y-%m-%d"),
                project_id=project_id,
                description=description,
                hours=hours_per_day,
                email=email,
                label_id=label_id
            )
            
            result_data = json.loads(result)
            if result_data.get("status") == "success":
                updated_count += 1
            else:
                results.append({
                    "date": current_date.strftime("%Y-%m-%d"),
                    "error": result_data.get("message", "Unknown error")
                })
            
            current_date += timedelta(days=1)
        
        return json.dumps({
            "status": "success",
            "message": f"Filled logs for date range {start_date} to {end_date}",
            "data": {
                "start_date": start_date,
                "end_date": end_date,
                "created": created_count,
                "updated": updated_count,
                "skipped": skipped_count,
                "total_days": (end - start).days + 1,
                "errors": results
            }
        }, indent=2)
