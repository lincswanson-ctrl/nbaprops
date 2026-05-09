"""Artifact loading for the Streamlit dashboard (bootstrap — expand with full pipeline)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Literal

import structlog

from engine.line_movement import compute_line_move_fields

logger = structlog.get_logger(__name__)

DEFAULT_OFFICIAL_CARD_SIZE: int | None = None
Confidence = Literal["high", "medium", "low"]
CONFIDENCE_LEVELS: tuple[Confidence, ...] = ("high", "medium", "low")
UNKNOWN_GAME_LABEL = "Unknown game"
_TEAM_MONEYLINE_MARKETS = frozenset({"team_moneyline", "moneyline"})


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dict_record(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Expected dict record")
    return dict(item)


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return list(value)


def _normalized_player(edge: dict[str, Any]) -> str:
    name = str(edge.get("player", edge.get("player_name", ""))).strip().lower()
    return name


def _normalize_line_key(line: Any) -> str:
    if line is None:
        return ""
    if isinstance(line, (int, float)):
        return str(float(line))
    text = str(line).strip()
    try:
        return str(float(text))
    except ValueError:
        return text.lower()


def ledger_official_edge_key(edge: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    pid = edge.get("player_id")
    if pid is None or pid == "":
        player_key = _normalized_player(edge)
    else:
        try:
            player_key = str(int(pid))
        except (TypeError, ValueError):
            player_key = _normalized_player(edge)
    return (
        str(edge.get("game_id", "")).strip(),
        player_key,
        str(edge.get("market", "")).strip().lower(),
        _normalize_line_key(edge.get("line")),
        str(edge.get("side", "")).strip().lower(),
        str(edge.get("sportsbook", edge.get("book", ""))).strip().lower(),
    )


def _is_player_prop_edge(edge: dict[str, Any]) -> bool:
    market = str(edge.get("market", "")).strip().lower()
    if market in _TEAM_MONEYLINE_MARKETS:
        return False
    if edge.get("team") and edge.get("opponent") and not _normalized_player(edge):
        return False
    return True


def _is_bettable_keep(edge: dict[str, Any]) -> bool:
    return (
        str(edge.get("decision", "")).strip().lower() == "keep"
        and bool(edge.get("bettable", False))
        and not bool(edge.get("research_only", False))
    )


def filtered_card_edges(
    edges: list[dict[str, Any]],
    *,
    card_size: int | None = None,
) -> list[dict[str, Any]]:
    rows = sorted(
        [e for e in edges if _is_bettable_keep(e)],
        key=_card_pick_sort_tuple,
        reverse=True,
    )
    if card_size is None:
        return rows
    return rows[:card_size]


def _final_grade_rank(grade: str) -> int:
    order = ["D", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+"]
    g = str(grade).strip().upper()
    return order.index(g) if g in order else -1


def _edge_sort_key(edge: dict[str, Any]) -> float:
    try:
        return float(edge.get("filtered_edge", edge.get("edge", 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _final_score_sort_component(edge: dict[str, Any]) -> float:
    raw = edge.get("final_score")
    if raw is None:
        return float("-inf")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float("-inf")


def _card_pick_sort_tuple(edge: dict[str, Any]) -> tuple[int, float, float]:
    rank = _final_grade_rank(str(edge.get("final_grade") or "D"))
    return (rank, _final_score_sort_component(edge), _edge_sort_key(edge))


def official_card_edge_keys(
    game_date: str,
    data_root: Path = Path("data"),
    *,
    card_size: int | None = DEFAULT_OFFICIAL_CARD_SIZE,
) -> set[tuple[str, str, str, str, str, str]]:
    edges = load_all_filtered_player_prop_edges(game_date, data_root)
    if not edges:
        return set()
    card = filtered_card_edges(edges, card_size=card_size)
    return {ledger_official_edge_key(e) for e in card}


def load_filtered_today_payload(game_date: str, data_root: Path = Path("data")) -> dict[str, Any]:
    path = data_root / "edges" / f"filtered_edges_{game_date}.json"
    if not path.exists():
        return {}
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object in {path}")
    return payload


def load_all_filtered_player_prop_edges(
    game_date: str,
    data_root: Path = Path("data"),
) -> list[dict[str, Any]]:
    payload = load_filtered_today_payload(game_date, data_root)
    raw_edges = payload.get("edges", [])
    if not isinstance(raw_edges, list):
        return []
    edges = [_dict_record(x) for x in raw_edges if isinstance(x, dict)]
    out: list[dict[str, Any]] = []
    for e in edges:
        if _is_player_prop_edge(e):
            out.append(compute_line_move_fields(dict(e)))
    return out


def load_today_edges(game_date: str, data_root: Path = Path("data")) -> list[dict[str, Any]]:
    path = data_root / "edges" / f"edges_{game_date}.json"
    if not path.exists():
        return []
    payload = _load_json(path)
    if not isinstance(payload, list):
        raise ValueError(f"Expected list in {path}")
    return [_dict_record(x) for x in payload if isinstance(x, dict)]


def latest_edge_date(data_root: Path = Path("data")) -> str:
    pattern = "filtered_edges_*.json"
    dates: list[str] = []
    root = data_root / "edges"
    if not root.exists():
        return date.today().isoformat()
    for path in root.glob(pattern):
        stem = path.stem.removeprefix("filtered_edges_")
        try:
            date.fromisoformat(stem)
        except ValueError:
            continue
        dates.append(stem)
    return max(dates) if dates else date.today().isoformat()


def row_type(row: dict[str, Any]) -> str:
    raw = row.get("bet_type") or row.get("type") or row.get("market_type") or ""
    value = str(raw).strip().lower()
    if value in {"player_prop", "player_props"}:
        return "player_prop"
    if value in {"team_moneyline", "moneyline"}:
        return "team_moneyline"
    if str(row.get("market", "")).strip().lower() == "team_moneyline":
        return "team_moneyline"
    return value or "player_prop"


def resolve_official_card(
    row: dict[str, Any],
    game_date: str,
    data_root: Path = Path("data"),
    *,
    card_size: int | None = DEFAULT_OFFICIAL_CARD_SIZE,
) -> bool:
    kind = row_type(row)
    if kind == "team_moneyline":
        return bool(row.get("bettable")) and not bool(row.get("research_only"))
    if kind != "player_prop":
        return False
    if bool(row.get("research_only")):
        return False
    keys = official_card_edge_keys(game_date, data_root, card_size=card_size)
    if keys:
        return ledger_official_edge_key(row) in keys
    return bool(row.get("official_card")) or (
        _player_prop_official_marker(row) and bool(row.get("bettable"))
    )


def _player_prop_official_marker(row: dict[str, Any]) -> bool:
    return bool(row.get("official_card", True))


def row_passes_research_visibility(row: dict[str, Any], *, show_research_raw: bool) -> bool:
    if show_research_raw:
        return True
    return not bool(row.get("research_only", False))


def official_card_research_lab_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [e for e in edges if _is_research_lab_official_pick(e)],
        key=_edge_sort_key,
        reverse=True,
    )


def _is_research_lab_official_pick(edge: dict[str, Any]) -> bool:
    if not bool(edge.get("bettable", False)):
        return False
    if bool(edge.get("research_only", False)):
        return False
    if "official_card" in edge:
        return bool(edge.get("official_card", False))
    return True


def _is_exposure_cap_edge(edge: dict[str, Any]) -> bool:
    tags = {str(t) for t in _list(edge.get("risk_tags"))}
    return "exposure_cap" in tags


def rejected_filter_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [e for e in edges if not _is_bettable_keep(e) and not _is_exposure_cap_edge(e)],
        key=_edge_sort_key,
        reverse=True,
    )


def card_cap_removed_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [e for e in edges if _is_exposure_cap_edge(e)],
        key=_edge_sort_key,
        reverse=True,
    )


def filtered_card_confidence_value(edge: dict[str, Any]) -> str:
    fc = edge.get("filtered_confidence")
    if fc is not None and str(fc).strip():
        return str(fc).strip()
    return str(edge.get("confidence", "") or "").strip()


def edge_confidence_display(edge: dict[str, Any]) -> str:
    return filtered_card_confidence_value(edge).title()


def _outcome_key(value: Any) -> str:
    return str(value).strip().lower()


def _safe_divide(num: float, den: float) -> float:
    if den == 0.0:
        return 0.0
    return num / den


def is_settled_graded_row(bet: dict[str, Any]) -> bool:
    o = _outcome_key(bet.get("outcome"))
    return o in {"win", "loss", "push"}


def final_grade_rank(grade: str) -> int:
    return _final_grade_rank(grade)


def _stake_units(bet: dict[str, Any]) -> float:
    v = bet.get("stake_units")
    return float(v) if v is not None else 1.0


def _profit_units(bet: dict[str, Any]) -> float:
    if bet.get("profit_units") is not None:
        return float(bet["profit_units"])
    o = _outcome_key(bet.get("outcome"))
    if o == "win":
        return 10.0 / 11.0
    if o == "loss":
        return -1.0
    return 0.0


def prob_bucket_calibration(graded_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 1.01))
    settled: list[tuple[float, str]] = []
    for row in graded_rows:
        o = _outcome_key(row.get("outcome"))
        if o not in {"win", "loss"}:
            continue
        try:
            p = float(row.get("model_prob"))
        except (TypeError, ValueError):
            continue
        settled.append((p, o))
    out: list[dict[str, Any]] = []
    for lower, upper in buckets:
        in_b = [(p, x) for p, x in settled if lower <= p < upper]
        wins = sum(1 for _, x in in_b if x == "win")
        bets = len(in_b)
        actual = _safe_divide(float(wins), float(bets)) if bets else 0.0
        expected = float(sum(p for p, _ in in_b) / bets) if bets else (lower + min(upper, 0.70)) / 2.0
        label = f"{lower:.0%}-{upper:.0%}" if upper < 1.0 else "65%+"
        out.append(
            {
                "bucket": label,
                "bets": bets,
                "expected_win_rate": expected,
                "actual_win_rate": actual,
                "delta": actual - expected if bets else 0.0,
            }
        )
    return out


def prob_bucket_calibration_live(data_root: Path = Path("data")) -> list[dict[str, Any]]:
    rows = load_graded_bets(None, data_root)
    official: list[dict[str, Any]] = []
    for row in rows:
        gd = str(row.get("date", "")).strip()
        if not gd:
            continue
        if not resolve_official_card(row, gd, data_root):
            continue
        if not is_settled_graded_row(row):
            continue
        official.append(row)
    return prob_bucket_calibration(official)


def load_graded_bets(game_date: str | None = None, data_root: Path = Path("data")) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    player_root = data_root / "graded"
    if game_date:
        paths = [player_root / f"graded_{game_date}.json"]
    else:
        paths = sorted(player_root.glob("graded_*.json"))
    for path in paths:
        if not path.exists():
            continue
        payload = _load_json(path)
        if not isinstance(payload, list):
            continue
        artifact_date = path.stem.removeprefix("graded_")
        for item in payload:
            record = _dict_record(item)
            record["date"] = artifact_date
            record.setdefault("bet_type", "player_prop")
            rows.append(record)
    return rows


def grade_hit_rate_summary_live(data_root: Path = Path("data")) -> list[dict[str, Any]]:
    rows = load_graded_bets(None, data_root)
    settled: list[dict[str, Any]] = []
    for row in rows:
        gd = str(row.get("date", "")).strip()
        if not gd:
            continue
        if not resolve_official_card(row, gd, data_root):
            continue
        if not is_settled_graded_row(row):
            continue
        if not str(row.get("final_grade", "")).strip():
            continue
        settled.append(row)
    by_grade: dict[str, list[dict[str, Any]]] = {}
    for row in settled:
        g = str(row.get("final_grade")).strip().upper()
        by_grade.setdefault(g, []).append(row)
    out: list[dict[str, Any]] = []
    for grade in sorted(by_grade.keys(), key=final_grade_rank):
        bucket = by_grade[grade]
        wins = sum(1 for r in bucket if _outcome_key(r.get("outcome")) == "win")
        losses = sum(1 for r in bucket if _outcome_key(r.get("outcome")) == "loss")
        denom = wins + losses
        wagered = sum(_stake_units(r) for r in bucket)
        profit = sum(_profit_units(r) for r in bucket)
        out.append(
            {
                "Grade": grade,
                "Bets": len(bucket),
                "Hit%": _safe_divide(float(wins), float(denom)),
                "ROI": _safe_divide(profit, wagered),
            }
        )
    return out


def results_projection_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Minimal projection error metrics for Results scope."""
    compared = 0
    abs_err = 0.0
    signed = 0.0
    for row in rows:
        if row.get("bet_type") != "player_prop":
            continue
        try:
            actual = float(row.get("actual_value"))
            proj = float(row.get("projection", row.get("point_estimate")))
        except (TypeError, ValueError):
            continue
        delta = actual - proj
        compared += 1
        abs_err += abs(delta)
        signed += delta
    if compared == 0:
        return {"compared_rows": 0, "avg_abs_error": 0.0, "signed_bias": 0.0}
    return {
        "compared_rows": compared,
        "avg_abs_error": abs_err / compared,
        "signed_bias": signed / compared,
    }


def results_metrics_by_scope(
    game_date: str,
    data_root: Path = Path("data"),
) -> dict[str, dict[str, Any]]:
    picks_all = load_graded_bets(game_date, data_root)
    picks_official = [
        r
        for r in picks_all
        if resolve_official_card(r, game_date, data_root)
        and row_passes_research_visibility(r, show_research_raw=False)
    ]
    uni_path = data_root / "graded" / f"graded_universe_{game_date}.json"
    universe_rows: list[dict[str, Any]] = []
    if uni_path.exists():
        payload = _load_json(uni_path)
        if isinstance(payload, list):
            universe_rows = [_dict_record(x) for x in payload if isinstance(x, dict)]
    return {
        "picks": {
            "dashboard": {},
            "projection": results_projection_metrics(picks_official),
        },
        "universe": {"projection": results_projection_metrics(universe_rows)},
    }


def get_available_results_dates(data_root: Path = Path("data")) -> list[str]:
    dates: set[str] = set()
    for path in (data_root / "graded").glob("graded_*.json"):
        stem = path.stem.removeprefix("graded_")
        try:
            date.fromisoformat(stem)
        except ValueError:
            continue
        dates.add(stem)
    for path in (data_root / "edges").glob("filtered_edges_*.json"):
        stem = path.stem.removeprefix("filtered_edges_")
        try:
            date.fromisoformat(stem)
        except ValueError:
            continue
        dates.add(stem)
    return sorted(dates, reverse=True)


def load_backtest_summary(season: str, data_root: Path = Path("data")) -> dict[str, Any]:
    path = data_root / "backtest" / f"summary_{season}.json"
    if not path.exists():
        return {}
    payload = _load_json(path)
    return payload if isinstance(payload, dict) else {}


def load_bankroll_history(season: str, data_root: Path = Path("data")) -> list[dict[str, Any]]:
    path = data_root / "backtest" / f"bankroll_{season}.json"
    if not path.exists():
        return []
    payload = _load_json(path)
    return payload if isinstance(payload, list) else []


def load_bettable_today_edges(
    game_date: str,
    data_root: Path = Path("data"),
    *,
    include_debug: bool = False,
    card_size: int | None = None,
) -> list[dict[str, Any]]:
    edges = load_all_filtered_player_prop_edges(game_date, data_root)
    if include_debug:
        return edges
    return filtered_card_edges(edges, card_size=card_size)


def count_games(edges: list[dict[str, Any]]) -> int:
    return len({str(e.get("game_id", "")).strip() for e in edges if e.get("game_id")})


def stale_odds_grade_cap_warning(_edges: list[dict[str, Any]]) -> str | None:
    return None


def card_cluster_warning_message(_edges: list[dict[str, Any]]) -> str | None:
    return None


def correlated_bet_warnings(_edges: list[dict[str, Any]]) -> list[str]:
    return []


def duplicate_player_exposure_message(edges: list[dict[str, Any]]) -> str | None:
    counts: Counter[str] = Counter()
    names: dict[str, str] = {}
    for edge in edges:
        key = _normalized_player(edge)
        if not key:
            continue
        counts[key] += 1
        if key not in names:
            names[key] = str(edge.get("player", edge.get("player_name", ""))).strip() or key.title()
    dups = sorted(names[k] for k, n in counts.items() if n >= 2)
    if not dups:
        return None
    return f"Duplicate player exposure detected: {', '.join(dups)}."


def line_move_warning_message(_edges: list[dict[str, Any]]) -> str | None:
    return None


def group_edges_by_grade(edges: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for e in edges:
        g = str(e.get("final_grade", "—")).strip() or "—"
        out.setdefault(g, []).append(e)
    return out


def grade_factors_to_bullets(_factors: Any) -> str:
    return ""


def load_manual_promotions(_date: str, _root: Path = Path("data")) -> list[dict[str, Any]]:
    return []


def save_manual_promotions(_date: str, _rows: list[dict[str, Any]], _root: Path = Path("data")) -> None:
    pass


def load_suggested_parlays(_date: str, _root: Path = Path("data")) -> list[dict[str, Any]]:
    return []


def load_watchlist_edges(_date: str, _root: Path = Path("data")) -> list[dict[str, Any]]:
    return []


def load_slate_injury_report(_date: str, _root: Path = Path("data")) -> tuple[list[dict[str, Any]], str | None]:
    return [], None


def load_team_moneyline_edges(_date: str, _root: Path = Path("data")) -> list[dict[str, Any]]:
    return []
