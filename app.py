"""Streamlit UI for Interview Sherpa. Run with: streamlit run app.py"""
from __future__ import annotations

import streamlit as st

import sherpa
from sherpa.interviewer import InterviewSession
from sherpa.scout import LINKEDIN_NOTE_LIMIT
from sherpa.telemetry import burnout_status, is_blocked
from sherpa.tracker import ValidationGap

st.set_page_config(page_title="Interview Sherpa", layout="wide")
st.title("Interview Sherpa")

tab_track, tab_interview, tab_scout, tab_stats = st.tabs(["Track", "Interview", "Scout", "Stats"])

# ── Track ──────────────────────────────────────────────────────────────────────
with tab_track:
    st.header("Track Application")
    text = st.text_area("Describe the application in plain English", placeholder="Applied to Stripe for SDE2 yesterday")
    if st.button("Track", key="track_btn") and text.strip():
        st.session_state["track_gap"] = None
        try:
            with st.spinner("Extracting details..."):
                result = sherpa.track(text)
        except sherpa.ProviderError as exc:
            st.error(str(exc))
        else:
            if isinstance(result, ValidationGap):
                st.session_state["track_gap"] = result
            else:
                st.success("Tracked!")
                st.json(result)

    saved = st.session_state.pop("track_saved", None)
    if saved is not None:
        st.success("Tracked!")
        st.json(saved)

    gap: ValidationGap | None = st.session_state.get("track_gap")
    if gap is not None:
        st.warning("Need a bit more before saving.")
        if gap.partial:
            st.caption("Understood so far: " + ", ".join(f"{k}: {v}" for k, v in gap.partial.items()))
        with st.form("track_followup"):
            answers = {}
            if "company" in gap.missing:
                answers["company"] = st.text_input("Company")
            if "stage" in gap.missing:
                answers["stage"] = st.selectbox("Stage", sherpa.application_stages())
            submitted = st.form_submit_button("Save")
        if submitted:
            completed = sherpa.complete_track(gap.partial, answers)
            if isinstance(completed, ValidationGap):
                st.session_state["track_gap"] = completed
                st.error("Still missing: " + ", ".join(completed.missing))
            else:
                st.session_state["track_gap"] = None
                st.session_state["track_saved"] = completed
                st.rerun()

# ── Interview ──────────────────────────────────────────────────────────────────
with tab_interview:
    st.header("Mock Interview")
    st.session_state.setdefault("interview_session", None)
    st.session_state.setdefault("interview_messages", [])
    session: InterviewSession | None = st.session_state["interview_session"]

    blocked = is_blocked()
    if blocked:
        status = burnout_status()
        st.warning(
            f"Burnout guard on: {status['daily_minutes']:.0f}/{status['threshold']} active minutes today. "
            f"New sessions are paused until {status['unblocks_at'] or 'the cooldown ends'}. "
            "An open session switches to advisory mode so you can wrap up."
        )

    if session is None:
        if not blocked:
            questions = {q.title: q.name for q in sherpa.list_questions()}
            custom = "Custom topic"
            choice = st.selectbox("Question", [custom, *questions], help="Pick from the question bank or type your own.")
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                topic = st.text_input(
                    "Topic",
                    placeholder="Design a distributed cache",
                    disabled=choice != custom,
                    value="" if choice == custom else choice,
                )
            with col2:
                kind = st.selectbox(
                    "Kind",
                    [k.value for k in sherpa.Kind],
                    disabled=choice != custom,
                    help="hld: system design, lld: class design, dsa: coding. Bank questions use their own kind.",
                )
            with col3:
                st.write("")
                st.write("")
                start_btn = st.button("Start Session")

            question = questions.get(choice)
            if start_btn and (question or topic.strip()):
                try:
                    session = InterviewSession.start(
                        None if question else topic.strip(),
                        question=question,
                        kind=None if question else sherpa.Kind(kind),
                    )
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
                if msg.get("review"):
                    with st.expander("Code review"):
                        st.markdown(msg["review"])
                if msg.get("advisory"):
                    st.caption("Advisory mode - burnout guard is on")

        user_input = st.chat_input("Your response...")
        if user_input:
            st.session_state["interview_messages"].append({"role": "user", "content": user_input})
            with st.spinner("Thinking..."):
                try:
                    response = session.turn(user_input)
                except sherpa.ProviderError as exc:
                    response = None
                    st.session_state["interview_messages"].pop()
                    st.error(str(exc))
            if response is not None:
                if session.last_review is not None:
                    st.session_state["interview_messages"][-1]["review"] = session.last_review.as_markdown()
                st.session_state["interview_messages"].append(
                    {"role": "assistant", "content": response, "advisory": session.last_reply_advisory}
                )
                st.rerun()

        if st.button("End Session"):
            st.session_state["interview_session"] = None
            st.session_state["interview_messages"] = []
            st.rerun()

# ── Scout ──────────────────────────────────────────────────────────────────────
with tab_scout:
    st.header("Job Scout & Outreach")
    job_post = st.text_area(
        "Paste a job post",
        height=220,
        placeholder="Paste the full job description, including the company and role.",
    )
    if st.button("Draft outreach", key="outreach_btn") and job_post.strip():
        with st.spinner("Reading the job post and drafting..."):
            try:
                st.session_state["outreach_draft"] = sherpa.draft_outreach(job_post)
            except (sherpa.OutreachError, sherpa.ProviderError) as exc:
                st.session_state["outreach_draft"] = None
                st.error(str(exc))

    draft: sherpa.OutreachDraft | None = st.session_state.get("outreach_draft")
    if draft is not None:
        st.subheader(f"{draft.company} - {draft.role}" if draft.role else draft.company)
        match = "Strong match" if draft.aligned else "Weak match"
        source = "full resume used" if draft.used_full_resume else "resume index only"
        (st.success if draft.aligned else st.warning)(f"{match}, {source}")

        st.markdown(f"**LinkedIn note** ({len(draft.linkedin_note)}/{LINKEDIN_NOTE_LIMIT} chars)")
        st.code(draft.linkedin_note, language=None, wrap_lines=True)
        st.markdown("**Email**")
        st.code(f"Subject: {draft.email_subject}\n\n{draft.email_body}", language=None, wrap_lines=True)
        st.caption(f"Saved to {draft.path}")

    with st.expander("Resume index"):
        st.text(sherpa.resume_index())
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

    today = sherpa.daily_activity()
    st.subheader("Today")
    col1, col2 = st.columns(2)
    col1.metric("Applications tracked", today.applications)
    col2.metric("Outreach drafts", today.outreach_drafts)
    st.caption("Only interview minutes count toward the burnout limit. Tracking and outreach are always open.")

    st.subheader("Recent Applications")
    entries = sherpa.load_applications()[-20:]
    if entries:
        st.dataframe(list(reversed(entries)), width="stretch")
    else:
        st.info("No applications tracked yet.")
