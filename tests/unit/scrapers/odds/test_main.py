from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx


def test_refresh_today_schedule_logs_and_continues_on_read_error(monkeypatch: Any) -> None:
    import data_pipeline.scrapers.odds.__main__ as odds_main

    class FakeNBAStatsScraper:
        async def __aenter__(self) -> "FakeNBAStatsScraper":
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: Any,
        ) -> None:
            return None

        async def fetch_scoreboard(self, *, season: str, game_date: Any) -> Any:
            raise httpx.ReadError("simulated read error")

    class FakeNBAStatsLoader:
        def load_scoreboard_schedule(self, *, season: str, game_date: Any) -> Any:
            raise AssertionError("loader should not run when fetch fails")

    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeLogger:
        def warning(self, event: str, **kwargs: Any) -> None:
            calls.append((event, kwargs))

        def info(self, event: str, **kwargs: Any) -> None:
            calls.append((event, kwargs))

    monkeypatch.setattr(odds_main, "NBAStatsScraper", FakeNBAStatsScraper)
    monkeypatch.setattr(odds_main, "NBAStatsLoader", FakeNBAStatsLoader)
    monkeypatch.setattr(odds_main, "logger", FakeLogger())

    asyncio.run(odds_main.refresh_today_schedule())

    assert calls, "expected at least one log call"
    assert calls[0][0] == "odds_schedule_refresh_failed"

