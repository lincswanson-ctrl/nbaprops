"""Smoke test for orchestration.daily."""

from __future__ import annotations

import json
from pathlib import Path

from orchestration.daily import run_pregame_edge_filter


def test_run_pregame_edge_filter_writes_filtered(tmp_path: Path) -> None:
    rules_src = Path("config/filter_rules.yaml")
    rules_dst = tmp_path / "rules.yaml"
    rules_dst.write_text(rules_src.read_text(encoding="utf-8"), encoding="utf-8")
    data_root = tmp_path / "data"
    (data_root / "edges").mkdir(parents=True)
    raw = [
        {
            "player_name": "Solo",
            "market": "player_points",
            "line": 11.5,
            "side": "over",
            "book": "draftkings",
            "edge": 0.08,
            "confidence": "high",
            "game_id": "g1",
        }
    ]
    (data_root / "edges" / "edges_2000-01-01.json").write_text(
        json.dumps(raw),
        encoding="utf-8",
    )
    payload = run_pregame_edge_filter(
        "2000-01-01",
        data_root=data_root,
        rules_path=rules_dst,
    )
    out = data_root / "edges" / "filtered_edges_2000-01-01.json"
    assert out.exists()
    assert payload["summary"]["kept_bettable"] == 1
