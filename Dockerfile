# Use official Playwright Python image so Chromium is pre-installed (required for ERP browser auth when deployed).
# See: https://playwright.dev/python/docs/docker
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Install Python deps (no need to run playwright install chromium; image has it).
COPY requirements-sse.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-sse.txt

COPY server_sse.py server.py api_client.py auth.py oauth_server.py erp_browser_auth.py erp_browser_sessions.py ./
# authorized_users.json is gitignored; auth.py creates an example at runtime if missing. Set MCP_AUTHORIZED_USERS in env instead.

ENV PORT=8000
EXPOSE 8000

CMD ["python", "server_sse.py"]
