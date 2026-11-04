# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project State

All 4 phases are complete. The `sherpa/` package, CLI, Streamlit UI, and full test suite are built and pushed.
191 tests pass. The active focus is maintenance, refinement, and real usage.
Remaining work and its implementation plan live in `ROADMAP.md`.

## Code Standards

Before writing or changing code, load the `/clean-code` skill (`.claude/skills/clean-code/SKILL.md`) and follow it.
Run its "Before you call the work done" checklist before every commit.

## Commands

```bash
# Install (editable + dev deps) — required before running anything
pip install -e ".[dev]"

# Run all tests
pytest

# Run a single test file
pytest tests/test_config.py

# Run a single test by name
pytest tests/test_config.py::test_hot_reload

# Lint (CI runs this and pytest on every push: .github/workflows/tests.yml)
python -m pyflakes sherpa app.py tests

# Bootstrap the workspace
python -c "from sherpa import init_workspace; print(init_workspace())"

# Smoke-test LLM connection (uses the Claude Code CLI login; falls back to Ollama)
python -c "from sherpa import get_provider; p = get_provider('default'); print(p.chat([{'role':'user','content':'hello'}])['message']['content'])"

# Run the CLI
sherpa --help
sherpa init
sherpa track "Applied to Stripe for SDE2"
sherpa interview "Design a distributed cache"
sherpa interview --list           # question bank; start one with: sherpa interview -q lld_parking_lot
sherpa stats
pbpaste | sherpa outreach        # or: sherpa outreach --file job.txt
sherpa review --file solution.py --problem "LRU cache"

# Run the Streamlit UI
streamlit run app.py
```

The virtual environment is `.venv/` (Python 3.14). Activate with `source .venv/bin/activate`.

## Architecture

### Layer model

Thin presentation shells import a headless core — no business logic in UI layers:

```
app.py (Streamlit)  |  sherpa/cli.py (Click)
        ↓                    ↓
        └──── sherpa/ package (core) ────┘
```

### `sherpa/` package modules

| Module | Purpose |
|---|---|
| `workspace.py` | Idempotent `~/.sherpa/` bootstrap; all path constants live here |
| `config.py` | Hot-reload JSON config via mtime polling (no background thread) |
| `llm_router.py` | `LLMProvider` Protocol; `ClaudeCodeProvider` (primary), `OllamaProvider` (backup), `FallbackProvider`; `get_provider(tier)` |
| `agent_loop.py` | Generic tool-dispatch loop for function-calling |
| `tracker.py` | NL → JSON extraction; gspread sheet writer + JSONL fallback |
| `scout.py` | Resume index (4-line metadata) + on-demand `fetch_full_resume()` tool; `draft_outreach()` cold-message engine |
| `code_review.py` | Rubric scoring of code (correctness/edge cases, thread safety, complexity); `extract_code()` finds fenced blocks |
| `question_bank.py` | Lists/loads `~/.sherpa/question_bank/*.md` (title from first `# ` heading) |
| `interviewer.py` | 4-state machine: CLARIFYING → HIGH_LEVEL → DEEP_DIVE → FEEDBACK |
| `telemetry.py` | Per-day active-minute tallying; burnout circuit breaker; `daily_activity()` (interview minutes, applications, outreach drafts) |

`sherpa/__init__.py` re-exports all public API — callers import from the package, never from sub-modules directly:
`init_workspace`, `get_config`, `get_provider`, `track`, `complete_track`, `load_applications`, `ValidationGap`, `VALID_STATES`, `resume_index`, `fetch_full_resume`, `draft_outreach`, `OutreachDraft`, `OutreachError`, `evaluate_code`, `CodeReview`, `CodeReviewError`, `list_questions`, `load_question`, `Question`, `QuestionNotFoundError`, `InterviewSession`, `State`, `burnout_status`, `is_blocked`, `daily_active_minutes`, `daily_activity`, `DailyActivity`.

### Key design decisions

**Config** (`sherpa/config.py`): reads `~/.sherpa/config.json`, checks `st_mtime` on every `.get()` call, reloads if changed. On `JSONDecodeError` during a mid-write, it keeps the last known-good value and logs to stderr — never crashes callers. Singleton exposed via `get_config()`; tests reset it with `_reset_config()`.

**LLM routing** (`sherpa/llm_router.py`): three named tiers in config - `default`, `extraction`, `interviewer` - each with a `provider`, `model`, and optional `fallback` spec. The seeded default is Claude Code (`sonnet`, `haiku` for extraction) with Ollama `qwen2.5:7b` as fallback. `ClaudeCodeProvider` runs `claude -p --output-format json` as a subprocess with Claude Code's built-in tools disabled, using the CLI's own login (`claude_code.config_dir` sets `CLAUDE_CONFIG_DIR` to pick an account). Sherpa's tools are described in the system prompt; a reply that is exactly `{"tool_call": {...}}` for a known tool becomes a tool call, so `run_tool_loop()` is backend-agnostic. The CLI exits 0 even when logged out, so errors are detected from the JSON `is_error`/`subtype`. Backend failures raise `ProviderError`; `FallbackProvider` retries on the backup and raises `ProviderError` if both fail. The CLI group and `app.py` turn `ProviderError` into a clean message. `LLMProvider` is a `Protocol`; new providers drop in via `_build_provider()`.

**Agent loop** (`sherpa/agent_loop.py`): `run_tool_loop()` handles the chat → tool_calls → dispatch → append → repeat cycle. `FETCH_RESUME_TOOL` and `SCOUT_REGISTRY` from `scout.py` are the registered tools passed to both the tracker and interviewer. `parse_json_object()` is the shared lenient parser for JSON in LLM output.

**Workspace** (`~/.sherpa/`): append-only JSONL (`applications.jsonl`) and Markdown logs under `interviews/` and `outreach/`. `init_workspace(force=False)` never overwrites user-edited files; `force=True` only reseeds packaged defaults (never touches `applications.jsonl`, `interviews/*`, or `outreach/*`). Seeds are packaged under `sherpa/seeds/` and copied via `importlib.resources`.

**Interviewer state machine** (`sherpa/interviewer.py`): session state lives in YAML front-matter of `~/.sherpa/interviews/session_YYYYMMDD_N.md`. Turn blocks are written as `## role TIMESTAMP\ncontent`. Crash-resilient: `resume()` parses front-matter for state and replays markdown turns into the LLM's `messages` list. `start(topic=None, question=None)` checks the burnout circuit breaker before creating a session. With `question`, the bank file's text is appended to every state's system prompt (`_system_message()`), the name is stored as `question:` in front-matter, and `resume()` reloads it (a deleted file is logged and skipped).

**Tracker validation** (`sherpa/tracker.py`): extraction output goes through `_normalize()` - known string fields only, whitespace collapsed, placeholder values ("Unknown", "N/A") dropped, state mapped onto `VALID_STATES` or dropped - and a missing date defaults to today. Missing `company`/`state` returns `ValidationGap(missing, partial)` without writing; the CLI prompts for just those fields (state as a choice) and the Track tab shows a form, both finishing with `complete_track(partial, answers)`. Every recorded entry gets `tracked_at` and is always appended to `applications.jsonl` (the source of truth for stats); Google Sheets is a mirror. `load_applications()` reads the ledger, skipping malformed lines.

**Burnout guard** (`sherpa/telemetry.py`): parses ISO timestamps from turn headers to sum daily active minutes. Gaps ≥10 min between turns are excluded (idle time). When `config.burnout.daily_minutes_threshold` is exceeded, `is_blocked()` returns True and `InterviewSession.start()` raises `RuntimeError`. An open session keeps working in advisory mode: `turn()` swaps in `_PROMPT_ADVISORY` for that call (no tools, no state advance), sets `last_reply_advisory`, and marks the turn header `## assistant TS advisory`. Tracker and outreach always pass through.

**Outreach engine** (`sherpa/scout.py`): `draft_outreach(job_post)` sends only the Resume Index up front; the model calls `fetch_full_resume` when it judges the role a match. Returns an `OutreachDraft` (LinkedIn note capped at 300 chars, email subject + body) and saves it to `~/.sherpa/outreach/outreach_YYYYMMDD_N.md`. An over-long note gets one model rewrite, then a sentence/word-boundary trim. Unusable model output raises `OutreachError` and saves nothing. Not gated by the burnout guard (low-stress admin work).

**Code review** (`sherpa/code_review.py`): `evaluate_code()` asks the `interviewer` tier for a JSON rubric; scores are coerced and clamped to 1-5 in code, issues capped at 5, and a missing score raises `CodeReviewError`. During an interview, a user message with a ``` block is reviewed before the reply: the review is written to the ledger as a `## review TS` block (skipped by `_parse_turns`, so not replayed on resume), injected as system context for that turn only, and exposed as `session.last_review`. A failed review is logged and the turn continues.

**CLI** (`sherpa/cli.py`): six Click commands. The interview REPL reads multi-line input when a line opens a ``` fence. `sherpa outreach` reads the job post from `--file`, stdin, or `$EDITOR`. `sherpa interview` runs a REPL loop with state-transition display; with no topic argument it resumes `InterviewSession.latest_open()` (most recent non-FEEDBACK session from today, numeric sort). `sherpa stats` shows interview minutes against the limit plus today's applications and outreach drafts, which never count toward it.

**Streamlit UI** (`app.py`): four tabs - Track, Interview (chat UI with `st.session_state`), Scout (job post to outreach draft), Stats. No business logic; all calls go to `sherpa.*` imports.

### Storage layout

```
~/.sherpa/
├── config.json          # hot-reloaded; edit live
├── credentials.json     # Google Service Account key
├── profile.json         # user metadata + competencies
├── resume.md            # master resume in Markdown
├── applications.jsonl   # append-only tracker ledger (sheet is a mirror)
├── interviews/          # session_YYYYMMDD_N.md — YAML front-matter + timestamped turn blocks
├── outreach/            # outreach_YYYYMMDD_N.md - drafted LinkedIn note + email + job post
└── question_bank/       # pre-seeded HLD/LLD/DSA prompt files
```

### Testing conventions

- `tests/conftest.py` provides a `tmp_workspace` fixture: monkeypatches `sherpa.workspace.SHERPA_DIR` to `tmp_path` and resets the config singleton before and after each test.
- LLM calls are monkeypatched — no real network calls in tests.
- Telemetry tests monkeypatch `daily_active_minutes` and `_last_turn_timestamp` directly.
- Claude Code tests patch `sherpa.llm_router.subprocess.run`; Ollama tests patch `sherpa.llm_router.ollama.Client` and switch routing to Ollama-only.
- 191 tests across 11 modules; full suite runs in ~3 seconds.

### Skills

`.claude/skills/clean-code/SKILL.md` - `/clean-code` skill: Python and Sherpa-specific code standards (headless core, LLM output validation, storage, tests) plus a done-checklist. Apply when writing or reviewing code.

`.claude/skills/commit/SKILL.md` — `/commit` skill for creating Conventional Commits. Analyzes the full diff, groups changes into logical units, proposes a multi-commit plan, warns on pytest failures, hard-blocks sensitive files, warns on main/master. Supports `--amend`.
