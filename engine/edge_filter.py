"""Deterministic edge filtering for pre-game card generation.

Pure functions plus Pydantic models for rules and summary. Callers handle I/O.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Confidence = Literal["high", "medium", "low"]


class CardCaps(BaseModel):
    """Exposure limits; value 0 disables that limit."""

    max_picks_per_game: int = 0
    max_picks_per_team: int = 0
    max_same_market_per_game: int = 0
    max_unders_per_card: int = 0
    max_threes_unders_per_card: int = 0
    max_defensive_props_per_card: int = 0


class NoveltyQuota(BaseModel):
    enabled: bool = False
    min_new_player_props: int = 2


class GlobalRules(BaseModel):
    novelty_quota: NoveltyQuota = Field(default_factory=NoveltyQuota)


class QualityRules(BaseModel):
    auto_reject_below: int = 50


class MarketGroups(BaseModel):
    threes: list[str] = Field(default_factory=lambda: ["player_threes"])
    defensive: list[str] = Field(
        default_factory=lambda: ["player_steals", "player_blocks", "player_steals_blocks"]
    )


class FilterRules(BaseModel):
    """YAML-backed filter configuration."""

    model_config = ConfigDict(populate_by_name=True)

    card_caps: CardCaps = Field(default_factory=CardCaps)
    global_: GlobalRules = Field(alias="global", default_factory=GlobalRules)
    quality: QualityRules = Field(default_factory=QualityRules)
    min_edge_by_confidence: dict[str, float] = Field(
        default_factory=lambda: {"high": 0.0, "medium": 0.0, "low": 0.0}
    )
    research_only_markets: list[str] = Field(default_factory=list)

    market_groups: MarketGroups = Field(default_factory=MarketGroups)

    @field_validator("research_only_markets", mode="before")
    @classmethod
    def _lower_markets(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(x).strip().lower() for x in v if str(x).strip()]


class FilterSummary(BaseModel):
    """Counts and metadata for a filter run."""

    game_date: str
    raw_edges: int
    kept_bettable: int
    rejected: int
    dedupe_losers: int
    exposure_capped: int
    novelty_rejected: int


def _norm_conf(value: str) -> Confidence:
    v = value.strip().lower()
    if v in ("high", "medium", "low"):
        return v  # type: ignore[return-value]
    return "medium"


def _player_key(edge: dict[str, Any]) -> str:
    if edge.get("player_id") not in (None, ""):
        try:
            return f"id:{int(edge['player_id'])}"
        except (TypeError, ValueError):
            pass
    name = str(edge.get("player_name", edge.get("player", ""))).strip().lower()
    return f"n:{name}"


def _line_key(line: Any) -> str:
    if line is None:
        return ""
    if isinstance(line, (int, float)) and not isinstance(line, bool):
        if math.isfinite(float(line)):
            return str(float(line))
        return ""
    text = str(line).strip()
    try:
        return str(float(text))
    except ValueError:
        return text.lower()


def _book(edge: dict[str, Any]) -> str:
    return str(edge.get("sportsbook", edge.get("book", ""))).strip().lower()


def _dedupe_key(edge: dict[str, Any]) -> tuple[str, str, str, str, str]:
    gid = str(edge.get("game_id", "__none__")).strip() or "__none__"
    return (gid, _player_key(edge), str(edge.get("market", "")).strip().lower(), _line_key(edge.get("line")), str(edge.get("side", "")).strip().lower())


def _quality_score(edge: dict[str, Any]) -> int:
    try:
        e = float(edge.get("edge", 0.0))
    except (TypeError, ValueError):
        e = 0.0
    conf = _norm_conf(str(edge.get("confidence", "medium")))
    bonus = {"high": 20, "medium": 10, "low": 0}[conf]
    raw = 40.0 + e * 150.0 + float(bonus)
    return int(max(0, min(100, round(raw))))


def _final_grade_and_score(quality: int) -> tuple[str, int]:
    if quality >= 88:
        return "A", 90
    if quality >= 82:
        return "A-", 85
    if quality >= 76:
        return "B+", 80
    if quality >= 70:
        return "B", 72
    if quality >= 64:
        return "B-", 66
    if quality >= 58:
        return "C+", 60
    if quality >= 52:
        return "C", 54
    if quality >= 46:
        return "C-", 48
    return "D", max(30, quality)


def _min_edge_for(rules: FilterRules, conf: Confidence) -> float:
    raw = rules.min_edge_by_confidence.get(conf)
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _is_under(edge: dict[str, Any]) -> bool:
    return str(edge.get("side", "")).strip().lower() == "under"


def _market_in(edge: dict[str, Any], markets: list[str]) -> bool:
    m = str(edge.get("market", "")).strip().lower()
    return m in set(markets)


def _dedupe_winner_index_by_key(edges: list[dict[str, Any]]) -> dict[tuple[str, str, str, str, str], int]:
    """Map dedupe key -> index of winning row (best edge, then book name, then index)."""
    best: dict[tuple[str, str, str, str, str], tuple[float, str, int]] = {}
    for i, edge in enumerate(edges):
        key = _dedupe_key(edge)
        try:
            ev = float(edge.get("edge", 0.0))
        except (TypeError, ValueError):
            ev = 0.0
        bk = _book(edge)
        cur = best.get(key)
        candidate = (ev, bk, i)
        if cur is None or candidate > cur:
            best[key] = candidate
    return {k: v[2] for k, v in best.items()}


def _quality_reason_label(bettable: bool, tags: list[str]) -> str:
    if bettable:
        return "Meets quality bar"
    if "research_only_market" in tags:
        return "Research-only market"
    if "duplicate_line_suppressed" in tags:
        return "Duplicate line (not best price)"
    if "below_min_edge_for_confidence" in tags:
        return "Below minimum edge for confidence tier"
    return "Low edge-quality score"


def _sort_for_card(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(e: dict[str, Any]) -> tuple[float, float, str]:
        fs = e.get("final_score")
        try:
            fsv = float(fs) if fs is not None else float("-inf")
        except (TypeError, ValueError):
            fsv = float("-inf")
        try:
            fe = float(e.get("filtered_edge", e.get("edge", 0.0)))
        except (TypeError, ValueError):
            fe = 0.0
        return (fsv, fe, _player_key(e))

    return sorted(edges, key=key, reverse=True)


def _novelty_victim_row(live: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick weakest bettable row among players who still have duplicate exposure."""
    counts: dict[str, int] = {}
    for r in live:
        pk = _player_key(r)
        if pk:
            counts[pk] = counts.get(pk, 0) + 1
    dup_players = {pk for pk, c in counts.items() if c > 1}
    if not dup_players:
        return None
    pool = [r for r in live if _player_key(r) in dup_players]
    pool.sort(
        key=lambda r: (
            float(r.get("final_score") or 0),
            float(r.get("filtered_edge") or 0),
            _book(r),
            _player_key(r),
        )
    )
    return pool[0]


def _apply_novelty(out: list[dict[str, Any]], rules: FilterRules, summary: FilterSummary) -> None:
    """Drop duplicate-player weakest picks until distinct-player count meets quota or no dups."""
    nq = rules.global_.novelty_quota
    if not nq.enabled:
        return
    need = max(0, int(nq.min_new_player_props))
    max_rounds = len(out) + 2
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        live = [r for r in out if r.get("decision") == "keep" and r.get("bettable")]
        players = {_player_key(r) for r in live if _player_key(r)}
        if len(players) >= need:
            return
        victim = _novelty_victim_row(live)
        if victim is None:
            return
        victim["decision"] = "reject"
        victim["bettable"] = False
        victim["official_card"] = False
        tags = list(victim.get("risk_tags", [])) if isinstance(victim.get("risk_tags"), list) else []
        tags.append("novelty_quota")
        victim["risk_tags"] = tags
        victim["reason"] = (
            f"Rejected: novelty quota requires {need} distinct players; "
            f"removed lowest-priority duplicate pick."
        )
        summary.novelty_rejected += 1


def _apply_card_caps(rows: list[dict[str, Any]], rules: FilterRules, summary: FilterSummary) -> None:
    caps = rules.card_caps
    if all(
        getattr(caps, name) == 0
        for name in (
            "max_picks_per_game",
            "max_picks_per_team",
            "max_same_market_per_game",
            "max_unders_per_card",
            "max_threes_unders_per_card",
            "max_defensive_props_per_card",
        )
    ):
        for e in rows:
            if e.get("decision") == "keep" and e.get("bettable"):
                e["official_card"] = True
        return

    per_game: dict[str, int] = {}
    per_team: dict[str, int] = {}
    per_game_market: dict[tuple[str, str], int] = {}
    unders = 0
    threes_u = 0
    defens = 0

    for e in _sort_for_card([x for x in rows if x.get("decision") == "keep" and x.get("bettable")]):
        gid = str(e.get("game_id", "")).strip() or "__none__"
        tid = str(e.get("team_id", e.get("team", ""))).strip().lower() or "__none__"
        mkt = str(e.get("market", "")).strip().lower()
        reject = False
        reason = ""
        if caps.max_picks_per_game > 0:
            if per_game.get(gid, 0) >= caps.max_picks_per_game:
                reject = True
                reason = f"Exposure cap: max {caps.max_picks_per_game} picks per game."
        if not reject and caps.max_picks_per_team > 0:
            if per_team.get(tid, 0) >= caps.max_picks_per_team:
                reject = True
                reason = f"Exposure cap: max {caps.max_picks_per_team} picks per team."
        if not reject and caps.max_same_market_per_game > 0:
            gm = (gid, mkt)
            if per_game_market.get(gm, 0) >= caps.max_same_market_per_game:
                reject = True
                reason = f"Exposure cap: max {caps.max_same_market_per_game} same market per game."
        if not reject and caps.max_unders_per_card > 0 and _is_under(e):
            if unders >= caps.max_unders_per_card:
                reject = True
                reason = f"Exposure cap: max {caps.max_unders_per_card} unders on card."
        if not reject and caps.max_threes_unders_per_card > 0:
            if _is_under(e) and _market_in(e, rules.market_groups.threes):
                if threes_u >= caps.max_threes_unders_per_card:
                    reject = True
                    reason = (
                        f"Exposure cap: max {caps.max_threes_unders_per_card} "
                        f"made-threes unders on card."
                    )
        if not reject and caps.max_defensive_props_per_card > 0:
            if _market_in(e, rules.market_groups.defensive):
                if defens >= caps.max_defensive_props_per_card:
                    reject = True
                    reason = (
                        f"Exposure cap: max {caps.max_defensive_props_per_card} "
                        f"defensive props on card."
                    )
        if reject:
            e["official_card"] = False
            e["bettable"] = False
            tags = list(e.get("risk_tags", [])) if isinstance(e.get("risk_tags"), list) else []
            tags.append("exposure_cap")
            e["risk_tags"] = tags
            e["reason"] = reason
            summary.exposure_capped += 1
            continue
        e["official_card"] = True
        per_game[gid] = per_game.get(gid, 0) + 1
        per_team[tid] = per_team.get(tid, 0) + 1
        per_game_market[(gid, mkt)] = per_game_market.get((gid, mkt), 0) + 1
        if _is_under(e):
            unders += 1
        if _is_under(e) and _market_in(e, rules.market_groups.threes):
            threes_u += 1
        if _market_in(e, rules.market_groups.defensive):
            defens += 1


def _normalize_pipeline_edges(raw_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in raw_edges:
        row = dict(raw)
        row.setdefault("game_id", row.get("game_id", ""))
        row["sportsbook"] = _book(row) or "unknown"
        row["player_name"] = str(row.get("player_name", row.get("player", ""))).strip()
        row["market"] = str(row.get("market", "")).strip().lower()
        row["side"] = str(row.get("side", "")).strip().lower()
        row.setdefault("research_only", False)
        normalized.append(row)
    return normalized


def _duplicate_loser_row(
    base: dict[str, Any],
    *,
    win_book: str,
    summary: FilterSummary,
) -> dict[str, Any]:
    summary.dedupe_losers += 1
    summary.rejected += 1
    return {
        **base,
        "decision": "reject",
        "bettable": False,
        "official_card": False,
        "filtered_edge": float(base.get("edge", 0.0) or 0.0),
        "filtered_confidence": _norm_conf(str(base.get("confidence", "medium"))).title(),
        "final_grade": "—",
        "final_score": 0,
        "quality_reason": "Duplicate line (not best price)",
        "reason": f"Rejected: duplicate line — better edge at {win_book}.",
        "risk_tags": ["duplicate_line_suppressed"],
        "model_prob": base.get("model_prob"),
    }


def _scored_edge_row(base: dict[str, Any], rules: FilterRules) -> dict[str, Any]:
    conf = _norm_conf(str(base.get("confidence", "medium")))
    try:
        edge_val = float(base.get("edge", 0.0))
    except (TypeError, ValueError):
        edge_val = 0.0
    q = _quality_score({**base, "edge": edge_val, "confidence": conf})
    min_e = _min_edge_for(rules, conf)
    grade, fscore = _final_grade_and_score(q)

    tags: list[str] = []
    reason = ""
    decision = "keep"
    bettable = True

    if base.get("research_only"):
        decision = "reject"
        bettable = False
        tags.append("research_only_market")
        reason = "Rejected: market is configured as research-only (not bettable)."

    if bettable and q < rules.quality.auto_reject_below:
        decision = "reject"
        bettable = False
        tags.append("edge_quality_auto_reject")
        reason = (
            f"Rejected: edge quality score {q}/100 is below auto-reject threshold "
            f"{rules.quality.auto_reject_below}"
        )
    elif bettable and edge_val < min_e:
        decision = "reject"
        bettable = False
        tags.append("below_min_edge_for_confidence")
        reason = (
            f"Rejected: edge {edge_val:.3f} is below minimum {min_e:.3f} "
            f"for {conf} confidence."
        )
    elif bettable:
        reason = "Kept: edge clears configured filter thresholds."

    return {
        **base,
        "decision": decision,
        "bettable": bettable,
        "official_card": False,
        "filtered_edge": edge_val,
        "filtered_confidence": conf.title(),
        "confidence": str(base.get("confidence", conf)).strip().title() or conf.title(),
        "final_grade": grade if bettable else "—",
        "final_score": fscore if bettable else q,
        "quality_reason": _quality_reason_label(bettable, tags),
        "reason": reason,
        "risk_tags": tags,
        "model_prob": base.get("model_prob"),
    }


def filter_raw_edges(
    raw_edges: list[dict[str, Any]],
    rules: FilterRules,
    *,
    game_date: str,
) -> tuple[list[dict[str, Any]], FilterSummary]:
    """Return filtered edge dicts and a summary.

    Args:
        raw_edges: Rows from ``edges_<date>.json`` (list of objects).
        rules: Parsed filter rules.
        game_date: ISO date string for bookkeeping.
    """
    summary = FilterSummary(
        game_date=game_date,
        raw_edges=len(raw_edges),
        kept_bettable=0,
        rejected=0,
        dedupe_losers=0,
        exposure_capped=0,
        novelty_rejected=0,
    )
    normalized = _normalize_pipeline_edges(raw_edges)
    winner_idx_by_key = _dedupe_winner_index_by_key(normalized)
    out: list[dict[str, Any]] = []
    for i, edge in enumerate(normalized):
        base = dict(edge)
        mkt = str(base.get("market", "")).strip().lower()
        if mkt in set(rules.research_only_markets):
            base["research_only"] = True

        dkey = _dedupe_key(edge)
        win_idx = winner_idx_by_key[dkey]
        if i != win_idx:
            win_book = _book(normalized[win_idx])
            out.append(_duplicate_loser_row(base, win_book=win_book, summary=summary))
            continue
        out.append(_scored_edge_row(base, rules))

    _apply_novelty(out, rules, summary)

    summary.kept_bettable = sum(1 for r in out if r.get("decision") == "keep" and r.get("bettable"))
    summary.rejected = sum(1 for r in out if r.get("decision") != "keep" or not r.get("bettable"))

    _apply_card_caps(out, rules, summary)

    return (out, summary)


def build_filtered_payload(
    edges: list[dict[str, Any]],
    summary: FilterSummary,
    *,
    research_only: bool,
) -> dict[str, Any]:
    """Wrap edges for ``filtered_edges_<date>.json`` on disk."""
    return {
        "research_only": research_only,
        "summary": summary.model_dump(),
        "edges": edges,
    }
