# Django Backend API Dependency Structure for Project Logs

## Overview
This document explains how the Django backend handles creating `PersonWeekProject` when a user adds a log entry through the web interface, and the dependency chain between different models.

## Model Dependency Chain

```
Person
  └── PersonTeam (links Person to SubTeam/NSubTeam)
       └── PersonWeekLog (for a specific week, Monday to Sunday)
            └── PersonWeekProject (links PersonWeekLog to PersonTeam)
                 └── ProjectTask (description of work)
                      └── ProjectTaskDayDetail (specific day's hours)
```

## When User Adds Log Entry on Tuesday via Web Interface

### Step 1: Frontend Gets Week Log
- **Endpoint**: `GET /project-logs/person/get/<week_log_id>/`
- **View**: `PersonWeekLogGetView` (RetrieveAPIView)
- **What it does**:
  - Returns existing `PersonWeekLog` with all `PersonWeekProject` entries
  - If week log doesn't exist, frontend handles it (shows empty state or creates it)

### Step 2: Frontend Saves Log Entry
- **Endpoint**: `PATCH /project-logs/person/person-week-log/save/<week_log_id>/`
- **View**: `ProjectLogSaveView` (UpdateAPIView)
- **Serializer**: `PersonWeekLogSerializer.update()`
- **What it does**:
  1. Requires `PersonWeekLog` to already exist (it's an UpdateAPIView, not CreateAPIView)
  2. Updates the `PersonWeekLog` instance
  3. For each project in the data:
     - Updates existing `PersonWeekProject` (must already exist)
     - Creates/updates `ProjectTask` entries
     - Creates/updates `ProjectTaskDayDetail` entries (the actual day logs)

### Key Point: PersonWeekProject Must Exist
The web interface **requires** `PersonWeekProject` to already exist before adding logs. It doesn't create it on-the-fly.

## How PersonWeekProject Gets Created

### Automatic Creation via `create_person_week_projects` Function

Located in: `apps/project_logs/utils.py`

```python
def create_person_week_projects(queryset, person_team_id, person):
    """
    Create person week projects.
    
    For each week in queryset:
    1. Check if PersonWeekLog exists for that week
       - If exists: use it
       - If not: create new PersonWeekLog
    2. Check if PersonWeekProject exists for (PersonWeekLog, PersonTeam)
       - If not: create new PersonWeekProject
    """
```

**When is this called?**
- When a `PersonTeam` is created or updated (via signals or management commands)
- When a person is assigned to a new team/subteam
- Via management commands that initialize logs for a date range

### Manual Creation Flow

If `PersonWeekProject` doesn't exist, you need to:

1. **Ensure PersonWeekLog exists**:
   ```python
   person_week_log, created = PersonWeekLog.objects.get_or_create(
       person=person,
       week_starting=week_starting_date  # Monday of the week
   )
   ```

2. **Get PersonTeam**:
   - From `active_projects` API, the `id` field is `nsubteam.id`
   - Need to find `PersonTeam` where:
     - `person_team.person = person`
     - `person_team.nsubteam.id = nsubteam_id`
     - `person_team.is_active = True`
     - Date range overlaps with the week

3. **Create PersonWeekProject**:
   ```python
   PersonWeekProject.objects.create(
       person_week_log=person_week_log,
       person_team=person_team
   )
   ```

## SlackAppProjectLogSaveView Flow

**Endpoint**: `POST /project-logs/person/person-week-log-from-slack/`
**View**: `SlackAppProjectLogSaveView` (CreateAPIView)
**Permission**: `IsSlackApp | IsAuthenticated` (Slack secret header **or** authenticated user e.g. Token)

### What it does:
1. Validates input (email, logs with date, time_spent, description, subteam, label_id)
2. Gets `Person` from email
3. **Checks if PersonWeekProject exists** for each log:
   ```python
   project_filters = Q(
       person_week_log__week_starting=get_week_starting_date(log["date"]),
       person_team__nsubteam_id=log["subteam"]
   ) & Q(person_week_log__person=person)
   
   projects_data = PersonWeekProject.active_objects.filter(project_filters)
   ```
4. **If PersonWeekProject doesn't exist**: Returns `400` with `UNAUTHORIZED_PROJECT_ACCESS` ("Request unsuccessful. You are not part of the project(s)"). **No creation of PersonWeekLog or PersonWeekProject.**
5. **If exists**: Creates `ProjectTask` and `ProjectTaskDayDetail` only.

### When week log is missing
The Slack endpoint has **no special handling** for a missing week log. It uses the same check as above: if there is no `PersonWeekProject` for that (person, week_starting, nsubteam_id), it returns 400. It does **not** create `PersonWeekLog` or `PersonWeekProject` when they are missing. So "behaving as Slack" does not allow creating a week log from MCP; the backend never creates it on this endpoint.

## MCP Server Implementation (Current)

The MCP uses the **Save API first** (same as the frontend), then falls back to Slack when the week log does not exist:

1. **When a week log exists** (from `person/list` for that year):  
   MCP calls `GET person/get/<week_log_id>/`, merges the new task/day into the correct project (matched by active project team name), then **PATCH `person/person-week-log/save/<week_log_id>/`** with the full payload. No separate "create PersonWeekProject" step—the week log already contains the project.

2. **When no week log exists**:  
   MCP tries **POST `person/person-week-log-from-slack/`**. If that returns UNAUTHORIZED_PROJECT_ACCESS (PersonWeekProject missing), the user is told to create the week log once via the web interface, then can add more entries via MCP.

There is no backend API to create `PersonWeekLog` or `PersonWeekProject` from MCP; they are created by the web app or by team-assignment flows.

### Historical / alternative options:

#### Option 1: Create PersonWeekProject Before Adding Logs
1. Check if `PersonWeekProject` exists (using `check_person_week_project_exists` tool)
2. If not, we need to:
   - Get or create `PersonWeekLog` for the week
   - Find `PersonTeam` from `nsubteam_id` (from active_projects)
   - Create `PersonWeekProject`
   - **Problem**: We don't have an API endpoint to create `PersonWeekProject` directly

#### Option 2: Use ProjectLogSaveView Instead
- This is the endpoint the web interface uses
- It requires the full week log structure with all projects
- More complex but handles creation automatically through the serializer

#### Option 3: Create a New API Endpoint
- Create a Django endpoint that:
  1. Checks if `PersonWeekLog` exists, creates if not
  2. Checks if `PersonWeekProject` exists, creates if not
  3. Then creates the log entry
- This would be the cleanest solution but requires backend changes

## Required Data for PersonWeekProject

When creating `PersonWeekProject`, you need:

1. **PersonWeekLog**:
   - `person`: Person instance (from email)
   - `week_starting`: Monday of the week (calculated from any date in the week)

2. **PersonTeam**:
   - `person`: Person instance
   - `nsubteam`: NSubTeam instance (from `nsubteam_id` in active_projects)
   - `is_active`: True
   - Date range must overlap with the week

3. **PersonWeekProject**:
   - `person_week_log`: PersonWeekLog instance
   - `person_team`: PersonTeam instance

## API Endpoints Summary

| Endpoint | Method | Purpose | Requires PersonWeekProject? |
|----------|--------|---------|----------------------------|
| `/person/get/<id>/` | GET | Get week log details | No (returns empty if doesn't exist) |
| `/person/person-week-log/save/<id>/` | PATCH | Save week log (web interface) | Yes (must exist) |
| `/person/person-week-log-from-slack/` | POST | Save log via Slack/MCP | Yes (must exist) |
| `/person/active_project_list/` | GET | Get active projects | No |
| `/person/list/` | GET | List week logs for year | No |

## Conclusion

The Django backend expects `PersonWeekProject` to be created **before** adding log entries. The web interface handles this by:
1. Using management commands or signals to auto-create when teams are assigned
2. Frontend only allows adding logs to existing week logs that already have projects

For the MCP server, we need to either:
- Create `PersonWeekProject` before adding logs (requires finding PersonTeam)
- Use a different endpoint that handles creation
- Create a new backend endpoint that auto-creates the structure
