"""Walk-forward backtest for the pregame model (spec 07).

Read-only against Postgres; writes JSON/CSV results under ``data/backtest/``.

Usage:
    python -m orchestration.backtest --season 2025-26 --mode calibration
    python -m orchestration.backtest --start 2026-04-18 --end 2026-06-20 --mode betting \
        --all-game-types --label playoffs-2026
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import structlog
from pydantic import BaseModel, ConfigDict

from betting.grading import grade_bet, profit_units
from betting.market import BookOffer, consensus_offer, pick_side
from betting.selection import Candidate, SelectionRules, select_picks
from data_pipeline.db import get_connection
from models.baseline.projection import (
    BASELINE_LOOKBACK_DAYS,
    market_stat_values,
    prob_over,
    project,
    score_market,
)
from models.evaluation import brier_score, brier_skill_vs_base_rate, log_loss, reliability_bins
from models.walk_forward import (
    STAT_COLUMNS,
    HistoryIndex,
    PlayerGame,
    actual_value,
    proxy_line,
)
from orchestration.pregame import EDGE_SANITY_BOUND, selection_rules

logger = structlog.get_logger(__name__)

CALIBRATION_MARKETS = (
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_points_rebounds_assists",
)
# Test lines at the proxy line and one point either side.
CALIBRATION_OFFSETS = (-1.0, 0.0, 1.0)
REGULAR_SEASON_GAME_ID_PREFIX = "002"
BANKROLL_START_UNITS = 100.0
EDGE_BUCKETS = ((0.0, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 1.0))
OUTPUT_DIR = Path("data/backtest")


class BetRecord(BaseModel):
    """One graded backtest pick."""

    model_config = ConfigDict(frozen=True)

    game_date: date
    game_id: str
    player_id: str
    market: str
    side: str
    line: float
    book: str
    price: int
    model_prob: float
    fair_prob: float
    edge: float
    actual: float | None
    outcome: str
    profit: float


# --------------------------------------------------------------------------- data access


def load_player_games(start: date, end: date) -> list[PlayerGame]:
    """Box scores from ``start - lookback`` through ``end`` (server-side cursor)."""
    cols = ", ".join(f"pgs.{c}" for c in STAT_COLUMNS)
    query = f"""
        SELECT pgs.player_id, pgs.game_id, g.date AS game_date, pgs.minutes, pgs.dnp_reason,
               {cols}
        FROM player_game_stats pgs
        JOIN games g ON g.game_id = pgs.game_id
        WHERE g.date BETWEEN %(lo)s AND %(hi)s
    """
    lo = start - timedelta(days=BASELINE_LOOKBACK_DAYS)
    rows: list[PlayerGame] = []
    with get_connection() as conn:
        with conn.cursor(name="backtest_player_games") as cur:
            cur.execute(query, {"lo": lo, "hi": end})
            for r in cur:
                rows.append(_player_game(cast(dict[str, Any], r)))
    return rows


def _player_game(r: dict[str, Any]) -> PlayerGame:
    played = float(r["minutes"] or 0) > 0 and r["dnp_reason"] is None
    stats = {c: (float(r[c]) if r[c] is not None else None) for c in STAT_COLUMNS}
    return PlayerGame(
        player_id=str(r["player_id"]),
        game_id=str(r["game_id"]),
        game_date=r["game_date"],
        played=played,
        stats=stats,
    )


def load_closing_lines(start: date, end: date, regular_only: bool) -> list[dict[str, Any]]:
    """Closing lines captured before tip for games in [start, end]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cl.game_id, cl.player_id, cl.market, cl.book, cl.line,
                       cl.over_price, cl.under_price, g.date AS game_date
                FROM closing_lines cl
                JOIN games g ON g.game_id = cl.game_id
                WHERE g.date BETWEEN %(lo)s AND %(hi)s
                  AND COALESCE(cl.minutes_before_tip, 0) >= 0
                """,
                {"lo": start, "hi": end},
            )
            rows = [dict(r) for r in cur.fetchall()]
    return [r for r in rows if not regular_only or _is_regular_season(str(r["game_id"]))]


def season_date_range(season: str) -> tuple[date, date]:
    """First and last game date stored for ``season``."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MIN(date) AS lo, MAX(date) AS hi FROM games WHERE season = %(s)s",
                {"s": season},
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
    if row is None or row["lo"] is None:
        raise SystemExit(f"No games stored for season {season}")
    return row["lo"], row["hi"]


def _is_regular_season(game_id: str) -> bool:
    return game_id.startswith(REGULAR_SEASON_GAME_ID_PREFIX)


# --------------------------------------------------------------------------- calibration


def calibration_samples(
    index: HistoryIndex, targets: Iterable[PlayerGame], market: str
) -> tuple[list[float], list[int], list[float]]:
    """Model P(over) and outcomes at proxy lines; plus |projected mean - actual| errors."""
    probs: list[float] = []
    outcomes: list[int] = []
    abs_errors: list[float] = []
    for target in targets:
        actual = actual_value(market, target)
        if actual is None:
            continue
        values = market_stat_values(market, index.prior_played(target.player_id, target.game_date))
        projection = project(values or [])
        base_line = proxy_line(values or [])
        if projection is None or base_line is None:
            continue
        abs_errors.append(abs(projection.mean - actual))
        for offset in CALIBRATION_OFFSETS:
            line = base_line + offset
            if line <= 0:
                continue
            probs.append(prob_over(line, projection))
            outcomes.append(int(actual > line))
    return probs, outcomes, abs_errors


def run_calibration(index: HistoryIndex, targets: Sequence[PlayerGame]) -> dict[str, Any]:
    """Per-market forecast quality of baseline_v1 at realistic (proxy) lines."""
    report: dict[str, Any] = {}
    for market in CALIBRATION_MARKETS:
        probs, outcomes, errors = calibration_samples(index, targets, market)
        if not probs:
            report[market] = {"samples": 0}
            continue
        report[market] = {
            "samples": len(probs),
            "player_games": len(errors),
            "base_rate_over": round(sum(outcomes) / len(outcomes), 4),
            "brier": round(brier_score(probs, outcomes), 5),
            "brier_skill_vs_base_rate": round(brier_skill_vs_base_rate(probs, outcomes), 5),
            "log_loss": round(log_loss(probs, outcomes), 5),
            "mean_abs_error": round(sum(errors) / len(errors), 3),
            "reliability": [b.model_dump() for b in reliability_bins(probs, outcomes)],
        }
    return report


# --------------------------------------------------------------------------- betting


def build_candidates(
    index: HistoryIndex, closing_rows: Sequence[dict[str, Any]]
) -> dict[date, list[tuple[Candidate, dict[str, Any]]]]:
    """Score each closing-line market with information available before its game date."""
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in closing_rows:
        grouped.setdefault((str(r["game_id"]), str(r["player_id"]), str(r["market"])), []).append(r)
    by_date: dict[date, list[tuple[Candidate, dict[str, Any]]]] = {}
    for (game_id, player_id, market), rows in sorted(grouped.items()):
        game_date: date = rows[0]["game_date"]
        consensus = consensus_offer([_book_offer(r) for r in rows])
        prob = score_market(market, consensus.line, index.prior_played(player_id, game_date))
        if prob is None:
            continue
        side = pick_side(prob, consensus, EDGE_SANITY_BOUND)
        cand = Candidate(
            game_id=game_id,
            player_id=player_id,
            market=market,
            side=side.side,
            edge=side.edge,
            book=side.book,
            price=side.price,
        )
        context = {
            "date": game_date,
            "line": consensus.line,
            "p": prob,
            "fair": consensus.fair_over,
        }
        by_date.setdefault(game_date, []).append((cand, context))
    return by_date


def _book_offer(r: dict[str, Any]) -> BookOffer:
    under = r.get("under_price")
    return BookOffer(
        book=str(r["book"]),
        line=float(r["line"]),
        over_price=int(r["over_price"]),
        under_price=int(under) if under is not None else None,
    )


def run_betting(
    index: HistoryIndex, closing_rows: Sequence[dict[str, Any]], rules: SelectionRules
) -> list[BetRecord]:
    """Replay live pick selection day by day and grade every pick."""
    bets: list[BetRecord] = []
    for game_date, scored in sorted(build_candidates(index, closing_rows).items()):
        context = {(c.game_id, c.player_id, c.market): ctx for c, ctx in scored}
        for decision in select_picks([c for c, _ in scored], rules):
            if decision.is_pick:
                cand = decision.candidate
                bets.append(
                    _grade(index, cand, context[(cand.game_id, cand.player_id, cand.market)])
                )
    return bets


def _grade(index: HistoryIndex, cand: Candidate, ctx: dict[str, Any]) -> BetRecord:
    actual = actual_value(cand.market, index.game(cand.player_id, cand.game_id))
    outcome = grade_bet(cand.side, float(ctx["line"]), actual)
    price = int(cand.price or 0)
    over_side = cand.side == "over"
    return BetRecord(
        game_date=ctx["date"],
        game_id=cand.game_id,
        player_id=cand.player_id,
        market=cand.market,
        side=cand.side,
        line=float(ctx["line"]),
        book=str(cand.book),
        price=price,
        model_prob=float(ctx["p"]) if over_side else 1.0 - float(ctx["p"]),
        fair_prob=float(ctx["fair"]) if over_side else 1.0 - float(ctx["fair"]),
        edge=cand.edge,
        actual=actual,
        outcome=outcome,
        profit=profit_units(outcome, price),
    )


def summarize_bets(bets: Sequence[BetRecord]) -> dict[str, Any]:
    """Headline numbers plus breakdowns by market, side and edge bucket."""
    summary = _group_stats(bets)
    summary["by_market"] = {
        m: _group_stats([b for b in bets if b.market == m])
        for m in sorted({b.market for b in bets})
    }
    summary["by_side"] = {
        s: _group_stats([b for b in bets if b.side == s]) for s in ("over", "under")
    }
    summary["by_edge_bucket"] = {
        f"{lo:.2f}-{hi:.2f}": _group_stats([b for b in bets if lo <= b.edge < hi])
        for lo, hi in EDGE_BUCKETS
    }
    return summary


def _group_stats(bets: Sequence[BetRecord]) -> dict[str, Any]:
    wins = sum(b.outcome == "win" for b in bets)
    losses = sum(b.outcome == "loss" for b in bets)
    pushes = sum(b.outcome == "push" for b in bets)
    risked = wins + losses + pushes
    profit = sum(b.profit for b in bets)
    return {
        "bets": len(bets),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "voids": sum(b.outcome == "void" for b in bets),
        "win_rate": round(wins / (wins + losses), 4) if wins + losses else None,
        "profit_units": round(profit, 3),
        "roi": round(profit / risked, 4) if risked else None,
        "avg_edge": round(sum(b.edge for b in bets) / len(bets), 4) if bets else None,
        "avg_model_prob": round(sum(b.model_prob for b in bets) / len(bets), 4) if bets else None,
    }


def bankroll_curve(bets: Sequence[BetRecord]) -> list[dict[str, Any]]:
    """End-of-day bankroll in units with flat 1-unit stakes."""
    bankroll = BANKROLL_START_UNITS
    by_day: dict[date, float] = {}
    for bet in sorted(bets, key=lambda b: b.game_date):
        by_day[bet.game_date] = by_day.get(bet.game_date, 0.0) + bet.profit
    curve: list[dict[str, Any]] = []
    for day in sorted(by_day):
        bankroll += by_day[day]
        curve.append({"date": day.isoformat(), "bankroll": round(bankroll, 3)})
    return curve


# --------------------------------------------------------------------------- CLI


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Walk-forward backtest (spec 07)")
    parser.add_argument("--season", help="Season label, e.g. 2025-26 (sets --start/--end)")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--mode", choices=["calibration", "betting", "both"], default="both")
    parser.add_argument("--label", help="Output file label (default: season or start_end)")
    parser.add_argument(
        "--all-game-types",
        action="store_true",
        help="Include playoff/play-in games (default: regular season only, game_id 002...)",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args(argv)


def _resolve_range(args: argparse.Namespace) -> tuple[date, date, str]:
    if args.season:
        start, end = season_date_range(args.season)
        return args.start or start, args.end or end, args.label or args.season
    if not (args.start and args.end):
        raise SystemExit("Provide --season or both --start and --end")
    return args.start, args.end, args.label or f"{args.start}_{args.end}"


def main(argv: Sequence[str] | None = None) -> None:
    """Run the requested backtest mode(s) and write results."""
    args = _parse_args(argv)
    start, end, label = _resolve_range(args)
    regular_only = not args.all_game_types
    games = load_player_games(start, end)
    index = HistoryIndex(games)
    targets = [
        g
        for g in games
        if start <= g.game_date <= end and (not regular_only or _is_regular_season(g.game_id))
    ]
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("backtest_start", start=str(start), end=str(end), targets=len(targets))
    if args.mode in ("calibration", "both"):
        report: dict[str, Any] = {
            "model_version": "baseline_v1",
            "start": str(start),
            "end": str(end),
        }
        report["markets"] = run_calibration(index, targets)
        _write_json(out_dir / f"calibration_{label}.json", report)
    if args.mode in ("betting", "both"):
        _run_and_write_betting(index, start, end, label, regular_only, out_dir)


def _run_and_write_betting(
    index: HistoryIndex, start: date, end: date, label: str, regular_only: bool, out_dir: Path
) -> None:
    closing = load_closing_lines(start, end, regular_only)
    bets = run_betting(index, closing, selection_rules())
    summary: dict[str, Any] = {"model_version": "baseline_v1", "start": str(start), "end": str(end)}
    summary["closing_line_rows"] = len(closing)
    summary.update(summarize_bets(bets))
    _write_json(out_dir / f"summary_{label}.json", summary)
    _write_json(out_dir / f"bankroll_{label}.json", bankroll_curve(bets))
    with (out_dir / f"bets_{label}.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(BetRecord.model_fields))
        writer.writeheader()
        for bet in bets:
            writer.writerow(bet.model_dump(mode="json"))
    logger.info("backtest_betting_done", bets=len(bets), profit=summary["profit_units"])


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("backtest_wrote", path=str(path))


if __name__ == "__main__":
    main()
