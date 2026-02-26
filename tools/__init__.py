"""Domain tool module loader.

Imports all domain tool modules listed in AVAILABLE_DOMAINS and calls
their register(mcp) function. Each module must expose a register(mcp) callable.

Authorization is handled by the ERP backend — the MCP server simply proxies
requests, so no feature-flag gating is needed here.
"""

from __future__ import annotations

import importlib
import logging

from fastmcp import FastMCP

logger = logging.getLogger("erp_mcp.server")

__all__ = ["load_domains"]

# Map domain name -> module path (relative import within the package).
AVAILABLE_DOMAINS: dict[str, str] = {
    "timelogs": "tools.timelogs",
    "leaves": "tools.leaves",
}


def load_domains(mcp: FastMCP) -> list[str]:
    """Import and register tool modules for every domain in AVAILABLE_DOMAINS.

    Modules that fail to import are logged and skipped.
    Returns list of successfully loaded domain names.
    """
    loaded: list[str] = []
    for domain, module_path in AVAILABLE_DOMAINS.items():
        try:
            module = importlib.import_module(module_path)
            module.register(mcp)
        except Exception:
            logger.warning(
                "Failed to load domain '%s' from '%s'",
                domain,
                module_path,
                exc_info=True,
            )
            continue
        loaded.append(domain)
        logger.info("Loaded domain: %s", domain)

    if len(loaded) < len(AVAILABLE_DOMAINS):
        failed = set(AVAILABLE_DOMAINS) - set(loaded)
        logger.error("Failed to load domain modules: %s", ", ".join(sorted(failed)))

    return loaded
