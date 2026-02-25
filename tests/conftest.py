"""Pytest configuration for ERP MCP server tests.

Sets required environment variables before any test module imports server.py,
which reads GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET at module level.
"""

import os

os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-google-client-secret")
os.environ.setdefault("MCP_BASE_URL", "http://localhost:8100")
os.environ.setdefault("ERP_API_BASE_URL", "http://127.0.0.1:8000/api/v1")
os.environ.setdefault("ALLOWED_DOMAIN", "arbisoft.com")
os.environ.setdefault("ENABLED_DOMAINS", "timelogs,leaves")
