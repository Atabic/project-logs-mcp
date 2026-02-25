"""Cross-cutting security tests (auto-discovered via AST scanning).

These tests scan tools/*.py files for @mcp.tool-decorated functions and
verify security controls across ALL tools without manual registration.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

# Path to the tools directory
_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


def _is_mcp_tool_decorator(dec: ast.expr) -> bool:
    """Return True if *dec* looks like ``@mcp.tool`` or ``@mcp.tool(...)``."""
    # @mcp.tool
    if isinstance(dec, ast.Attribute) and dec.attr == "tool":
        return True
    # @tool (bare name, unlikely but covered)
    if isinstance(dec, ast.Name) and dec.id == "tool":
        return True
    # @mcp.tool(...) call form
    if isinstance(dec, ast.Call):
        func = dec.func
        if isinstance(func, ast.Attribute) and func.attr == "tool":
            return True
    return False


def _discover_tool_functions() -> list[tuple[str, str, ast.AsyncFunctionDef]]:
    """Discover all @mcp.tool async functions across tools/*.py files.

    Returns list of (filename, tool_name, ast_node) tuples.
    Tools are defined inside register() functions, so we walk into function bodies.
    """
    results: list[tuple[str, str, ast.AsyncFunctionDef]] = []
    for py_file in sorted(_TOOLS_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue  # skip __init__.py etc.
        with open(py_file, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        # Walk the entire AST to find @mcp.tool decorated functions
        # (they are nested inside register() functions)
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if any(_is_mcp_tool_decorator(dec) for dec in node.decorator_list):
                results.append((py_file.name, node.name, node))
    return results


def _discover_tool_names() -> list[str]:
    """Return names of all @mcp.tool functions across tools/*.py."""
    return [name for _, name, _ in _discover_tool_functions()]


def _discover_write_tool_names() -> set[str]:
    """Return names of tools that emit WRITE_OP."""
    names: set[str] = set()
    for _, name, node in _discover_tool_functions():
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and "WRITE_OP" in child.value
            ):
                names.add(name)
                break
    return names


class TestToolNamingConvention:
    """Verify every tool in tools/{domain}.py starts with {domain}_ prefix."""

    def test_all_tools_have_domain_prefix(self) -> None:
        tools = _discover_tool_functions()
        assert tools, "No @mcp.tool functions discovered in tools/*.py"
        for filename, tool_name, _ in tools:
            domain = filename.removesuffix(".py")
            assert tool_name.startswith(f"{domain}_"), (
                f"Tool '{tool_name}' in tools/{filename} must start with '{domain}_'"
            )


class TestSEC01NoEmailParam:
    """SEC-01: No tool accepts an 'email' parameter."""

    def test_no_tool_has_email_param(self) -> None:
        """Parse tools/*.py AST and check that no @mcp.tool function has an 'email' param."""
        violations: list[str] = []
        for _, tool_name, node in _discover_tool_functions():
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.arg == "email":
                    violations.append(tool_name)
        assert violations == [], (
            f"SEC-01 violation: these tools accept an 'email' parameter: {violations}"
        )

    def test_runtime_signatures_have_no_email(self) -> None:
        """Check actual registered tool signatures on the mcp instance."""
        from fastmcp.tools.function_tool import FunctionTool

        import server as srv

        tool_names = _discover_tool_names()
        assert tool_names, "No @mcp.tool functions discovered"

        # Build lookup of registered tool functions
        lp = srv.mcp.local_provider
        registered: dict[str, FunctionTool] = {
            comp.name: comp
            for comp in lp._components.values()
            if isinstance(comp, FunctionTool)
        }

        for name in tool_names:
            tool = registered.get(name)
            assert tool is not None, f"Tool {name} not registered on mcp"
            sig = inspect.signature(tool.fn)
            assert "email" not in sig.parameters, (
                f"SEC-01: {name} has an 'email' parameter"
            )


class TestSEC08SensitiveDomainGate:
    """SEC-08: payroll/invoices require ENABLE_SENSITIVE_DOMAINS=true."""

    def test_sensitive_domains_defined(self) -> None:
        from tools import SENSITIVE_DOMAINS

        assert "payroll" in SENSITIVE_DOMAINS
        assert "invoices" in SENSITIVE_DOMAINS
