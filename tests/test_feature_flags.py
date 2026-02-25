"""Tests for the domain tool loader (tools/__init__.py)."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools.function_tool import FunctionTool


def _get_tool_names(mcp: FastMCP) -> list[str]:
    """Extract registered tool names from a FastMCP instance."""
    lp = mcp.local_provider
    return [comp.name for comp in lp._components.values() if isinstance(comp, FunctionTool)]


class TestLoadDomains:
    def test_default_loads_timelogs(self) -> None:
        """All available domains (timelogs, leaves) are loaded at startup."""
        import server as srv

        tool_names = _get_tool_names(srv.mcp)
        assert any(name.startswith("timelogs_") for name in tool_names)
        assert any(name.startswith("leaves_") for name in tool_names)

    def test_tool_count(self) -> None:
        """All 20 tools (11 timelogs + 9 leaves) are registered."""
        from tools import load_domains

        test_mcp = FastMCP(name="test")
        loaded = load_domains(test_mcp)
        assert loaded == ["timelogs", "leaves"]
        assert len(_get_tool_names(test_mcp)) == 20
