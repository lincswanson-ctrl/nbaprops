"""Pytest hooks: skip modules that depend on optional pipeline packages."""

from __future__ import annotations

import importlib.util

if importlib.util.find_spec("data_pipeline.scrapers.odds.base") is None:
    collect_ignore = ["unit/scrapers/odds/test_betmgm.py"]
