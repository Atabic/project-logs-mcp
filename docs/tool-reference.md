# ERP MCP Server -- Tool Reference

> Complete reference for all 21 MCP tools exposed by the ERP MCP server.
> For setup, deployment, and architecture see the [README](../README.md).

**Audience:** MCP client developers and AI assistants consuming these tools.
**Source of truth:** `tools/timelogs.py`, `tools/leaves.py`, `_constants.py`.

---

## Table of Contents

- [Conventions](#conventions)
- [Constants](#constants)
- [Timelogs Domain (11 tools)](#timelogs-domain-11-tools)
  - [Read Tools](#timelogs-read-tools)
    - [`timelogs_list_projects`](#timelogs_list_projects)
    - [`timelogs_list_labels`](#timelogs_list_labels)
    - [`timelogs_get_week`](#timelogs_get_week)
    - [`timelogs_get_day`](#timelogs_get_day)
    - [`timelogs_get_range`](#timelogs_get_range)
    - [`timelogs_get_month`](#timelogs_get_month)
  - [Write Tools](#timelogs-write-tools)
    - [`timelogs_upsert_entry`](#timelogs_upsert_entry)
    - [`timelogs_delete_entry`](#timelogs_delete_entry)
    - [`timelogs_complete_week`](#timelogs_complete_week)
    - [`timelogs_fill_days`](#timelogs_fill_days)
  - [Diagnostic Tools](#timelogs-diagnostic-tools)
    - [`timelogs_check_week_project`](#timelogs_check_week_project)
- [Leaves Domain (10 tools)](#leaves-domain-10-tools)
  - [Read Tools](#leaves-read-tools)
    - [`leaves_get_choices`](#leaves_get_choices)
    - [`leaves_get_fiscal_years`](#leaves_get_fiscal_years)
    - [`leaves_get_summary`](#leaves_get_summary)
    - [`leaves_list_month`](#leaves_list_month)
    - [`leaves_list_mine`](#leaves_list_mine)
    - [`leaves_list_team`](#leaves_list_team)
    - [`leaves_list_encashments`](#leaves_list_encashments)
  - [Write Tools](#leaves-write-tools)
    - [`leaves_apply`](#leaves_apply)
    - [`leaves_cancel`](#leaves_cancel)
    - [`leaves_create_encashment`](#leaves_create_encashment)

---

## Conventions

**Date format.** All date parameters must be `YYYY-MM-DD` (e.g., `2026-01-15`). The server validates this with a strict regex and rejects ISO week-date formats.

**Project resolution.** Tools that target a project accept either `project_id` (exact int) or `project_name` (case-insensitive substring match against the `team` field). Exactly one must be provided. If `project_name` matches multiple projects, the tool returns a `ToolError` listing the ambiguous matches and their team names. An exact case-insensitive match takes priority over substring matches.

**Label resolution.** Tools that accept a label accept either `label_id` (exact int) or `label_name` (case-insensitive exact match against the `name` field). Both are optional -- when neither is provided, the server falls back to `DEFAULT_LABEL_ID` (66).

**Week starting.** Parameters named `week_starting` must be a Monday. The server validates the weekday and returns a `ToolError` naming the actual day of the week if it is not Monday.

**Return shape.** All tools return `dict[str, Any]`. Successful responses have `"status": "success"` with a `"data"` key. The server converts error dicts (`"status": "error"`) into `ToolError` exceptions before the response reaches the client.

**Error handling.** All tools raise `ToolError` on failure -- never raw Python exceptions. The `tool_error_handler` decorator catches `PermissionError` and `ValueError` (preserving their message) and converts all other exceptions to a generic error string (SEC-04: no stack traces leak to clients). Write tools log audit entries server-side with the pattern `WRITE_OP tool=<name> user=<email> ...`.

**Authentication.** Every tool call requires a valid Google OAuth token. The server extracts the user identity from the token claims -- no tool accepts an email or user ID parameter (SEC-01). Domain restriction is enforced via the `hd` claim and email suffix (SEC-02).

---

## Constants

Defined in `_constants.py`. Referenced by tool validation logic.

| Constant | Value | Used By |
|----------|-------|---------|
| `MAX_FILL_DAYS` | 31 | `timelogs_fill_days` -- max day span for bulk fill |
| `MAX_QUERY_DAYS` | 366 | `timelogs_get_range` -- max day span for read queries |
| `MAX_DESCRIPTION_LEN` | 5000 | `timelogs_upsert_entry`, `timelogs_delete_entry`, `timelogs_fill_days` -- max chars |
| `DEFAULT_LABEL_ID` | 66 | Fallback label when no label is specified on write tools |
| `MAX_RECURSION_DEPTH` | 20 | Internal: week log ID search depth in nested API responses |
| `MAX_TOKEN_LENGTH` | 4096 | Internal: max Google token byte length |
| `MAX_LEAVE_REASON_LEN` | 2000 | `leaves_apply` -- max chars for leave reason |
| `MAX_LEAVE_DAYS` | 90 | `leaves_apply` -- max day span for a single leave request |
| `MAX_ENCASHMENT_DAYS` | 90 | `leaves_create_encashment` -- max days to encash |

---

## Timelogs Domain (11 tools)

### Timelogs Read Tools

#### `timelogs_list_projects`

> Get list of active projects/subteams for the authenticated user.

No parameters.

**Returns:** List of project dicts, each containing `id` (int), `team` (str, project/subteam name), and `is_active` (bool).

**Notes:** Use the returned `id` values as `project_id` in other timelog tools, or use the `team` values as `project_name` for name-based resolution.

**See Also:** [`timelogs_list_labels`](#timelogs_list_labels)

---

#### `timelogs_list_labels`

> Get available log labels for categorizing log entries.

No parameters.

**Returns:** List of label dicts, each containing `id` (int) and `name` (str).

**Notes:** Use the returned `id` values as `label_id` in write tools, or use `name` values as `label_name` for name-based resolution. When no label is specified on a write tool, `DEFAULT_LABEL_ID` (66) is used.

**See Also:** [`timelogs_list_projects`](#timelogs_list_projects)

---

#### `timelogs_get_week`

> Get project logs for a specific week.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `week_starting` | `str` | Yes | -- | `YYYY-MM-DD`, must be a Monday |

**Returns:** Detailed week log containing projects, tasks, day-level details, completion status, and person metadata.

**Notes:** The server validates that the date falls on a Monday. If no week log exists for the given Monday, the tool returns an error with the message "Week log not found for week starting {date}".

**See Also:** [`timelogs_get_day`](#timelogs_get_day), [`timelogs_get_month`](#timelogs_get_month), [`timelogs_complete_week`](#timelogs_complete_week)

---

#### `timelogs_get_day`

> Get detailed project logs for a specific day.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `date` | `str` | Yes | -- | `YYYY-MM-DD` |

**Returns:** Day's projects, tasks, and hours with computed totals. Includes `week_starting` (the Monday of that week), and a `week_log` object with `id`, `person_id`, `person_name`, `is_completed`, `week_starting`, and `week_ending`.

**Notes:** Internally derives the day's data from its parent week log. The server auto-computes the Monday for the given date and fetches the full week, then extracts the single day.

**See Also:** [`timelogs_get_week`](#timelogs_get_week), [`timelogs_get_range`](#timelogs_get_range)

---

#### `timelogs_get_range`

> Get project logs for a date range.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `start_date` | `str` | Yes | -- | `YYYY-MM-DD` |
| `end_date` | `str` | Yes | -- | `YYYY-MM-DD`, must be on or after `start_date` |

**Returns:** Dict with `data` (list of week log summaries overlapping the range), `count` (int), `start_date`, and `end_date`. Week logs are filtered to only those whose Monday-to-Sunday span overlaps the requested range.

**Notes:** The date range is capped at `MAX_QUERY_DAYS` (366) days. Internally fetches month-list data for each calendar month in the range, then filters to overlapping weeks.

**See Also:** [`timelogs_get_month`](#timelogs_get_month), [`timelogs_get_day`](#timelogs_get_day)

---

#### `timelogs_get_month`

> Get all project logs for a specific month and year.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `year` | `int` | Yes | -- | e.g., 2024 |
| `month` | `int` | Yes | -- | 1--12 |

**Returns:** Month's week log summaries from the ERP month-list API.

**Notes:** Returns all week logs that fall within the given month. This is the raw month-list endpoint response without additional filtering.

**See Also:** [`timelogs_get_range`](#timelogs_get_range), [`timelogs_get_week`](#timelogs_get_week)

---

### Timelogs Write Tools

#### `timelogs_upsert_entry`

> Add or update a time log entry for a date.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `date` | `str` | Yes | -- | `YYYY-MM-DD` |
| `description` | `str` | Yes | -- | Max 5000 chars (`MAX_DESCRIPTION_LEN`) |
| `hours` | `float` | Yes | -- | `0 < hours <= 24`; minimum ~0.02 (1 minute) |
| `project_id` | `int \| None` | Conditional | `None` | Exactly one of `project_id` / `project_name` required |
| `project_name` | `str \| None` | Conditional | `None` | Case-insensitive substring match against `team` field |
| `label_id` | `int \| None` | No | `None` | Falls back to `DEFAULT_LABEL_ID` (66) when both label params omitted |
| `label_name` | `str \| None` | No | `None` | Case-insensitive exact match against `name` field |

**Returns:** The saved week log data on success.

**Notes:**
- Idempotent: re-calling with the same date, project, and description updates the existing entry rather than creating a duplicate. Task matching is case-insensitive on the description.
- Dual write path: if a week log already exists for the target week, the server uses the Save API (PATCH). If no week log exists, it falls back to the Slack endpoint (POST), which creates the week log implicitly.
- Hours are converted to hours and minutes internally (e.g., 2.5 becomes 02:30). Values that round to 0 minutes are rejected.
- Audit logged: `WRITE_OP tool=timelogs_upsert_entry`.

**See Also:** [`timelogs_delete_entry`](#timelogs_delete_entry), [`timelogs_fill_days`](#timelogs_fill_days), [`timelogs_check_week_project`](#timelogs_check_week_project)

---

#### `timelogs_delete_entry`

> Remove a time log entry for a date.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `date` | `str` | Yes | -- | `YYYY-MM-DD` |
| `description` | `str` | Yes | -- | Exact task description to match; max 5000 chars |
| `project_id` | `int \| None` | Conditional | `None` | Exactly one of `project_id` / `project_name` required |
| `project_name` | `str \| None` | Conditional | `None` | Case-insensitive substring match against `team` field |

**Returns:** Confirmation message and the updated week log data on success.

**Notes:**
- Task matching is case-insensitive on the description. The first 50 characters are shown in error messages if no match is found.
- Internally removes the day entry from the matched task. If the task has no remaining days after removal, the entire task is removed from the project.
- Operates via the Save API (PATCH) on the existing week log. If no week log exists for the target week, the tool returns an error.
- Audit logged: `WRITE_OP tool=timelogs_delete_entry`.

**See Also:** [`timelogs_upsert_entry`](#timelogs_upsert_entry)

---

#### `timelogs_complete_week`

> Mark a week's project logs as completed (or save as draft).

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `week_starting` | `str` | Yes | -- | `YYYY-MM-DD`, must be a Monday |
| `save_draft` | `bool` | No | `False` | When `True`, saves as draft without completing |

**Returns:** Result from the ERP complete/draft endpoint.

**Notes:**
- When `save_draft` is `False` (default), the week log is marked as completed. This may trigger downstream workflows in the ERP (e.g., manager notifications).
- When `save_draft` is `True`, the week log is saved without completing.
- Requires an existing week log for the specified Monday. Returns an error if none is found.
- Audit logged: `WRITE_OP tool=timelogs_complete_week`.

**See Also:** [`timelogs_get_week`](#timelogs_get_week), [`timelogs_upsert_entry`](#timelogs_upsert_entry)

---

#### `timelogs_fill_days`

> Fill time logs for multiple days at once (bulk operation).

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `start_date` | `str` | Yes | -- | `YYYY-MM-DD` |
| `end_date` | `str` | Yes | -- | `YYYY-MM-DD`, must be on or after `start_date` |
| `description` | `str` | Yes | -- | Max 5000 chars (`MAX_DESCRIPTION_LEN`) |
| `hours_per_day` | `float` | No | `8.0` | `0 < hours_per_day <= 24`; minimum ~0.02 (1 minute) |
| `project_id` | `int \| None` | Conditional | `None` | Exactly one of `project_id` / `project_name` required |
| `project_name` | `str \| None` | Conditional | `None` | Case-insensitive substring match against `team` field |
| `label_id` | `int \| None` | No | `None` | Falls back to `DEFAULT_LABEL_ID` (66) when both label params omitted |
| `label_name` | `str \| None` | No | `None` | Case-insensitive exact match against `name` field |
| `skip_weekends` | `bool` | No | `False` | When `True`, skips Saturday and Sunday |

**Returns:** Summary dict with `updated` (int, days successfully logged), `skipped` (int, weekend days skipped), `total_days` (int, span of the range), and `errors` (list of `{date, error}` dicts for failed days). Status is `"success"` if all days succeeded, `"partial_error"` if some failed, or `"error"` if all failed.

**Notes:**
- Date range is capped at `MAX_FILL_DAYS` (31) days. Requests exceeding this are rejected before any writes.
- Executes sequential upserts for each day in the range (2-4 HTTP calls per day internally). Has a 5-minute timeout; if exceeded, returns a `ToolError` suggesting a smaller range.
- Idempotent: re-running for the same dates updates existing entries rather than creating duplicates.
- Project and label are resolved once before the loop begins, not per-day.
- Audit logged: `WRITE_OP tool=timelogs_fill_days`.

**See Also:** [`timelogs_upsert_entry`](#timelogs_upsert_entry), [`timelogs_complete_week`](#timelogs_complete_week)

---

### Timelogs Diagnostic Tools

#### `timelogs_check_week_project`

> Check if a PersonWeekProject exists for a given date and project.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `date` | `str` | Yes | -- | `YYYY-MM-DD` |
| `project_id` | `int \| None` | Conditional | `None` | Exactly one of `project_id` / `project_name` required |
| `project_name` | `str \| None` | Conditional | `None` | Case-insensitive substring match against `team` field |

**Returns:** Dict with `exists` (bool), `person_week_project_id` (int, present only when `exists` is `True`), `week_starting` (str, the computed Monday), and `project_id` (int).

**Notes:**
- PersonWeekProject is a prerequisite for adding time logs to a project for a given week. It links `PersonWeekLog` (the week) to `PersonTeam` (the person-to-subteam assignment).
- PersonWeekProject records are created by the ERP web application or team-assignment workflows -- not by this MCP server. If `exists` is `False`, the user must create the link via the ERP web interface before logs can be added.
- The server auto-computes the Monday for the given date before checking.

**See Also:** [`timelogs_upsert_entry`](#timelogs_upsert_entry), [`timelogs_list_projects`](#timelogs_list_projects)

---

## Leaves Domain (10 tools)

### Leaves Read Tools

#### `leaves_get_choices`

> Get leave types and approver for the authenticated user.

No parameters.

**Returns:** Leave type list (each with `id` and display name) and the user's leave approver information.

**Notes:** The returned leave type `id` values are required as the `leave_type` parameter for `leaves_apply` and `leaves_create_encashment`.

**See Also:** [`leaves_apply`](#leaves_apply), [`leaves_create_encashment`](#leaves_create_encashment)

---

#### `leaves_get_fiscal_years`

> Get available fiscal years for leave summary queries.

No parameters.

**Returns:** List of fiscal years with their `id` values and date ranges, plus `selected_fiscal_year` (int) indicating the currently active fiscal year ID.

**Notes:** The returned `id` values are used as the `fiscal_year_id` parameter for `leaves_get_summary`. The `selected_fiscal_year` field identifies which fiscal year is currently active.

**See Also:** [`leaves_get_summary`](#leaves_get_summary)

---

#### `leaves_get_summary`

> Get leave balances for the authenticated user.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `fiscal_year_id` | `int \| None` | No | `None` | Auto-resolves to current fiscal year when omitted |

**Returns:** Leave balance breakdown including used, available, and encashed days per leave type for the specified fiscal year.

**Notes:** When `fiscal_year_id` is omitted, the tool automatically calls `leaves_get_fiscal_years` to resolve the current active fiscal year via the `selected_fiscal_year` field. If auto-resolution fails (no active fiscal year found), a `ToolError` is raised.

**See Also:** [`leaves_get_fiscal_years`](#leaves_get_fiscal_years), [`leaves_list_encashments`](#leaves_list_encashments)

---

#### `leaves_list_month`

> Get approved leaves and holidays for a specific month.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `year` | `int` | Yes | -- | e.g., 2024 |
| `month` | `int` | Yes | -- | 1--12 |

**Returns:** Dict with `leaves` (list of approved leave records), `holidays` (list of holiday records), `year`, and `month`.

**Notes:** Fetches leaves and holidays concurrently via `asyncio.gather()`. If one call fails but the other succeeds, the tool returns the successful data with an empty list for the failed one. If both fail, the leaves result is passed to `check_erp_result()`, which raises a `ToolError` if its status is `"error"`.

**See Also:** [`leaves_list_mine`](#leaves_list_mine), [`leaves_list_team`](#leaves_list_team)

---

#### `leaves_list_mine`

> List own leaves for a month (all statuses).

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `year` | `int` | Yes | -- | e.g., 2024 |
| `month` | `int` | Yes | -- | 1--12 |

**Returns:** List of the authenticated user's leave requests for the specified month, including pending, approved, and rejected leaves.

**Notes:** Unlike `leaves_list_month` (which returns only approved leaves and holidays), this tool returns leaves in all statuses for the authenticated user only.

**See Also:** [`leaves_list_month`](#leaves_list_month), [`leaves_cancel`](#leaves_cancel)

---

#### `leaves_list_team`

> List team members currently on leave.

No parameters.

**Returns:** List of team members who are currently on leave.

**Notes:** Shows a snapshot of team members on leave at the time of the call. Does not accept date parameters -- it always reflects the current state.

**See Also:** [`leaves_list_month`](#leaves_list_month)

---

#### `leaves_list_encashments`

> List own leave encashment claims.

No parameters.

**Returns:** List of the authenticated user's encashment claims.

**Notes:** Returns all encashment requests regardless of status (pending, approved, rejected).

**See Also:** [`leaves_create_encashment`](#leaves_create_encashment), [`leaves_get_summary`](#leaves_get_summary)

---

### Leaves Write Tools

#### `leaves_apply`

> Apply for leave.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `leave_type` | `int` | Yes | -- | Leave type ID from `leaves_get_choices` |
| `start_date` | `str` | Yes | -- | `YYYY-MM-DD` |
| `end_date` | `str` | Yes | -- | `YYYY-MM-DD`, must be on or after `start_date` |
| `reason` | `str` | Yes | -- | Max 2000 chars (`MAX_LEAVE_REASON_LEN`) |
| `half_day` | `bool` | No | `False` | Set `True` for half-day leave |
| `half_day_period` | `str \| None` | Conditional | `None` | Required when `half_day` is `True`; must be `"first_half"` or `"second_half"` |

**Returns:** Result from the ERP leave application endpoint.

**Notes:**
- Date range is capped at `MAX_LEAVE_DAYS` (90) days.
- The `half_day_period` parameter is required when `half_day` is `True` and must not be provided when `half_day` is `False`. Providing `half_day_period` without `half_day=True` raises a `ToolError`.
- The ERP backend handles approver assignment, balance validation, and conflict detection.
- Audit logged: `WRITE_OP tool=leaves_apply`.

**See Also:** [`leaves_get_choices`](#leaves_get_choices), [`leaves_cancel`](#leaves_cancel), [`leaves_get_summary`](#leaves_get_summary)

---

#### `leaves_cancel`

> Cancel a pending leave request.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `leave_id` | `int` | Yes | -- | Positive integer |

**Returns:** Result from the ERP leave cancellation endpoint.

**Notes:**
- Ownership is enforced by the ERP backend via the authenticated user's token -- a user can only cancel their own leave requests.
- Only pending (not yet approved or rejected) leaves can be cancelled. The ERP backend enforces this constraint.
- Audit logged: `WRITE_OP tool=leaves_cancel`.

**See Also:** [`leaves_list_mine`](#leaves_list_mine), [`leaves_apply`](#leaves_apply)

---

#### `leaves_create_encashment`

> Create a leave encashment request.

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `leave_type` | `int` | Yes | -- | Leave type ID from `leaves_get_choices` |
| `days` | `int` | Yes | -- | Positive integer, max 90 (`MAX_ENCASHMENT_DAYS`) |

**Returns:** Result from the ERP encashment creation endpoint.

**Notes:**
- Encashment converts unused leave days into monetary compensation. Eligibility and balance validation are handled by the ERP backend.
- Audit logged: `WRITE_OP tool=leaves_create_encashment`.

**See Also:** [`leaves_get_choices`](#leaves_get_choices), [`leaves_list_encashments`](#leaves_list_encashments), [`leaves_get_summary`](#leaves_get_summary)
