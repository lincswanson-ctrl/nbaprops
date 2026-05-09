"""Team moneyline page (stub)."""

from __future__ import annotations

import streamlit as st

EMPTY_STATE = "No team moneyline edges loaded."


def render() -> None:
    st.title("Team Moneyline")
    st.info(EMPTY_STATE)
