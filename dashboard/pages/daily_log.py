"""Daily log page (stub — restore full ledger UI when graded data is wired)."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    st.title("Daily Log")
    st.info("Bootstrap mode: add `data/graded/graded_YYYY-MM-DD.json` artifacts to enable this page.")
