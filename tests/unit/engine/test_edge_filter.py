"""Tests for engine.edge_filter."""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.edge_filter import FilterRules, build_filtered_payload, filter_raw_edges


def _rules() -> FilterRules:
    path = Path("config/filter_rules.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return FilterRules.model_validate(raw)


def test_filter_typical_sample_matches_quality_threshold() -> None:
    """High/medium edges keep; low edge with weak quality rejects."""
    rules = _rules()
    raw = [
        {
            "player_name": "Player A",
            "market": "player_points",
            "line": 24.5,
            "side": "over",
            "book": "draftkings",
            "edge": 0.07,
            "confidence": "high",
            "game_id": "g1",
        },
        {
            "player_name": "Player B",
            "market": "player_rebounds",
            "line": 8.5,
            "side": "under",
            "book": "draftkings",
            "edge": 0.055,
            "confidence": "medium",
            "game_id": "g1",
        },
        {
            "player_name": "Player C",
            "market": "player_assists",
            "line": 5.5,
            "side": "over",
            "book": "betmgm",
            "edge": 0.04,
            "confidence": "low",
            "game_id": "g2",
        },
    ]
    edges, summary = filter_raw_edges(raw, rules, game_date="2026-05-08")
    assert summary.raw_edges == 3
    assert summary.kept_bettable == 2
    assert summary.rejected == 1
    kept = [e for e in edges if e.get("bettable")]
    assert {e["player_name"] for e in kept} == {"Player A", "Player B"}
    rejected = [e for e in edges if not e.get("bettable")]
    assert len(rejected) == 1
    assert "edge_quality_auto_reject" in rejected[0]["risk_tags"]


def test_filter_dedupe_keeps_best_edge_deterministic() -> None:
    """Same key: higher-edge book wins; tie-break uses book name then index."""
    rules = _rules()
    raw = [
        {
            "player_name": "X",
            "market": "player_points",
            "line": 20.5,
            "side": "over",
            "book": "alpha",
            "edge": 0.05,
            "confidence": "high",
            "game_id": "g9",
        },
        {
            "player_name": "X",
            "market": "player_points",
            "line": 20.5,
            "side": "over",
            "book": "zebra",
            "edge": 0.05,
            "confidence": "high",
            "game_id": "g9",
        },
        {
            "player_name": "X",
            "market": "player_points",
            "line": 20.5,
            "side": "over",
            "book": "mid",
            "edge": 0.06,
            "confidence": "high",
            "game_id": "g9",
        },
    ]
    edges, summary = filter_raw_edges(raw, rules, game_date="2099-01-01")
    assert summary.dedupe_losers == 2
    winners = [e for e in edges if e.get("bettable")]
    assert len(winners) == 1
    assert winners[0]["sportsbook"] == "mid"


def test_filter_missing_and_invalid_edge_values_minimal() -> None:
    """Missing player name still parses; invalid edge string treated as 0."""
    rules = _rules()
    raw = [
        {
            "player": "",
            "market": "player_points",
            "line": 1.0,
            "side": "over",
            "sportsbook": "draftkings",
            "edge": "not-a-number",
            "confidence": "low",
        },
    ]
    edges, summary = filter_raw_edges(raw, rules, game_date="2099-02-01")
    assert summary.raw_edges == 1
    assert summary.rejected == 1
    assert edges[0]["filtered_edge"] == 0.0


def test_build_filtered_payload_includes_summary() -> None:
    rules = _rules()
    edges, summary = filter_raw_edges([], rules, game_date="2099-03-01")
    payload = build_filtered_payload(edges, summary, research_only=False)
    assert payload["research_only"] is False
    assert payload["summary"]["raw_edges"] == 0
    assert payload["edges"] == []
