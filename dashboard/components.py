"""Reusable Streamlit layout pieces."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import streamlit as st

logger = __import__("structlog").get_logger(__name__)


@dataclass
class KPI:
    """Single headline metric tile."""

    label: str
    value: str
    help: str | None = None


@dataclass
class HeaderBadge:
    """Small label next to the page title."""

    label: str
    tone: str = "neutral"


def kpi_row(items: list[KPI]) -> None:
    """Render a row of metrics."""
    cols = st.columns(len(items))
    for col, kpi in zip(cols, items):
        with col:
            st.metric(kpi.label, kpi.value, help=kpi.help)


def page_header(
    title: str,
    *,
    subtitle: str | None = None,
    badges: list[HeaderBadge] | None = None,
    action_label: str | None = None,
    action_key: str = "page_action",
) -> bool:
    """Page title; returns True if action button was clicked."""
    c1, c2 = st.columns([4, 1])
    with c1:
        st.title(title)
        if subtitle:
            st.caption(subtitle)
        if badges:
            st.markdown(
                " ".join(f'<span class="badge">{b.label}</span>' for b in badges),
                unsafe_allow_html=True,
            )
    clicked = False
    with c2:
        if action_label:
            clicked = st.button(action_label, key=action_key)
    return clicked


@contextmanager
def section(title: str, caption: str | None = None) -> Iterator[None]:
    """Grouped section with optional caption."""
    st.subheader(title)
    if caption:
        st.caption(caption)
    yield


def grade_pill_html(_grade: str) -> str:
    return ""
