"""Audit closing-line capture completeness."""

from __future__ import annotations

from data_pipeline.db import get_connection


def main() -> None:
    query = """
        SELECT COUNT(*) AS total,
               AVG(minutes_before_tip) AS avg_minutes,
               SUM(CASE WHEN minutes_before_tip <= 5 THEN 1 ELSE 0 END) AS within_5
        FROM closing_lines
        WHERE captured_at >= NOW() - INTERVAL '1 day'
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone() or {}
    total = int(row.get("total") or 0)
    avg_minutes = float(row.get("avg_minutes") or 0.0)
    within_5 = int(row.get("within_5") or 0)
    print(f"Closing lines captured: {total}")
    print(f"Mean minutes_before_tip: {avg_minutes:.2f}")
    print(f"Captured within five minutes: {within_5}")


if __name__ == "__main__":
    main()
