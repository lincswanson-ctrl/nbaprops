"""Point-in-time walk-forward helpers for backtests (spec 07). Pure functions.

Every projection for a game on date D uses only games strictly before D.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict

from models.baseline.projection import (
    BASELINE_LOOKBACK_DAYS,
    BASELINE_MAX_GAMES,
    market_stat_values,
)

PROXY_LINE_GAMES = 10
STAT_COLUMNS = ("points", "rebounds", "assists", "fg3m", "steals", "blocks")


class PlayerGame(BaseModel):
    """One player's box score for one game."""

    model_config = ConfigDict(frozen=True)

    player_id: str
    game_id: str
    game_date: date
    played: bool
    stats: Mapping[str, float | None]


class HistoryIndex:
    """Per-player games sorted by date, supporting point-in-time lookups."""

    def __init__(self, games: Iterable[PlayerGame]) -> None:
        by_player: dict[str, list[PlayerGame]] = {}
        for game in games:
            by_player.setdefault(game.player_id, []).append(game)
        self._games = {
            pid: sorted(rows, key=lambda g: (g.game_date, g.game_id))
            for pid, rows in by_player.items()
        }
        self._dates = {pid: [g.game_date for g in rows] for pid, rows in self._games.items()}

    def prior_played(
        self,
        player_id: str,
        before: date,
        max_games: int = BASELINE_MAX_GAMES,
        lookback_days: int = BASELINE_LOOKBACK_DAYS,
    ) -> list[Mapping[str, float | None]]:
        """Stats of played games with ``before - lookback_days <= date < before``, newest first."""
        rows = self._games.get(player_id, [])
        end = bisect.bisect_left(self._dates.get(player_id, []), before)
        earliest = before - timedelta(days=lookback_days)
        result: list[Mapping[str, float | None]] = []
        for game in reversed(rows[:end]):
            if game.game_date < earliest or len(result) >= max_games:
                break
            if game.played:
                result.append(game.stats)
        return result

    def game(self, player_id: str, game_id: str) -> PlayerGame | None:
        """Return the player's row for ``game_id`` if present."""
        return next((g for g in self._games.get(player_id, []) if g.game_id == game_id), None)


def proxy_line(recent_values: Sequence[float], n: int = PROXY_LINE_GAMES) -> float | None:
    """Stand-in for a book line: floor of the last-``n`` simple average, plus 0.5.

    Books set most player lines near a recent average, so this places calibration test
    lines where real ones usually are. Returns ``None`` with no history.
    """
    window = list(recent_values[:n])
    if not window:
        return None
    return math.floor(sum(window) / len(window)) + 0.5


def actual_value(market: str, game: PlayerGame | None) -> float | None:
    """Final stat for ``market`` in ``game``; ``None`` if the player did not play."""
    if game is None or not game.played:
        return None
    values = market_stat_values(market, [game.stats])
    return values[0] if values else None
