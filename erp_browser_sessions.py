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
    browser = None

    def set_error(msg: str) -> None:
        session["error"] = msg
        session["event"].set()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        set_error("Playwright not installed")
        return

    try:
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
                set_error(str(e))
                return

            try:
                context = await browser.new_context(
                    viewport=VIEWPORT,
                    ignore_https_errors=True,
                )
                main_page = await context.new_page()
                session["main_page"] = main_page
                session["page"] = main_page  # active page we stream and send input to (main or popup)

                async def on_popup(popup):
                    # Google OAuth etc. open in a popup; stream and control the popup so user can complete login
                    session["page"] = popup
                    try:
                        await popup.set_viewport_size(VIEWPORT["width"], VIEWPORT["height"])
                    except Exception:
                        pass
                    def on_close():
                        if session.get("page") is popup:
                            session["page"] = session.get("main_page")
                    popup.once("close", on_close)

                context.on("page", on_popup)

                # One initial screenshot so the client gets a frame immediately (avoids "Connecting…" with no feedback)
                try:
                    session["last_screenshot"] = await main_page.screenshot(type="png", timeout=3000)
                except Exception:
                    pass
                await main_page.goto(erp_url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                set_error(f"Could not open ERP: {e}")
                await browser.close()
                return

            deadline = time.monotonic() + LOGIN_WAIT_TIMEOUT
            token = None
            email = None

            try:
                while time.monotonic() < deadline and not session.get("closed"):
                    active_page = session.get("page")
                    # Process pending input (non-blocking) – send to whichever page we're streaming (main or popup)
                    while True:
                        try:
                            ev = session["input_queue"].get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        try:
                            if ev.get("type") == "click" and active_page:
                                x = ev.get("x", 0)
                                y = ev.get("y", 0)
                                await active_page.mouse.click(x, y)
                            elif ev.get("type") == "key" and active_page:
                                key = ev.get("key")
                                if key:
                                    await active_page.keyboard.press(key)
                            elif ev.get("type") == "type" and active_page:
                                text = ev.get("text", "")
                                await active_page.keyboard.type(text, delay=10)
                        except Exception:
                            pass

                    # Screenshot the active page (main or popup) so user sees what they're interacting with
                    try:
                        if active_page:
                            shot = await active_page.screenshot(type="png", timeout=2000)
                            session["last_screenshot"] = shot
                            session["last_screenshot_time"] = time.monotonic()
                    except Exception:
                        pass

                    # Token lives in the main window's localStorage after OAuth popup closes
                    main = session.get("main_page")
                    if main:
                        try:
                            token = await main.evaluate(
                                f"() => window.localStorage && window.localStorage.getItem('{ERP_LOCALSTORAGE_TOKEN_KEY}')"
                            )
                            if token and isinstance(token, str) and token.strip():
                                try:
                                    email = await main.evaluate(
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
                try:
                    await browser.close()
                except Exception:
                    pass
                session["page"] = None
                session["main_page"] = None

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
    except Exception as e:
        set_error(str(e))
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        session["page"] = None
        session["closed"] = True
