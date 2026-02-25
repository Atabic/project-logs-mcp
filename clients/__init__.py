"""Client registry for domain-specific ERP clients.

Provides get_registry() / set_registry() to replace the old module-level singleton.
Tests inject mocks via set_registry().
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clients._base import BaseERPClient
from clients.leaves import LeavesClient
from clients.timelogs import TimelogsClient

__all__ = ["ERPClientRegistry", "get_registry", "set_registry"]


@dataclass
class ERPClientRegistry:
    """Holds domain client instances. One registry per server lifecycle."""

    base: BaseERPClient
    timelogs: TimelogsClient = field(init=False)
    leaves: LeavesClient = field(init=False)

    def __post_init__(self) -> None:
        self.timelogs = TimelogsClient(self.base)
        self.leaves = LeavesClient(self.base)

    async def close(self) -> None:
        await self.base.close()


_registry: ERPClientRegistry | None = None


def get_registry() -> ERPClientRegistry:
    """Return the active registry, or raise if not initialized."""
    if _registry is None:
        raise RuntimeError("ERPClientRegistry not initialized. Server lifespan has not started.")
    return _registry


def set_registry(registry: ERPClientRegistry | None) -> None:
    """Set (or clear) the global registry. Used by lifespan and tests."""
    global _registry
    _registry = registry
