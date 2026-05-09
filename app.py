"""Streamlit entrypoint (run from repo root: `streamlit run app.py`)."""

from __future__ import annotations

import streamlit as st

from dashboard.pages import card_history, daily_log, health, history, research_lab, team_moneyline, today

st.set_page_config(page_title="NBA Props", layout="wide", initial_sidebar_state="expanded")

_NAV = {
    "Today's Card": today.render,
    "Research Lab": research_lab.render,
    "Performance": history.render,
    "Daily Log": daily_log.render,
    "Pipeline Health": health.render,
    "Team Moneyline": team_moneyline.render,
    "Card History": card_history.render,
}

st.sidebar.title("Navigation")
choice = st.sidebar.radio("Page", list(_NAV.keys()), label_visibility="collapsed")
_NAV[choice]()
