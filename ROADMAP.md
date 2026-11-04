# Interview Sherpa - Roadmap

This is the plan for the remaining work.
`sherpa_spec.md` defines what the product should do, and `PLAN.md` is the historical Phase 1 build plan.
Last updated: 2026-11-04.

---

## Status

All four spec modules exist and work end to end on Claude Code (with Ollama as the fallback).
117 tests pass.

| Area | State |
|---|---|
| Workspace, hot-reload config | Done |
| LLM routing (Claude Code CLI primary, Ollama fallback) | Done |
| Tool loop + Resume Index + `fetch_full_resume` | Done |
| Tracker (NL extraction, Google Sheets, JSONL fallback) | Done; missing-field follow-up pending (R4) |
| Outreach (LinkedIn note + cold email) | Done |
| Interviewer 4-state machine, crash-resilient ledger | Done; code rubric (R2) and question bank (R3) pending |
| Burnout guard | Done, including advisory mode; tracker activity in stats (R5) pending |
| CLI + Streamlit UI | Done |

---

## Feature work

Ordered by priority.
Each item follows `.claude/skills/clean-code/SKILL.md` and is done only when its tests pass and it has been run end to end through `sherpa` and `streamlit run app.py`.

### R1. Burnout advisory mode - Done

**Spec:** Module 4, "Hard State-Shifting".
When daily active minutes pass the threshold, the persona shifts to an advisory profile that declines new engineering work but stays available for low-stress tracking.

**Today:** `InterviewSession.start()` refuses new sessions, but `turn()` on an already-open session keeps running at full intensity, and nothing changes the persona.

**Approach:**
- Add `_PROMPT_ADVISORY` in `sherpa/interviewer.py`: a supportive coach that will not start new problems, briefly wraps up the current thread, and suggests resuming after the cooldown.
- In `InterviewSession.turn()`, check `telemetry.is_blocked()` before calling the model.
  When blocked, use the advisory prompt as the system message for that turn and do not advance state.
- Record `advisory: true` on the turn header (or front-matter) so the ledger shows when the guard took over.
- CLI REPL and Streamlit Interview tab show a one-line banner when a reply came from advisory mode.
- Tracker and outreach stay ungated.

**Tests:** blocked session gets the advisory system prompt; state does not advance while blocked; unblocked sessions are unchanged; banner text appears in CLI output.

**Done when:** an in-progress interview switches to advisory replies the moment the threshold is crossed.

### R2. Code evaluation rubric

**Spec:** Module 3, "Semantic Code Evaluation" - check thread safety, edge cases, and asymptotic complexity.

**Today:** only the general SDE2 feedback rubric in the FEEDBACK state.

**Approach:**
- Add `evaluate_code(code: str, problem: str) -> CodeReview` in a new `sherpa/code_review.py`, using the `interviewer` tier.
- Fixed rubric prompt with three scored dimensions (correctness and edge cases, thread safety, time/space complexity) plus concrete fixes; return JSON parsed with `parse_json_object()` and validated in code (scores clamped to 1-5).
- In `InterviewSession.turn()`, detect a fenced code block in the user's message and append the rubric result to the interviewer's context for that turn, and to the session ledger.
- Expose as `sherpa review --file solution.py` for standalone use.

**Tests:** rubric JSON parsing and clamping; malformed output raises a typed error; code blocks trigger evaluation, plain prose does not.

**Done when:** pasting an LRU cache solution in a DSA session produces a scored review that names a real issue (e.g. missing lock, O(n) eviction).

### R3. Use the question bank

**Spec:** Module 3 and the backlog row "Pre-seeded Local MD Templates".

**Today:** `init_workspace()` seeds `~/.sherpa/question_bank/*.md`, but nothing reads them.

**Approach:**
- Add `list_questions()` and `load_question(name)` in `sherpa/interviewer.py` (or a small `question_bank.py`), reading titles from each file's first `#` heading.
- `sherpa interview --question lld_parking_lot` starts a session seeded with that file; `sherpa interview --list` prints the bank.
- Store `question:` in the session front-matter and include the question text in the CLARIFYING system prompt; `resume()` reloads it.
- Streamlit Interview tab gets a picker next to the free-text topic.

**Tests:** listing reads headings; unknown name errors clearly; started session carries the question through resume.

**Done when:** every seeded question can be started from both CLI and UI, and resumed after a crash.

### R4. Tracker missing-field follow-up

**Spec:** Module 2, "Validation Failure Loop" - pause insertion and ask for the missing parameter.

**Today:** `track()` returns `ValidationGap`; the CLI and UI print the missing fields and the user must retype the whole update.

**Approach:**
- Add `ValidationGap.partial: dict` holding what was extracted.
- Add `complete_track(partial: dict, answers: dict) -> dict | ValidationGap` in `sherpa/tracker.py` that merges answers and writes the row.
- CLI `track` prompts for each missing field (state offered as a choice list) and then calls `complete_track`.
- Streamlit Track tab shows inputs for just the missing fields, kept in `st.session_state`.

**Tests:** partial extraction is preserved; answers merge and write one row; invalid state choice is rejected; nothing is written until all required fields exist.

**Done when:** "applied somewhere today" leads to one question ("Which company?") and a single correct row.

### R5. Count tracker activity in telemetry

**Plan:** `PLAN.md` Phase 4 says telemetry walks interviews "and tracker events".

**Today:** `daily_active_minutes()` reads only `interviews/`.

**Decision needed first:** the spec treats tracking as low-stress, so it may be better to *show* tracker and outreach activity in `sherpa stats` without counting it toward the burnout threshold.
Recommended: show it, do not count it.

**Approach (recommended option):** add a `tracked_at` timestamp to JSONL entries and outreach front-matter (already present), and add "applications today" and "outreach drafts today" to `sherpa stats` and the Stats tab.

**Done when:** stats show today's interview minutes, applications, and outreach drafts separately.

---

## Repo and portfolio readiness

These matter for using Sherpa as a portfolio project.

- [ ] **Fix the `@claude` GitHub Action.** The `ANTHROPIC_API_KEY` secret (set 2026-06-10) is being rejected. Re-run `/install-github-app` with the Claude subscription option, or replace the secret.
- [ ] **Make the repo public** once secrets and personal data are confirmed absent from history.
- [ ] **Add a demo** to the README: a short GIF of `sherpa outreach` and `sherpa interview`, plus a screenshot of the Streamlit UI, since reviewers cannot run it without Claude Code or Ollama.
- [ ] **Rewrite the README** as a project page: problem, architecture diagram, key design decisions, how AI tools were used to build it.
- [ ] **Clean up branches and issues:** delete `claude/issue-2-20260610-0714` (stale Phase 1 copy) and the merged `add-claude-github-actions-*` branches; close issue #2 with a link to this roadmap.
- [ ] **Add CI** that runs `pytest` on every push.

---

## Backlog (from the spec, not planned)

- Execute code in a sandbox (Docker or subprocess) to run real unit tests on solutions.
- Migrate older history to SQLite if file scans get slow.
- Compare grading between local and cloud models.
- Ingest fresh interview questions from engineering forums.
- Anthropic API provider (native tool calling) as an alternative to the Claude Code CLI.
