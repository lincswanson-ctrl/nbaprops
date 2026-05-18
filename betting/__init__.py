"""Betting helpers.

Imports are lazy so ``import betting`` never pulls optional submodules before PYTHONPATH
is configured (e.g. pytest collection).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "american_to_implied_prob",
    "implied_prob_to_american",
    "vig_remove_power",
    "vig_remove_proportional",
]


def __getattr__(name: str) -> Any:
    """Load vig helpers on first attribute access."""

    if name in __all__:
        from betting import vig as _vig

        return getattr(_vig, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
