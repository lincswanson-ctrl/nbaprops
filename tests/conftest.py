"""Pytest hooks: skip modules that depend on optional pipeline packages."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if importlib.util.find_spec("data_pipeline.scrapers.odds.base") is None:
    collect_ignore = ["unit/scrapers/odds/test_betmgm.py"]
