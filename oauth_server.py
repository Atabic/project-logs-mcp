"""
Local OAuth server for browser-based sign-in (Figma MCP style).

Runs a small HTTP server on localhost. User opens a URL in the browser,
signs in with Google, and is redirected back so the MCP can complete auth.
No Django backend changes required.
"""

import asyncio
import os
import urllib.parse
from typing import Optional, Callable, Any

try:
    from aiohttp import web
except ImportError:
    web = None

# Default port for OAuth callback (must be whitelisted in Google Cloud Console as redirect URI)
DEFAULT_OAUTH_PORT = 8765
OAUTH_PORT = int(os.getenv("MCP_OAUTH_PORT", str(DEFAULT_OAUTH_PORT)))
# Google OAuth client ID (same as Django EXTERNAL_GOOGLE_CLIENT_ID). Required for browser flow.
GOOGLE_CLIENT_ID = os.getenv("MCP_GOOGLE_CLIENT_ID", "").strip()

# Scopes needed for Django core/google-login/ (userinfo)
OAUTH_SCOPE = "openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile"

_oauth_app: Optional[web.Application] = None
_oauth_runner: Optional[Any] = None
_oauth_site: Optional[Any] = None


def _login_html(base_url: str) -> str:
    """HTML that redirects to Google OAuth; callback returns to our /callback with fragment."""
    redirect_uri = f"{base_url}/callback"
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urllib.parse.urlencode({
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "token",
            "scope": OAUTH_SCOPE,
        })
    )
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Sign in – Project Logs MCP</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 420px; margin: 60px auto; padding: 20px; text-align: center; }}
    a {{ display: inline-block; margin-top: 16px; padding: 12px 24px; background: #1a73e8; color: #fff; text-decoration: none; border-radius: 6px; }}
    a:hover {{ background: #1557b0; }}
    p {{ color: #555; }}
  </style>
</head>
<body>
  <h1>Sign in with Google</h1>
  <p>You will be redirected to Google to sign in, then back here to complete authentication for the Project Logs MCP.</p>
  <a href="{auth_url}">Continue with Google</a>
</body>
</html>"""


def _callback_html(base_url: str) -> str:
    """HTML that runs in the browser: reads access_token from hash and sends to /store."""
    store_url = f"{base_url}/store"
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Completing sign-in…</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 420px; margin: 60px auto; padding: 20px; text-align: center; }}
    #msg {{ color: #555; }}
    .ok {{ color: #0d6832; }}
    .err {{ color: #c5221f; }}
  </style>
</head>
<body>
  <p id="msg">Completing sign-in…</p>
  <script>
    (function() {{
      var hash = window.location.hash.slice(1);
      var params = new URLSearchParams(hash);
      var accessToken = params.get('access_token');
      if (!accessToken) {{
        document.getElementById('msg').innerHTML = '<span class="err">No token received from Google. Try again.</span>';
        return;
      }}
      fetch('{store_url}?access_token=' + encodeURIComponent(accessToken))
        .then(function(r) {{ return r.text(); }})
        .then(function(html) {{
          document.open();
          document.write(html);
          document.close();
        }})
        .catch(function() {{
          document.getElementById('msg').innerHTML = '<span class="err">Could not complete sign-in. Check that the MCP server is still running.</span>';
        }});
    }})();
  </script>
</body>
</html>"""


def _success_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Sign-in complete</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 420px; margin: 60px auto; padding: 20px; text-align: center; }
    .ok { color: #0d6832; font-weight: 500; }
  </style>
</head>
<body>
  <p class="ok">Authentication complete.</p>
  <p>You can close this window and return to Cursor.</p>
</body>
</html>"""


def _error_html(message: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Sign-in failed</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 420px; margin: 60px auto; padding: 20px; text-align: center; }}
    .err {{ color: #c5221f; }}
  </style>
</head>
<body>
  <p class="err">{message}</p>
  <p>You can close this window and try again in Cursor.</p>
</body>
</html>"""


async def _handle_login(request: web.Request) -> web.Response:
    base = f"http://127.0.0.1:{request.app['port']}"
    html = _login_html(base)
    return web.Response(text=html, content_type="text/html")


async def _handle_callback(request: web.Request) -> web.Response:
    base = f"http://127.0.0.1:{request.app['port']}"
    html = _callback_html(base)
    return web.Response(text=html, content_type="text/html")


async def _handle_store(request: web.Request) -> web.Response:
    access_token = request.query.get("access_token")
    exchange = request.app.get("exchange_callback")
    if not exchange or not access_token:
        return web.Response(
            text=_error_html("Missing token or server not configured."),
            content_type="text/html"
        )
    try:
        result = await exchange(access_token)
        if result.get("status") == "success":
            return web.Response(text=_success_html(), content_type="text/html")
        return web.Response(
            text=_error_html(result.get("message", "Sign-in failed.")),
            content_type="text/html"
        )
    except Exception as e:
        return web.Response(
            text=_error_html(str(e)),
            content_type="text/html"
        )


def get_login_url() -> tuple[str, Optional[str]]:
    """
    Return (login_url, error_message).
    If error_message is set, login_url is still the URL to open but client_id may be missing.
    """
    base = f"http://127.0.0.1:{OAUTH_PORT}"
    if not GOOGLE_CLIENT_ID:
        return base + "/login", (
            "MCP_GOOGLE_CLIENT_ID is not set. Set it to your Google OAuth client ID (same as Django EXTERNAL_GOOGLE_CLIENT_ID) "
            "and ensure http://127.0.0.1:{} is added to Authorized redirect URIs in Google Cloud Console.".format(OAUTH_PORT)
        )
    return base + "/login", None


async def start_oauth_server(exchange_callback: Callable) -> Optional[str]:
    """
    Start the local OAuth server. exchange_callback(access_token) is async and returns
    a dict with status and optional message (e.g. from api_client.authenticate_with_google).
    Returns None if started, or an error string.
    """
    global _oauth_app, _oauth_runner, _oauth_site
    if web is None:
        return "aiohttp is required for browser sign-in (pip install aiohttp)."
    if _oauth_runner is not None:
        return None  # already running
    try:
        app = web.Application()
        app["port"] = OAUTH_PORT
        app["exchange_callback"] = exchange_callback
        app.router.add_get("/login", _handle_login)
        app.router.add_get("/callback", _handle_callback)
        app.router.add_get("/store", _handle_store)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", OAUTH_PORT)
        await site.start()
        _oauth_app = app
        _oauth_runner = runner
        _oauth_site = site
        return None
    except OSError as e:
        if "Address already in use" in str(e) or "Errno 48" in str(e):
            return None  # port in use, assume our server from a previous run
        return str(e)
