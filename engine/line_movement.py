"""Line movement helpers (bootstrap — extend when full pipeline is restored)."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class RecommendationRegistry:
    """Placeholder registry for recommendation history."""

    @classmethod
    def load(cls, *args: Any, **kwargs: Any) -> "RecommendationRegistry":
        return cls()

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []


def compute_line_move_fields(edge: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    """Return edge dict unchanged if no registry data is available."""
    return edge
