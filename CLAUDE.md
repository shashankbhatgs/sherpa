# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project State

Early-stage Python project. The repo currently has a spec (`sherpa_spec.md`) and detailed implementation plan (`PLAN.md`) but **no source code yet**. Phase 1 is the active build target.

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

# Bootstrap the workspace (after Phase 1 is built)
python -c "from sherpa import init_workspace; print(init_workspace())"

# Smoke-test LLM connection (requires Ollama running with qwen2.5:7b pulled)
python -c "from sherpa import get_provider; p = get_provider('default'); print(p.chat([{'role':'user','content':'hello'}])['message']['content'])"
```

The virtual environment is `.venv/` (Python 3.14). Activate with `source .venv/bin/activate`.

## Architecture

### Layer model

Thin presentation shells import a headless core — no business logic in UI layers:

```
app.py (Streamlit)  |  cli.py (Click)
        ↓                    ↓
        └──── sherpa/ package (core) ────┘
```

### `sherpa/` package modules (build order matches phases)

| Module | Phase | Purpose |
|---|---|---|
| `workspace.py` | 1 | Idempotent `~/.sherpa/` bootstrap; all path constants live here |
| `config.py` | 1 | Hot-reload JSON config via mtime polling (no background thread) |
| `llm_router.py` | 1 | `LLMProvider` Protocol + `OllamaProvider`; `get_provider(tier)` |
| `agent_loop.py` | 2 | Tool-dispatch loop for function-calling; first real tool is `fetch_full_resume` |
| `tracker.py` | 2 | gspread sheet writer + JSONL fallback; NL → JSON extraction |
| `scout.py` | 2 | Resume Index (4-line metadata) + on-demand `fetch_full_resume()` |
| `interviewer.py` | 3 | 4-state machine: CLARIFYING → HIGH_LEVEL → DEEP_DIVE → FEEDBACK |
| `telemetry.py` | 4 | Per-day active-minute tallying; circuit breaker for burnout guard |

`sherpa/__init__.py` re-exports `init_workspace`, `get_config`, `get_provider` — callers import from the package, never from sub-modules directly.

### Key design decisions

**Config** (`sherpa/config.py`): reads `~/.sherpa/config.json`, checks `st_mtime` on every `.get()` call, reloads if changed. On `JSONDecodeError` during a mid-write, it keeps the last known-good value and logs to stderr — never crashes callers. Singleton exposed via `get_config()`; tests reset it with `_reset_config()`.

**LLM routing** (`sherpa/llm_router.py`): three named tiers in config — `default`, `extraction`, `interviewer` — so each phase can use a different model without code changes. MVP wires only Ollama (`qwen2.5:7b`). `LLMProvider` is a `Protocol`; cloud providers drop in later without touching callers. Tool-loop dispatch lives in `agent_loop.py` (Phase 2), not in the provider class.

**Workspace** (`~/.sherpa/`): append-only JSONL (`applications.jsonl`) and Markdown session logs under `interviews/`. `init_workspace(force=False)` never overwrites user-edited files; `force=True` only reseeds the packaged defaults (never touches `applications.jsonl` or `interviews/*`). Seeds are packaged under `sherpa/seeds/` and copied via `importlib.resources`.

**Interviewer state machine**: session state lives in YAML front-matter of `~/.sherpa/interviews/session_YYYYMMDD_N.md`. Crash-resilient: on resume, parse front-matter for current state and replay the markdown body into the LLM's `messages` list.

**Burnout guard**: `telemetry.py` diffs turn timestamps across session files to sum daily active minutes. When `config.burnout.daily_minutes_threshold` is exceeded, the circuit breaker prepends an advisory system message and blocks new interview sessions until `cooldown_hours` elapses; tracker writes always pass through.

### Storage layout

```
~/.sherpa/
├── config.json          # hot-reloaded; edit live
├── credentials.json     # Google Service Account key (Phase 2)
├── profile.json         # user metadata + competencies
├── resume.md            # master resume in Markdown
├── applications.jsonl   # append-only tracker fallback
├── interviews/          # one .md per session, YAML front-matter + turn blocks
└── question_bank/       # pre-seeded HLD/LLD/DSA prompt files
```

### Build backend

`pyproject.toml` with hatchling. Runtime deps added per phase — do **not** add `streamlit`, `gspread`, or `google-auth` until their phase starts.

### Testing conventions

- `tests/conftest.py` provides a `tmp_workspace` fixture: monkeypatches `sherpa.workspace.SHERPA_DIR` to `tmp_path` and resets the config singleton. All tests that touch the filesystem use this fixture.
- LLM tests monkeypatch `ollama.Client` — never make real network calls.
- Workspace tests verify idempotency and that `force=True` never touches user-data files.
