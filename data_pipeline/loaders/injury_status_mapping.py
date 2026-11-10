"""Injury status normalization map with regex fallbacks."""

from __future__ import annotations

import re

STATUS_MAP: dict[str, str] = {
    "out": "out",
    "ofs": "out",  # Rotowire: out for season
    "out for season": "out",
    "inactive": "out",
    "will not play": "out",
    "doubtful": "doubtful",
    "doubt": "doubtful",  # Rotowire abbreviation
    "questionable": "questionable",
    "gtd": "questionable",
    # ESPN "Day-To-Day" lines up with Rotowire "Ques" (spec 02, revised 2026-11-10).
    "dtd": "questionable",
    "day-to-day": "questionable",
    "ques": "questionable",  # Rotowire abbreviation
    "game-time decision": "questionable",
    "probable": "probable",
    "expected to play": "probable",
    "prob": "probable",  # Rotowire abbreviation
    "available": "available",
    "active": "available",
}

REGEX_FALLBACKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bout\b", re.IGNORECASE), "out"),
    (re.compile(r"\bdoubtful\b", re.IGNORECASE), "doubtful"),
    (re.compile(r"\bquestionable\b|\bgtd\b", re.IGNORECASE), "questionable"),
    (re.compile(r"\bprobable\b|expected to play", re.IGNORECASE), "probable"),
]


def normalize_status(raw_status: str) -> str:
    """Map source status string to normalized injury vocabulary."""

    normalized = raw_status.strip().lower()
    if normalized in STATUS_MAP:
        return STATUS_MAP[normalized]
    for pattern, mapped in REGEX_FALLBACKS:
        if pattern.search(raw_status):
            return mapped
    return "unknown"
