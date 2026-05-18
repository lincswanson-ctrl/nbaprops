"""Strict Pydantic models for loader row validation."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model with strict validation enabled."""

    model_config = ConfigDict(extra="forbid", strict=True)


class TeamRow(StrictModel):
    """Validated row for teams table."""

    team_id: str
    name: str
    abbreviation: str
    conference: str | None = None
    division: str | None = None


class PlayerRow(StrictModel):
    """Validated row for players table."""

    player_id: str
    name: str
    team_id: str | None = None
    position: str | None = None
    height: str | None = None
    weight: str | None = None
    birthdate: date | None = None
    draft_year: int | None = None


class GameRow(StrictModel):
    """Validated row for games table."""

    game_id: str
    date: date
    season: str
    home_team_id: str
    away_team_id: str
    home_score: int | None = None
    away_score: int | None = None
    pace: float | None = None
    total_possessions: float | None = None
    ot_periods: int | None = None
    vegas_total: float | None = None
    vegas_spread: float | None = None
    attendance: int | None = None


class PlayerGameStatsRow(StrictModel):
    """Validated row for player_game_stats table."""

    player_id: str
    game_id: str
    team_id: str | None = None
    minutes: float | None = None
    points: int | None = None
    rebounds: int | None = None
    assists: int | None = None
    steals: int | None = None
    blocks: int | None = None
    fga: int | None = None
    fgm: int | None = None
    fg3a: int | None = None
    fg3m: int | None = None
    fta: int | None = None
    ftm: int | None = None
    turnovers: int | None = None
    plus_minus: int | None = None
    usage_rate: float | None = None
    true_shooting: float | None = None
    starter: bool | None = None
    dnp_reason: str | None = None


class LineupRow(StrictModel):
    """Validated row for lineups table."""

    game_id: str
    period: int
    start_seconds: int
    end_seconds: int
    team_id: str
    player1_id: str | None = None
    player2_id: str | None = None
    player3_id: str | None = None
    player4_id: str | None = None
    player5_id: str | None = None
    possessions: float | None = None
    points_for: int | None = None
    points_against: int | None = None
