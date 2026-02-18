"""
In-memory session store for deployed ERP browser auth.

When MCP is deployed (e.g. on Render), authenticate_via_erp_browser cannot open
a local browser. Instead we create a session and return a URL. When the user
opens that URL, we run Playwright headless and stream the browser to them.
"""

import asyncio
import secrets
import time
from typing import Any, Callable, Optional

# Session data: store_token callback, result when done, event to wait on,
# and Playwright state (last_screenshot, page, input_queue).
_sessions: dict[str, dict[str, Any]] = {}
# Lock for creating sessions
_lock = asyncio.Lock()
# Lock when starting the browser task for a session (avoid duplicate tasks)
_browser_task_lock = asyncio.Lock()

# From erp_browser_auth
ERP_LOCALSTORAGE_TOKEN_KEY = "token"
LOGIN_WAIT_TIMEOUT = 300
POLL_INTERVAL = 0.15  # fast polling for responsive stream
VIEWPORT = {"width": 1280, "height": 720}


def create_session(store_token: Callable[[str, Optional[str]], None]) -> str:
    """Create a new ERP browser session. Returns session_id."""
    session_id = secrets.token_urlsafe(16)
    _sessions[session_id] = {
        "store_token": store_token,
        "result": None,
        "event": asyncio.Event(),
        "browser_task": None,
        "last_screenshot": None,
        "last_screenshot_time": 0.0,
        "page": None,
        "input_queue": asyncio.Queue(),
        "started": False,
        "closed": False,
        "error": None,
    }
    return session_id


def get_session(session_id: str) -> Optional[dict[str, Any]]:
    return _sessions.get(session_id)


def mark_session_started(session_id: str, task: asyncio.Task) -> None:
    s = _sessions.get(session_id)
    if s:
        s["browser_task"] = task
        s["started"] = True


def remove_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


async def run_erp_browser_session(
    session_id: str,
    erp_url: str,
) -> None:
    """Run Playwright headless: navigate to ERP, stream screenshots, handle input, capture token."""
    session = get_session(session_id)
    if not session or session.get("started") and session.get("browser_task"):
        return
    session["started"] = True

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        session["error"] = "Playwright not installed"
        session["event"].set()
        return

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
        except Exception as e:
            session["error"] = str(e)
            session["event"].set()
            return

        try:
            context = await browser.new_context(
                viewport=VIEWPORT,
                ignore_https_errors=True,
            )
            page = await context.new_page()
            session["page"] = page
            await page.goto(erp_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            session["error"] = f"Could not open ERP: {e}"
            await browser.close()
            session["event"].set()
            return

        deadline = time.monotonic() + LOGIN_WAIT_TIMEOUT
        token = None
        email = None

        try:
            while time.monotonic() < deadline and not session.get("closed"):
                # Process pending input (non-blocking)
                while True:
                    try:
                        ev = session["input_queue"].get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    try:
                        if ev.get("type") == "click" and session["page"]:
                            x = ev.get("x", 0)
                            y = ev.get("y", 0)
                            await session["page"].mouse.click(x, y)
                        elif ev.get("type") == "key" and session["page"]:
                            key = ev.get("key")
                            if key:
                                await session["page"].keyboard.press(key)
                        elif ev.get("type") == "type" and session["page"]:
                            text = ev.get("text", "")
                            await session["page"].keyboard.type(text, delay=10)
                    except Exception:
                        pass

                # Screenshot
                try:
                    shot = await page.screenshot(type="png", timeout=2000)
                    session["last_screenshot"] = shot
                    session["last_screenshot_time"] = time.monotonic()
                except Exception:
                    pass

                # Check token
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
            session["closed"] = True
            await browser.close()
            session["page"] = None

        if token and isinstance(token, str) and token.strip():
            try:
                session["store_token"](
                    token.strip(),
                    email.strip() if email and isinstance(email, str) else None,
                )
                session["result"] = {"status": "success", "email": email}
            except Exception as e:
                session["result"] = {"status": "error", "message": str(e)}
        else:
            session["result"] = {
                "status": "error",
                "message": "No token found. Log in on the ERP page within the time limit.",
            }
        session["event"].set()
        # Do not remove_session here so client can fetch /status and see result
