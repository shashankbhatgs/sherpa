"""Streamlit UI for Interview Sherpa. Run with: streamlit run app.py"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

import sherpa
from sherpa.interviewer import InterviewSession, State
from sherpa.telemetry import burnout_status, is_blocked
from sherpa.tracker import ValidationGap
from sherpa.workspace import workspace_paths

st.set_page_config(page_title="Interview Sherpa", layout="wide")
st.title("Interview Sherpa")

tab_track, tab_interview, tab_scout, tab_stats = st.tabs(["Track", "Interview", "Scout", "Stats"])

# ── Track ──────────────────────────────────────────────────────────────────────
with tab_track:
    st.header("Track Application")
    text = st.text_area("Describe the application in plain English", placeholder="Applied to Stripe for SDE2 yesterday")
    if st.button("Track", key="track_btn") and text.strip():
        with st.spinner("Extracting details..."):
            result = sherpa.track(text)
        if isinstance(result, ValidationGap):
            st.warning(f"Missing required fields: {', '.join(result.missing)}. Try adding more detail.")
        else:
            st.success("Tracked!")
            st.json(result)

# ── Interview ──────────────────────────────────────────────────────────────────
with tab_interview:
    st.header("Mock Interview")

    if is_blocked():
        status = burnout_status()
        st.error(
            f"Burnout guard active: {status['daily_minutes']:.0f}/{status['threshold']} active minutes today. "
            f"Resume after {status['unblocks_at'] or 'later'}."
        )
    else:
        if "interview_session" not in st.session_state:
            st.session_state["interview_session"] = None
            st.session_state["interview_messages"] = []

        session: InterviewSession | None = st.session_state["interview_session"]

        if session is None:
            col1, col2 = st.columns([3, 1])
            with col1:
                topic = st.text_input("Topic", placeholder="Design a distributed cache")
            with col2:
                st.write("")
                st.write("")
                start_btn = st.button("Start Session")

            if start_btn and topic.strip():
                try:
                    session = InterviewSession.start(topic.strip())
                    st.session_state["interview_session"] = session
                    st.session_state["interview_messages"] = []
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))
        else:
            st.caption(f"Session: {session.path.name}  |  State: **{session.state.value}**")

            for msg in st.session_state["interview_messages"]:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            user_input = st.chat_input("Your response...")
            if user_input:
                st.session_state["interview_messages"].append({"role": "user", "content": user_input})
                with st.spinner("Thinking..."):
                    response = session.turn(user_input)
                st.session_state["interview_messages"].append({"role": "assistant", "content": response})
                st.rerun()

            if st.button("End Session"):
                st.session_state["interview_session"] = None
                st.session_state["interview_messages"] = []
                st.rerun()

# ── Scout ──────────────────────────────────────────────────────────────────────
with tab_scout:
    st.header("Resume Scout")
    index = sherpa.resume_index()
    st.text(index)
    if st.button("Show full resume"):
        full = sherpa.fetch_full_resume()
        if full:
            st.markdown(full)
        else:
            st.info("No resume found at ~/.sherpa/resume.md")

# ── Stats ──────────────────────────────────────────────────────────────────────
with tab_stats:
    st.header("Prep Stats")

    status = burnout_status()
    col1, col2, col3 = st.columns(3)
    col1.metric("Active minutes today", f"{status['daily_minutes']:.1f}", delta=None)
    col2.metric("Daily threshold", str(status["threshold"]))
    col3.metric("Status", "BLOCKED" if status["blocked"] else "OK")

    if status["blocked"]:
        st.error(f"Cooldown active. Unblocks at: {status['unblocks_at']}")

    st.subheader("Recent Applications")
    apps_path = workspace_paths()["applications"]
    if apps_path.exists():
        lines = [l for l in apps_path.read_text().splitlines() if l.strip()]
        if lines:
            entries = []
            for line in lines[-20:]:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            if entries:
                st.dataframe(entries, use_container_width=True)
        else:
            st.info("No applications tracked yet.")
    else:
        st.info("No applications tracked yet.")
