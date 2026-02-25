"""Tests for the feature flag domain loader (tools/__init__.py)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from fastmcp.tools.function_tool import FunctionTool


def _get_tool_names(mcp: FastMCP) -> list[str]:
    """Extract registered tool names from a FastMCP instance."""
    lp = mcp.local_provider
    return [
        comp.name for comp in lp._components.values() if isinstance(comp, FunctionTool)
    ]


class TestLoadDomains:
    def test_default_loads_timelogs(self) -> None:
        """Default ENABLED_DOMAINS loads timelogs."""
        import server as srv

        tool_names = _get_tool_names(srv.mcp)
        assert any(name.startswith("timelogs_") for name in tool_names)

    def test_empty_enabled_domains_exits(self) -> None:
        from tools import load_domains

        test_mcp = FastMCP(name="test")
        with patch.dict(os.environ, {"ENABLED_DOMAINS": ""}):
            with pytest.raises(SystemExit):
                load_domains(test_mcp)

    def test_unknown_domain_skipped(self) -> None:
        from tools import load_domains

        test_mcp = FastMCP(name="test")
        with patch.dict(os.environ, {"ENABLED_DOMAINS": "nonexistent"}):
            with pytest.raises(SystemExit):
                load_domains(test_mcp)

    def test_sensitive_domain_without_flag_exits(self) -> None:
        from tools import load_domains

        test_mcp = FastMCP(name="test")
        # Ensure ENABLE_SENSITIVE_DOMAINS is not set
        env = os.environ.copy()
        env.pop("ENABLE_SENSITIVE_DOMAINS", None)
        env["ENABLED_DOMAINS"] = "payroll"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(SystemExit):
                load_domains(test_mcp)

    def test_tool_count_with_timelogs_only(self) -> None:
        from tools import load_domains

        test_mcp = FastMCP(name="test")
        with patch.dict(os.environ, {"ENABLED_DOMAINS": "timelogs"}):
            loaded = load_domains(test_mcp)
        assert loaded == ["timelogs"]
        assert len(_get_tool_names(test_mcp)) == 11
