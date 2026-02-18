#!/usr/bin/env python3
"""
MCP Server over HTTP/SSE – use this when you want a URL for AI apps (e.g. remote MCP).

Run with:
  python server_sse.py
  # or: python server_sse.py --port 8000

Then the MCP URL is: http://<host>:<port>/sse
Clients POST messages to /messages/ (session_id from the SSE endpoint event).

When deployed (MCP_SSE_PUBLIC_URL set), authenticate_via_erp_browser returns a URL;
opening it streams a headless Playwright browser so the user can log in to ERP.

Requires: pip install -r requirements-sse.txt
"""

import asyncio
import json
import os
import sys
from pathlib import Path

current_dir = Path(__file__).parent.resolve()
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

# Must import app from server (shared MCP server instance)
from server import app

from erp_browser_sessions import get_session, run_erp_browser_session, _browser_task_lock
from erp_browser_auth import _erp_base_url

def _get_send(request):
    """Get ASGI send callable from Starlette request."""
    if hasattr(request, "_send"):
        return request._send
    if hasattr(request, "send"):
        return request.send
    raise RuntimeError("Starlette Request has no send; upgrade starlette or use request._send")

async def handle_sse(request):
    from mcp.server.sse import SseServerTransport
    from starlette.responses import Response

    sse = request.app.state.sse_transport
    send = _get_send(request)
    async with sse.connect_sse(request.scope, request.receive, send) as streams:
        await app.run(
            streams[0],
            streams[1],
            app.create_initialization_options(),
        )
    return Response()


def _erp_browser_html(session_id: str) -> str:
    """HTML page that displays streamed Playwright screenshot and forwards input."""
    base = f"/erp-browser/{session_id}"
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ERP sign-in – Project Logs MCP</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: system-ui, sans-serif; margin: 0; background: #1a1a1a; color: #e0e0e0; min-height: 100vh; display: flex; flex-direction: column; align-items: center; padding: 16px; }}
    h1 {{ font-size: 1.1rem; font-weight: 600; margin-bottom: 8px; }}
    p {{ color: #999; font-size: 0.9rem; margin: 0 0 12px 0; text-align: center; max-width: 640px; }}
    #view {{ background: #000; border-radius: 8px; box-shadow: 0 4px 24px rgba(0,0,0,0.5); cursor: pointer; max-width: 100%; height: auto; display: block; }}
    #view:not([src]) {{ min-height: 360px; display: flex; align-items: center; justify-content: center; color: #666; }}
    #status {{ margin-top: 12px; padding: 12px; border-radius: 8px; font-size: 0.9rem; display: none; }}
    #status.success {{ display: block; background: #0d6832; color: #fff; }}
    #status.error {{ display: block; background: #c5221f; color: #fff; }}
    #status.loading {{ display: block; color: #999; }}
  </style>
</head>
<body>
  <h1>ERP sign-in</h1>
  <p>Live stream of the remote browser. <strong>Clicks and keystrokes are sent to the session</strong> (including Google login popups). Log in as usual; your token will be captured automatically.</p>
  <img id="view" alt="ERP browser stream" />
  <div id="status" class="loading">Connecting…</div>
  <script>
    (function() {{
      var sid = {json.dumps(session_id)};
      var base = {json.dumps(base)};
      var view = document.getElementById('view');
      var status = document.getElementById('status');

      function pollFrame() {{
        view.src = base + '/frame?t=' + Date.now();
      }}
      function pollStatus() {{
        fetch(base + '/status')
          .then(function(r) {{ return r.json(); }})
          .then(function(data) {{
            if (data.error) {{
              status.className = 'error';
              status.textContent = data.error;
              status.style.display = 'block';
              return;
            }}
            if (data.result) {{
              status.className = data.result.status === 'success' ? 'success' : 'error';
              status.textContent = data.result.status === 'success'
                ? 'Authentication complete. You can close this window and return to Cursor.'
                : (data.result.message || 'Something went wrong.');
              status.style.display = 'block';
              if (data.result.status === 'success') return;
            }}
            setTimeout(pollStatus, 500);
          }})
          .catch(function() {{ setTimeout(pollStatus, 1000); }});
      }}

      view.onload = function() {{
        status.textContent = 'Connected. Click and type in the view to log in (e.g. Google login).';
        status.className = '';
      }};
      view.onerror = function() {{
        status.className = 'loading';
        status.textContent = 'Waiting for browser…';
      }};

      view.onclick = function(e) {{
        e.preventDefault();
        e.stopPropagation();
        var rect = view.getBoundingClientRect();
        var x = (e.clientX - rect.left) * (1280 / rect.width);
        var y = (e.clientY - rect.top) * (720 / rect.height);
        fetch(base + '/input', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ type: 'click', x: Math.round(x), y: Math.round(y) }})
        }}).catch(function() {{}});
      }};
      view.onmousedown = view.onselectstart = function(e) {{ e.preventDefault(); }};
      document.onkeydown = function(e) {{
        if (document.activeElement && document.activeElement !== document.body) return;
        fetch(base + '/input', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ type: 'key', key: e.key }})
        }}).catch(function() {{}});
      }};

      setInterval(pollFrame, 120);
      pollStatus();
      pollFrame();
    }})();
  </script>
</body>
</html>"""


def create_starlette_app():
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from starlette.responses import PlainTextResponse, HTMLResponse, Response, JSONResponse
    from mcp.server.sse import SseServerTransport

    sse = SseServerTransport("/messages/")

    async def homepage(request):
        return PlainTextResponse(
            "Project Logs MCP (SSE). Connect to /sse for MCP over HTTP.\n"
            "Use this URL in AI apps that support remote MCP (e.g. MCP URL).",
            status_code=200,
        )

    async def erp_browser_page(request):
        session_id = request.path_params.get("session_id")
        if not session_id:
            return PlainTextResponse("Missing session", status_code=400)
        session = get_session(session_id)
        if not session:
            return PlainTextResponse("Session not found or expired.", status_code=404)
        async with _browser_task_lock:
            if not session.get("browser_task"):
                erp_url = _erp_base_url()
                session["browser_task"] = asyncio.create_task(
                    run_erp_browser_session(session_id, erp_url)
                )
        return HTMLResponse(_erp_browser_html(session_id))

    async def erp_browser_frame(request):
        session_id = request.path_params.get("session_id")
        session = get_session(session_id) if session_id else None
        if not session:
            return Response(status_code=404)
        shot = session.get("last_screenshot")
        if not shot:
            return Response(status_code=204)
        return Response(shot, media_type="image/png")

    async def erp_browser_input(request):
        session_id = request.path_params.get("session_id")
        session = get_session(session_id) if session_id else None
        if not session:
            return Response(status_code=404)
        try:
            body = await request.json()
            session.get("input_queue").put_nowait(body)
        except Exception:
            pass
        return Response(status_code=204)

    async def erp_browser_status(request):
        session_id = request.path_params.get("session_id")
        session = get_session(session_id) if session_id else None
        if not session:
            return Response(status_code=404)
        return JSONResponse({"result": session.get("result"), "error": session.get("error")})

    routes = [
        Route("/", endpoint=homepage),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Route("/erp-browser/{session_id}", endpoint=erp_browser_page, methods=["GET"]),
        Route("/erp-browser/{session_id}/frame", endpoint=erp_browser_frame, methods=["GET"]),
        Route("/erp-browser/{session_id}/input", endpoint=erp_browser_input, methods=["POST"]),
        Route("/erp-browser/{session_id}/status", endpoint=erp_browser_status, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
    ]
    starlette_app = Starlette(routes=routes)
    starlette_app.state.sse_transport = sse
    return starlette_app


def main():
    import uvicorn

    port = 8000
    if "--port" in sys.argv:
        i = sys.argv.index("--port")
        if i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    else:
        # Render, Railway, etc. set PORT
        port = int(os.environ.get("PORT", "8000"))

    host = os.environ.get("MCP_SSE_HOST", "0.0.0.0")
    print(f"MCP over SSE: http://{host}:{port}/sse", file=sys.stderr)
    uvicorn.run(
        create_starlette_app(),
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
