# ERP MCP – Test prompts for every tool

Use these prompts in Cursor (or any MCP client) to verify each tool. **Most tools require authentication first**—use one of the auth prompts below, then run the others.

---

## Authentication tools (use one first)

| # | Tool | Test prompt |
|---|------|-------------|
| 1 | `authenticate_with_token` | **"Authenticate with ERP using my token: Token abc123xyz"** (replace with a real token from the browser Network tab, Authorization header, when logged into your ERP host) |
| 2 | `authenticate_with_google` | **"Sign me in with Google; here’s my Google access token: ya29.xxx"** (replace with a real Google OAuth access_token; rarely used from Cursor) |
| 3 | `authenticate_via_browser` | **"Give me a URL to sign in with Google for the project logs MCP"** or **"I want to log in via browser like Figma MCP"** |
| 4 | `authenticate_via_erp_browser` | **"Open ERP in my browser and log me in; capture the token automatically"** or **"Sign me in via ERP browser"** |

---

## Read-only tools (need auth first)

| # | Tool | Test prompt |
|---|------|-------------|
| 5 | `get_week_logs` | **"Get my project logs for the week starting 2026-01-27"** or **"Show week logs for 2026-01-20"** |
| 6 | `get_day_logs` | **"Get my detailed logs for 2026-01-28"** or **"What did I log on 2026-01-15?"** |
| 7 | `get_logs_for_date_range` | **"Get my project logs from 2026-01-01 to 2026-01-11"** |
| 8 | `get_month_logs` | **"Get all my project logs for January 2026"** or **"Month logs for 2025-12"** |
| 9 | `get_active_projects` | **"List my active projects"** or **"What projects am I on?"** |
| 10 | `get_log_labels` | **"Get available log labels"** or **"What labels can I use for project logs?"** |
| 11 | `check_person_week_project_exists` | **"Check if I have a week log set up for 2026-01-28 on project Your Project Name"** (use a real project name from your list) |

---

## Write / action tools (need auth first)

| # | Tool | Test prompt |
|---|------|-------------|
| 12 | `create_or_update_log` | **"Log 2 hours for 2026-01-28 on Your Project Name, description: Testing MCP, label Coding"** or **"Add 4 hours on 2026-01-27 for project X, description: Code review"** |
| 13 | `delete_log` | **"Delete my log entry for 2026-01-28 on project Your Project Name with description 'Testing MCP'"** (use the exact description of an existing log) |
| 14 | `complete_week_log` | **"Mark my week starting 2026-01-27 as completed"** or **"Complete my week log for 2026-01-20"** |
| 15 | `fill_logs_for_days` | **"Fill 8 hours per day from 2026-01-20 to 2026-01-24 for project Your Project Name, description: Development work"** (optionally add "skip weekends") |

---

## Quick checklist (order to test)

1. **Auth:** Use prompt **#4** (ERP browser) or **#1** (token) so you’re logged in.
2. **Read:** **#9** (active projects) → **#5** (week logs) → **#6** (day logs) → **#7** (date range) → **#8** (month) → **#10** (labels) → **#11** (check week project).
3. **Write:** **#12** (create log) → **#13** (delete that log if you want) → **#14** (complete week) or **#15** (fill days), as needed.

---

## Minimal one-liners (copy-paste)

```
Authenticate with ERP browser and capture my token.
```
```
List my active projects.
```
```
Get my week logs for week starting 2026-01-27.
```
```
Get my detailed logs for 2026-01-28.
```
```
Get my logs from 2026-01-01 to 2026-01-11.
```
```
Get my project logs for January 2026.
```
```
What log labels are available?
```
```
Check if I have a week project for 2026-01-28 and project "Your Project Name".
```
```
Add 1 hour for 2026-01-30, project "Your Project Name", description "MCP test", label Coding.
```
```
Delete the log for 2026-01-30, project "Your Project Name", description "MCP test".
```
```
Complete my week for 2026-01-27.
```
```
Fill 8 hours per day from 2026-01-20 to 2026-01-24 for project "Your Project Name", description "Development".
```

Replace project names and dates with your real projects and dates.
