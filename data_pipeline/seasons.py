"""NBA season label helpers shared across scrapers, scripts and the dashboard."""

from __future__ import annotations

from datetime import date

# Calendar month on or after which a new NBA season label begins (July = offseason rollover).
SEASON_ROLLOVER_MONTH = 7


def infer_nba_season(target_date: date) -> str:
    """Infer the NBA season label for a calendar date.

    Args:
        target_date: Any calendar date.

    Returns:
        Season label such as ``"2026-27"``. Dates from July onward belong to the season
        starting that year; January-June dates belong to the season that started the
        previous year.
    """
    start_year = (
        target_date.year if target_date.month >= SEASON_ROLLOVER_MONTH else target_date.year - 1
    )
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def previous_season(season: str) -> str:
    """Return the season label immediately before ``season``.

    Args:
        season: Season label in ``YYYY-YY`` form, e.g. ``"2026-27"``.

    Returns:
        The prior season label, e.g. ``"2025-26"``.

    Raises:
        ValueError: If ``season`` is not a valid ``YYYY-YY`` label.
    """
    start_year = _season_start_year(season)
    return f"{start_year - 1}-{str(start_year)[-2:]}"


def current_nba_season() -> str:
    """Return the season label for today's local date."""
    return infer_nba_season(date.today())


def _season_start_year(season: str) -> int:
    parts = season.split("-")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        raise ValueError(f"invalid season label: {season!r}")
    start_year = int(parts[0])
    if len(parts[0]) != 4 or f"{(start_year + 1) % 100:02d}" != parts[1]:
        raise ValueError(f"invalid season label: {season!r}")
    return start_year
