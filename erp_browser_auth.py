"""
Authenticate by opening your ERP site in the browser and reading the token automatically.

Uses your installed Chrome or Edge when possible (via Playwright). Opens the ERP URL, you log in,
and the MCP captures the token from the page—no copy/paste.
"""

import asyncio
import os
import time
from typing import Optional, Callable

# ERP frontend stores the API token in localStorage under this key
ERP_LOCALSTORAGE_TOKEN_KEY = "token"

# How long to wait for user to log in (seconds)
LOGIN_WAIT_TIMEOUT = 300  # 5 minutes
POLL_INTERVAL = 2


def _erp_base_url() -> str:
    """Derive ERP site base URL from API base URL."""
    base = os.getenv("ERP_API_BASE_URL", "https://your-erp.example.com/api/v1/").strip().rstrip("/")
    if "/api/v1" in base:
        base = base.split("/api/v1")[0]
    elif "/api/" in base:
        base = base.split("/api/")[0]
    return base.rstrip("/") or "https://your-erp.example.com"


async def authenticate_via_erp_browser(
    store_token: Callable[[str, Optional[str]], None],
) -> dict:
    """
    Open ERP in your browser (Chrome, Edge, or Chromium), wait for you to log in,
    then read the token from the page and store it. No copy/paste.
    Prefers your installed Chrome, then Edge, then Playwright's Chromium.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "status": "error",
            "message": "Playwright is required. Install with: pip install playwright && playwright install chromium"
        }

    url = _erp_base_url()
    browser = None

    async with async_playwright() as p:
        # Prefer user's installed Chrome, then Edge, then Chromium
        for channel in ("chrome", "msedge", None):
            try:
                if channel:
                    browser = await p.chromium.launch(channel=channel, headless=False)
                else:
                    browser = await p.chromium.launch(headless=False)
                break
            except Exception:
                continue

        if browser is None:
            return {
                "status": "error",
                "message": "Could not launch a browser. Install Google Chrome, Microsoft Edge, or run: playwright install chromium"
            }

        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            await browser.close()
            return {"status": "error", "message": f"Could not open ERP: {e}"}

        token = None
        email = None
        try:
            deadline = time.monotonic() + LOGIN_WAIT_TIMEOUT
            while time.monotonic() < deadline:
                try:
                    token = await page.evaluate(
                        f"() => window.localStorage && window.localStorage.getItem('{ERP_LOCALSTORAGE_TOKEN_KEY}')"
                    )
                    if token and isinstance(token, str) and token.strip():
                        try:
                            email = await page.evaluate(
                                "() => window.localStorage && window.localStorage.getItem('username')"
                            )
                        except Exception:
                            pass
                        break
                except Exception:
                    pass
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await browser.close()

        if not token or not token.strip():
            return {
                "status": "error",
                "message": "No token found. Log in on the ERP page within {} minutes.".format(
                    LOGIN_WAIT_TIMEOUT // 60
                )
            }

        try:
            store_token(token.strip(), email.strip() if email and isinstance(email, str) else None)
        except Exception as e:
            return {"status": "error", "message": f"Could not store token: {e}"}

        return {"status": "success", "email": email if email else None}
