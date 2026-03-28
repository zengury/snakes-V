"""
Agent MCP Server — external gateway (:8090).

This is the ONLY external port. All human interaction flows through here.
Internal servers (8080-8088) bind to 127.0.0.1 only.

Phase 5 will wire up a real MCP/SSE server. For now this module exposes
the four agent tools as plain async functions so they can be called
directly or wrapped by any ASGI/SSE framework.
"""

from __future__ import annotations

from typing import Any, Dict

# Lazy import: ManastoneAgent is only instantiated when the server is started.
_agent: Any = None


def get_agent():
    """Return the shared ManastoneAgent instance (must call init_agent first)."""
    if _agent is None:
        raise RuntimeError("Agent not initialised — call init_agent() first")
    return _agent


def init_agent(robot_id: str = "g1", **kwargs) -> Any:
    """Create and store the singleton ManastoneAgent."""
    global _agent
    from manastone.agent.agent import ManastoneAgent

    _agent = ManastoneAgent(robot_id=robot_id, **kwargs)
    return _agent


# ---------------------------------------------------------------------------
# The four MCP tools as plain async functions
# ---------------------------------------------------------------------------

async def tool_ask(question: str) -> Dict[str, Any]:
    """Ask the agent a question. Returns {answer: str}."""
    agent = get_agent()
    answer = await agent.ask(question)
    return {"answer": answer}


async def tool_command(instruction: str) -> Dict[str, Any]:
    """Send a command instruction to the agent. Returns the intent result dict."""
    agent = get_agent()
    return await agent.command(instruction)


async def tool_status() -> Dict[str, Any]:
    """Return comprehensive agent/robot status."""
    agent = get_agent()
    return await agent.status()


async def tool_teach(insight: str) -> Dict[str, Any]:
    """Store a human insight into the agent's semantic memory."""
    agent = get_agent()
    return await agent.teach(insight)


# ---------------------------------------------------------------------------
# Optional: minimal HTTP server (no extra deps beyond stdlib)
# Run with: python -m manastone.agent.mcp_interface
# ---------------------------------------------------------------------------

def _make_app():
    """Build a minimal JSON-over-HTTP ASGI app (no framework needed)."""
    import json

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return

        path = scope["path"]
        method = scope["method"]

        body = b""
        while True:
            event = await receive()
            body += event.get("body", b"")
            if not event.get("more_body"):
                break

        payload: Dict[str, Any] = {}
        if body:
            try:
                payload = json.loads(body)
            except Exception:
                pass

        result: Any = {"error": "not found"}
        status = 200

        try:
            if method == "POST" and path == "/ask":
                result = await tool_ask(payload.get("question", ""))
            elif method == "POST" and path == "/command":
                result = await tool_command(payload.get("instruction", ""))
            elif method in ("GET", "POST") and path == "/status":
                result = await tool_status()
            elif method == "POST" and path == "/teach":
                result = await tool_teach(payload.get("insight", ""))
            else:
                status = 404
                result = {"error": f"Unknown route {method} {path}"}
        except Exception as exc:
            status = 500
            result = {"error": str(exc)}

        body_out = json.dumps(result, default=str).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body_out)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body_out})

    return app


if __name__ == "__main__":
    import uvicorn  # type: ignore

    init_agent()
    uvicorn.run(_make_app(), host="0.0.0.0", port=8090)
