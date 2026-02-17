#!/usr/bin/env python3
"""
MCP Server over HTTP/SSE – use this when you want a URL for AI apps (e.g. remote MCP).

Run with:
  python server_sse.py
  # or: python server_sse.py --port 8000

Then the MCP URL is: http://<host>:<port>/sse
Clients POST messages to /messages/ (session_id from the SSE endpoint event).

Requires: pip install -r requirements-sse.txt
"""

import os
import sys
from pathlib import Path

current_dir = Path(__file__).parent.resolve()
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

# Must import app from server (shared MCP server instance)
from server import app

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


def create_starlette_app():
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from starlette.responses import PlainTextResponse
    from mcp.server.sse import SseServerTransport

    sse = SseServerTransport("/messages/")

    async def homepage(request):
        return PlainTextResponse(
            "Project Logs MCP (SSE). Connect to /sse for MCP over HTTP.\n"
            "Use this URL in AI apps that support remote MCP (e.g. MCP URL).",
            status_code=200,
        )

    routes = [
        Route("/", endpoint=homepage),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
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
