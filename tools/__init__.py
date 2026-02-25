"""Feature flag loader for domain tool modules.

Reads ENABLED_DOMAINS env var (comma-separated) and imports only those
domain tool modules. Each module must expose a register(mcp) function.
"""

from __future__ import annotations

import importlib
import logging
import os

from fastmcp import FastMCP

logger = logging.getLogger("erp_mcp.server")

__all__ = ["load_domains"]

# Map domain name -> module path (relative import within the package).
AVAILABLE_DOMAINS: dict[str, str] = {
    "timelogs": "tools.timelogs",
    "leaves": "tools.leaves",
}

# Domains that require ENABLE_SENSITIVE_DOMAINS=true (SEC-08).
SENSITIVE_DOMAINS: frozenset[str] = frozenset({"payroll", "invoices"})


def load_domains(mcp: FastMCP) -> list[str]:
    """Import and register tool modules for each enabled domain.

    Reads ENABLED_DOMAINS env var (comma-separated, default "timelogs").
    Raises SystemExit if no valid domains are enabled or if a sensitive
    domain is requested without ENABLE_SENSITIVE_DOMAINS=true.

    Returns list of loaded domain names.
    """
    raw = os.environ.get("ENABLED_DOMAINS", "timelogs")
    requested = [d.strip().lower() for d in raw.split(",") if d.strip()]

    if not requested:
        logger.critical("ENABLED_DOMAINS is empty — at least one domain must be enabled")
        raise SystemExit(1)

    # SEC-08: gate sensitive domains
    sensitive_enabled = os.environ.get("ENABLE_SENSITIVE_DOMAINS", "").lower().strip() == "true"
    for domain in requested:
        if domain in SENSITIVE_DOMAINS and not sensitive_enabled:
            logger.critical("Domain '%s' requires ENABLE_SENSITIVE_DOMAINS=true (SEC-08)", domain)
            raise SystemExit(1)

    loaded: list[str] = []
    for domain in requested:
        module_path = AVAILABLE_DOMAINS.get(domain)
        if module_path is None:
            logger.warning(
                "Unknown domain '%s' in ENABLED_DOMAINS — skipping. Available: %s",
                domain,
                sorted(AVAILABLE_DOMAINS.keys()),
            )
            continue

        module = importlib.import_module(module_path)
        module.register(mcp)
        loaded.append(domain)
        logger.info("Loaded domain: %s", domain)

    if not loaded:
        logger.critical("No valid domains loaded from ENABLED_DOMAINS=%r", raw)
        raise SystemExit(1)

    return loaded
