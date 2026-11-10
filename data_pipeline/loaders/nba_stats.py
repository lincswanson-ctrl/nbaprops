"""Load raw NBA stats JSON files into Postgres."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from data_pipeline.config import get_settings
from data_pipeline.db import get_connection
from data_pipeline.loaders.models import GameRow, LineupRow, PlayerGameStatsRow, PlayerRow, TeamRow
from data_pipeline.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ScheduleLoadSummary:
    """Return type for scoreboard schedule loads."""

    files_loaded: int
    files_skipped: int
    games_upserted: int


class NBAStatsLoader:
    """Raw-json loader for NBA stats endpoints."""

    def __init__(self) -> None:
        self._raw_root = get_settings().raw_storage_path / "nba_stats"

    def load_scoreboard_schedule(self, season: str, game_date: date) -> ScheduleLoadSummary:
        """Load scoreboard schedule JSON into teams and games tables."""

        file_path = self._raw_root / "scoreboardv2" / season / f"{game_date.isoformat()}.json"
        if not file_path.exists():
            logger.warning("scoreboard_raw_missing", season=season, game_date=game_date.isoformat())
            return ScheduleLoadSummary(files_loaded=0, files_skipped=1, games_upserted=0)
        checksum = _checksum(file_path)
        if _already_loaded("scoreboardv2", game_date.isoformat(), checksum):
            return ScheduleLoadSummary(files_loaded=0, files_skipped=1, games_upserted=0)
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        games = _extract_games_from_scoreboard(payload, season)
        teams = _extract_teams_from_scoreboard(payload)
        if not games:
            return ScheduleLoadSummary(files_loaded=1, files_skipped=0, games_upserted=0)

        with get_connection() as conn:
            with conn.cursor() as cur:
                _upsert_teams(cur, teams)
                upserted_games = _upsert_games(cur, games)
                _upsert_load_log(
                    cur=cur,
                    endpoint="scoreboardv2",
                    natural_key=game_date.isoformat(),
                    raw_file_path=str(file_path),
                    raw_file_checksum=checksum,
                )
        return ScheduleLoadSummary(files_loaded=1, files_skipped=0, games_upserted=upserted_games)

    def load_season(self, season: str) -> int:
        """Load schedule and per-game player stats for an entire season."""

        loaded = self._load_scoreboards(season)
        loaded += self._load_boxscores(season)
        return loaded

    def _load_scoreboards(self, season: str) -> int:
        scoreboard_dirs = [self._raw_root / "scoreboardv2" / season, self._raw_root / "scoreboardv3" / season]
        loaded = 0
        for scoreboard_dir in scoreboard_dirs:
            if not scoreboard_dir.exists():
                continue
            for raw_file in sorted(scoreboard_dir.glob("*.json")):
                loaded += self.load_scoreboard_schedule(season, date.fromisoformat(raw_file.stem)).games_upserted
        return loaded

    def _load_boxscores(self, season: str) -> int:
        traditional_dir = self._raw_root / "boxscoretraditionalv2" / season
        advanced_dir = self._raw_root / "boxscoreadvancedv2" / season
        matchup_dir = self._raw_root / "boxscorematchupsv3" / season
        if not traditional_dir.exists():
            return 0
        loaded = 0
        for trad_file in sorted(traditional_dir.glob("*.json")):
            game_id = trad_file.stem
            checksum = _checksum(trad_file)
            if _already_loaded("boxscoretraditionalv2", game_id, checksum):
                continue
            advanced_payload: dict[str, Any] | None = None
            adv_file = advanced_dir / trad_file.name
            if adv_file.exists():
                advanced_payload = json.loads(adv_file.read_text(encoding="utf-8"))
            with get_connection() as conn:
                with conn.cursor() as cur:
                    payload = json.loads(trad_file.read_text(encoding="utf-8"))
                    players = _extract_players_from_boxscore(payload)
                    stats = _extract_player_stats_from_boxscore(payload, advanced_payload, game_id=game_id)
                    lineup_rows: list[LineupRow] = []
                    matchup_file = matchup_dir / trad_file.name
                    if matchup_file.exists():
                        matchup_payload = json.loads(matchup_file.read_text(encoding="utf-8"))
                        lineup_rows = _extract_lineups_from_matchups(matchup_payload, game_id=game_id)
                    if not stats:
                        continue
                    _upsert_players(cur, players)
                    rows = _upsert_player_game_stats(cur, stats)
                    _upsert_lineups(cur, lineup_rows)
                    _upsert_load_log(
                        cur=cur,
                        endpoint="boxscoretraditionalv2",
                        natural_key=game_id,
                        raw_file_path=str(trad_file),
                        raw_file_checksum=checksum,
                    )
                    loaded += rows
        return loaded


def _extract_games_from_scoreboard(payload: dict[str, Any], season: str) -> list[GameRow]:
    game_rows: list[GameRow] = []
    result_sets = payload.get("resultSets", [])
    by_name = {item.get("name"): item for item in result_sets if isinstance(item, dict)}
    game_header = by_name.get("GameHeader")
    if not game_header:
        return game_rows
    headers = game_header.get("headers", [])
    header_idx = {name: i for i, name in enumerate(headers)}
    for row in game_header.get("rowSet", []):
        game_date_raw = str(row[header_idx["GAME_DATE_EST"]])
        game_date = _parse_game_date(game_date_raw)
        game_rows.append(
            GameRow(
                game_id=str(row[header_idx["GAME_ID"]]),
                date=game_date,
                season=season,
                home_team_id=str(row[header_idx["HOME_TEAM_ID"]]),
                away_team_id=str(row[header_idx["VISITOR_TEAM_ID"]]),
                home_score=_safe_int(row[header_idx.get("PTS_HOME", -1)]),
                away_score=_safe_int(row[header_idx.get("PTS_AWAY", -1)]),
            )
        )
    return game_rows


def _extract_teams_from_scoreboard(payload: dict[str, Any]) -> list[TeamRow]:
    teams: dict[str, TeamRow] = {}
    result_sets = payload.get("resultSets", [])
    by_name = {item.get("name"): item for item in result_sets if isinstance(item, dict)}
    line_score = by_name.get("LineScore")
    if not line_score:
        return []
    headers = line_score.get("headers", [])
    header_idx = {name: i for i, name in enumerate(headers)}
    for row in line_score.get("rowSet", []):
        team_id = str(row[header_idx["TEAM_ID"]])
        teams[team_id] = TeamRow(
            team_id=team_id,
            name=str(row[header_idx["TEAM_NAME"]]),
            abbreviation=str(row[header_idx["TEAM_ABBREVIATION"]]),
        )
    return list(teams.values())


def _upsert_teams(cur: Any, teams: list[TeamRow]) -> None:
    if not teams:
        return
    cur.executemany(
        """
        INSERT INTO teams (team_id, name, abbreviation, conference, division)
        VALUES (%(team_id)s, %(name)s, %(abbreviation)s, %(conference)s, %(division)s)
        ON CONFLICT (team_id) DO UPDATE SET
            name = EXCLUDED.name,
            abbreviation = EXCLUDED.abbreviation,
            conference = EXCLUDED.conference,
            division = EXCLUDED.division
        """,
        [team.model_dump() for team in teams],
    )


def _upsert_players(cur: Any, players: list[PlayerRow]) -> None:
    if not players:
        return
    cur.executemany(
        """
        INSERT INTO players (
            player_id, name, team_id, position, height, weight, birthdate, draft_year
        )
        VALUES (
            %(player_id)s, %(name)s, %(team_id)s, %(position)s, %(height)s, %(weight)s, %(birthdate)s, %(draft_year)s
        )
        ON CONFLICT (player_id) DO UPDATE SET
            name = EXCLUDED.name,
            team_id = EXCLUDED.team_id,
            position = COALESCE(EXCLUDED.position, players.position),
            height = COALESCE(EXCLUDED.height, players.height),
            weight = COALESCE(EXCLUDED.weight, players.weight),
            birthdate = COALESCE(EXCLUDED.birthdate, players.birthdate),
            draft_year = COALESCE(EXCLUDED.draft_year, players.draft_year)
        """,
        [player.model_dump() for player in players],
    )


def _upsert_games(cur: Any, games: list[GameRow]) -> int:
    if not games:
        return 0
    cur.executemany(
        """
        INSERT INTO games (game_id, date, season, home_team_id, away_team_id, home_score, away_score)
        VALUES (
            %(game_id)s, %(date)s, %(season)s, %(home_team_id)s, %(away_team_id)s,
            %(home_score)s, %(away_score)s
        )
        ON CONFLICT (game_id) DO UPDATE SET
            date = EXCLUDED.date,
            season = EXCLUDED.season,
            home_team_id = EXCLUDED.home_team_id,
            away_team_id = EXCLUDED.away_team_id,
            home_score = EXCLUDED.home_score,
            away_score = EXCLUDED.away_score
        """,
        [game.model_dump() for game in games],
    )
    return len(games)


def _upsert_player_game_stats(cur: Any, stats: list[PlayerGameStatsRow]) -> int:
    cur.executemany(
        """
        INSERT INTO player_game_stats (
            player_id, game_id, team_id, minutes, points, rebounds, assists, steals, blocks,
            fga, fgm, fg3a, fg3m, fta, ftm, turnovers, plus_minus, usage_rate, true_shooting, starter, dnp_reason
        )
        VALUES (
            %(player_id)s, %(game_id)s, %(team_id)s, %(minutes)s, %(points)s, %(rebounds)s, %(assists)s, %(steals)s, %(blocks)s,
            %(fga)s, %(fgm)s, %(fg3a)s, %(fg3m)s, %(fta)s, %(ftm)s, %(turnovers)s, %(plus_minus)s, %(usage_rate)s,
            %(true_shooting)s, %(starter)s, %(dnp_reason)s
        )
        ON CONFLICT (player_id, game_id) DO UPDATE SET
            team_id = EXCLUDED.team_id,
            minutes = EXCLUDED.minutes,
            points = EXCLUDED.points,
            rebounds = EXCLUDED.rebounds,
            assists = EXCLUDED.assists,
            steals = EXCLUDED.steals,
            blocks = EXCLUDED.blocks,
            fga = EXCLUDED.fga,
            fgm = EXCLUDED.fgm,
            fg3a = EXCLUDED.fg3a,
            fg3m = EXCLUDED.fg3m,
            fta = EXCLUDED.fta,
            ftm = EXCLUDED.ftm,
            turnovers = EXCLUDED.turnovers,
            plus_minus = EXCLUDED.plus_minus,
            usage_rate = EXCLUDED.usage_rate,
            true_shooting = EXCLUDED.true_shooting,
            starter = EXCLUDED.starter,
            dnp_reason = EXCLUDED.dnp_reason
        """,
        [row.model_dump() for row in stats],
    )
    return len(stats)


def _upsert_lineups(cur: Any, lineups: list[LineupRow]) -> None:
    if not lineups:
        return
    cur.executemany(
        """
        INSERT INTO lineups (
            game_id, period, start_seconds, end_seconds, team_id,
            player1_id, player2_id, player3_id, player4_id, player5_id,
            possessions, points_for, points_against
        )
        VALUES (
            %(game_id)s, %(period)s, %(start_seconds)s, %(end_seconds)s, %(team_id)s,
            %(player1_id)s, %(player2_id)s, %(player3_id)s, %(player4_id)s, %(player5_id)s,
            %(possessions)s, %(points_for)s, %(points_against)s
        )
        ON CONFLICT (game_id, period, start_seconds, team_id) DO UPDATE SET
            end_seconds = EXCLUDED.end_seconds,
            player1_id = EXCLUDED.player1_id,
            player2_id = EXCLUDED.player2_id,
            player3_id = EXCLUDED.player3_id,
            player4_id = EXCLUDED.player4_id,
            player5_id = EXCLUDED.player5_id,
            possessions = EXCLUDED.possessions,
            points_for = EXCLUDED.points_for,
            points_against = EXCLUDED.points_against
        """,
        [row.model_dump() for row in lineups],
    )


def _upsert_load_log(
    *,
    cur: Any,
    endpoint: str,
    natural_key: str,
    raw_file_path: str,
    raw_file_checksum: str,
) -> None:
    cur.execute(
        """
        INSERT INTO load_log (source, endpoint, natural_key, raw_file_path, raw_file_checksum)
        VALUES ('nba_stats', %(endpoint)s, %(natural_key)s, %(raw_file_path)s, %(raw_file_checksum)s)
        ON CONFLICT (source, endpoint, natural_key) DO UPDATE SET
            raw_file_path = EXCLUDED.raw_file_path,
            raw_file_checksum = EXCLUDED.raw_file_checksum,
            loaded_at = NOW()
        """,
        {
            "endpoint": endpoint,
            "natural_key": natural_key,
            "raw_file_path": raw_file_path,
            "raw_file_checksum": raw_file_checksum,
        },
    )


def _already_loaded(endpoint: str, natural_key: str, checksum: str) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM load_log
                WHERE source = 'nba_stats'
                  AND endpoint = %(endpoint)s
                  AND natural_key = %(natural_key)s
                  AND raw_file_checksum = %(checksum)s
                LIMIT 1
                """,
                {"endpoint": endpoint, "natural_key": natural_key, "checksum": checksum},
            )
            return cur.fetchone() is not None


def _extract_players_from_boxscore(payload: dict[str, Any]) -> list[PlayerRow]:
    result_sets = payload.get("resultSets", [])
    player_stats = next(
        (result for result in result_sets if isinstance(result, dict) and result.get("name") == "PlayerStats"),
        None,
    )
    if player_stats is None:
        return []
    headers = player_stats.get("headers", [])
    idx = {name: i for i, name in enumerate(headers)}
    rows: list[PlayerRow] = []
    for row in player_stats.get("rowSet", []):
        player_id = str(row[idx["PLAYER_ID"]]) if "PLAYER_ID" in idx else None
        if not player_id:
            continue
        rows.append(
            PlayerRow(
                player_id=player_id,
                name=str(row[idx["PLAYER_NAME"]]) if "PLAYER_NAME" in idx else f"Player {player_id}",
                team_id=str(row[idx["TEAM_ID"]]) if "TEAM_ID" in idx else None,
                position=str(row[idx["START_POSITION"]]) if "START_POSITION" in idx else None,
            )
        )
    return rows


def _extract_player_stats_from_boxscore(
    payload: dict[str, Any],
    advanced_payload: dict[str, Any] | None,
    *,
    game_id: str,
) -> list[PlayerGameStatsRow]:
    advanced_map = _advanced_stats_map(advanced_payload or {})
    result_sets = payload.get("resultSets", [])
    player_stats = next(
        (result for result in result_sets if isinstance(result, dict) and result.get("name") == "PlayerStats"),
        None,
    )
    if player_stats is None:
        return []
    headers = player_stats.get("headers", [])
    idx = {name: i for i, name in enumerate(headers)}
    rows: list[PlayerGameStatsRow] = []
    for row in player_stats.get("rowSet", []):
        player_id = str(row[idx["PLAYER_ID"]])
        extra = advanced_map.get(player_id, {})
        minutes_raw = str(row[idx["MIN"]]) if "MIN" in idx else "0:00"
        dnp_reason = str(row[idx["COMMENT"]]).strip() if "COMMENT" in idx and row[idx["COMMENT"]] else None
        rows.append(
            PlayerGameStatsRow(
                player_id=player_id,
                game_id=game_id,
                team_id=str(row[idx["TEAM_ID"]]) if "TEAM_ID" in idx else None,
                minutes=_minutes_to_decimal(minutes_raw),
                points=_safe_int(row[idx["PTS"]]) if "PTS" in idx else None,
                rebounds=_safe_int(row[idx["REB"]]) if "REB" in idx else None,
                assists=_safe_int(row[idx["AST"]]) if "AST" in idx else None,
                steals=_safe_int(row[idx["STL"]]) if "STL" in idx else None,
                blocks=_safe_int(row[idx["BLK"]]) if "BLK" in idx else None,
                fga=_safe_int(row[idx["FGA"]]) if "FGA" in idx else None,
                fgm=_safe_int(row[idx["FGM"]]) if "FGM" in idx else None,
                fg3a=_safe_int(row[idx["FG3A"]]) if "FG3A" in idx else None,
                fg3m=_safe_int(row[idx["FG3M"]]) if "FG3M" in idx else None,
                fta=_safe_int(row[idx["FTA"]]) if "FTA" in idx else None,
                ftm=_safe_int(row[idx["FTM"]]) if "FTM" in idx else None,
                turnovers=_safe_int(row[idx["TO"]]) if "TO" in idx else None,
                plus_minus=_safe_int(row[idx["PLUS_MINUS"]]) if "PLUS_MINUS" in idx else None,
                usage_rate=extra.get("usage_rate"),
                true_shooting=extra.get("true_shooting"),
                starter=bool(str(row[idx["START_POSITION"]]).strip()) if "START_POSITION" in idx else None,
                dnp_reason=dnp_reason,
            )
        )
    return rows


def _advanced_stats_map(payload: dict[str, Any]) -> dict[str, dict[str, float | None]]:
    result_sets = payload.get("resultSets", [])
    player_stats = next(
        (result for result in result_sets if isinstance(result, dict) and result.get("name") == "PlayerStats"),
        None,
    )
    if player_stats is None:
        return {}
    headers = player_stats.get("headers", [])
    idx = {name: i for i, name in enumerate(headers)}
    out: dict[str, dict[str, float | None]] = {}
    for row in player_stats.get("rowSet", []):
        player_id = str(row[idx["PLAYER_ID"]]) if "PLAYER_ID" in idx else None
        if not player_id:
            continue
        out[player_id] = {
            "usage_rate": _safe_float(row[idx["USG_PCT"]]) if "USG_PCT" in idx else None,
            "true_shooting": _safe_float(row[idx["TS_PCT"]]) if "TS_PCT" in idx else None,
        }
    return out


def _extract_lineups_from_matchups(payload: dict[str, Any], *, game_id: str) -> list[LineupRow]:
    result_sets = payload.get("resultSets", [])
    if not result_sets:
        return []
    matchup = next(
        (
            result
            for result in result_sets
            if isinstance(result, dict) and str(result.get("name", "")).lower().startswith("game")
        ),
        None,
    )
    if matchup is None:
        return []
    headers = matchup.get("headers", [])
    idx = {name: i for i, name in enumerate(headers)}
    rows: list[LineupRow] = []
    for row in matchup.get("rowSet", []):
        team_id = _pick_value(row, idx, ["TEAM_ID", "OFF_TEAM_ID", "DEF_TEAM_ID"])
        if team_id is None:
            continue
        player_ids = [
            _pick_value(row, idx, [f"PLAYER{i}_ID", f"OFF_PLAYER{i}_ID", f"DEF_PLAYER{i}_ID"])
            for i in range(1, 6)
        ]
        start_sec = _safe_int(_pick_value(row, idx, ["START_SEC", "START_SECONDS", "START_TIME"])) or 0
        end_sec = _safe_int(_pick_value(row, idx, ["END_SEC", "END_SECONDS", "END_TIME"])) or 0
        rows.append(
            LineupRow(
                game_id=game_id,
                period=_safe_int(_pick_value(row, idx, ["PERIOD"])) or 1,
                start_seconds=start_sec,
                end_seconds=end_sec,
                team_id=str(team_id),
                player1_id=str(player_ids[0]) if player_ids[0] is not None else None,
                player2_id=str(player_ids[1]) if player_ids[1] is not None else None,
                player3_id=str(player_ids[2]) if player_ids[2] is not None else None,
                player4_id=str(player_ids[3]) if player_ids[3] is not None else None,
                player5_id=str(player_ids[4]) if player_ids[4] is not None else None,
                possessions=_safe_float(_pick_value(row, idx, ["POSS", "POSSESSIONS"])),
                points_for=_safe_int(_pick_value(row, idx, ["PTS", "POINTS_FOR", "OFF_PTS"])),
                points_against=_safe_int(_pick_value(row, idx, ["PTS_AGAINST", "DEF_PTS"])),
            )
        )
    return rows


def _parse_game_date(value: str) -> date:
    if "T" in value:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").date()
    return date.fromisoformat(value)


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _minutes_to_decimal(value: str) -> float | None:
    if not value:
        return None
    pieces = value.split(":")
    if len(pieces) != 2:
        return _safe_float(value)
    try:
        minutes = int(pieces[0])
        seconds = int(pieces[1])
    except ValueError:
        return None
    return round(minutes + (seconds / 60.0), 3)


def _pick_value(row: list[Any], idx: dict[str, int], keys: list[str]) -> Any:
    for key in keys:
        position = idx.get(key)
        if position is None:
            continue
        if position >= len(row):
            continue
        value = row[position]
        if value not in (None, ""):
            return value
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load NBA stats JSON into Postgres")
    parser.add_argument("--season", required=True, help="NBA season label, e.g. 2024-25")
    parser.add_argument("--game-id", help="Reserved for future per-game reload support")
    parser.add_argument("--reload-all", action="store_true")
    return parser


def _run_cli() -> None:
    args = _build_parser().parse_args()
    loaded = NBAStatsLoader().load_season(args.season)
    logger.info("nba_stats_load_complete", season=args.season, games_upserted=loaded)


if __name__ == "__main__":
    _run_cli()
