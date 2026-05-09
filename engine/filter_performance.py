"""Filter performance report (bootstrap)."""

from __future__ import annotations

import pandas as pd


class FilterPerformanceTracker:
    """Aggregates graded rows by market/side/role (empty until data is wired)."""

    def generate_report(self) -> pd.DataFrame:
        return pd.DataFrame(
            columns=["market", "side", "player_role", "clearance", "total_bets", "hit_rate", "roi", "average_clv"]
        )
