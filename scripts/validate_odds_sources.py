"""Cross-book odds sanity checks."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from data_pipeline.db import get_connection


def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    print(f"Validating odds sources at {now}")
    query = """
        SELECT game_id, player_id, market, book, line, over_price, under_price
        FROM prop_lines
        WHERE snapshot_ts >= NOW() - INTERVAL '6 hours'
    """
    by_key: dict[tuple[str, str, str], dict[str, tuple[float, int, int]]] = defaultdict(dict)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            for row in cur.fetchall():
                by_key[(row["game_id"], row["player_id"], row["market"])][row["book"]] = (
                    float(row["line"]),
                    int(row["over_price"]),
                    int(row["under_price"]),
                )
    total_multi = sum(1 for books in by_key.values() if len(books) >= 2)
    print(f"Total props with multi-book coverage: {total_multi}")
    disagreements = 0
    for key, books in by_key.items():
        if "draftkings" in books and "fanduel" in books:
            dk_line = books["draftkings"][0]
            fd_line = books["fanduel"][0]
            if abs(dk_line - fd_line) > 1.5:
                disagreements += 1
                print(f"Line disagreement: {key} DK={dk_line} FD={fd_line}")
    print(f"Disagreements > 1.5 line points: {disagreements}")


if __name__ == "__main__":
    main()
