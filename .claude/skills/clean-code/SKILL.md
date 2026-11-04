---
name: clean-code
description: Clean code standards for the Sherpa codebase. Use when writing new code, refactoring, or reviewing a diff in this repo, or when the user mentions "clean code", "code quality", or "review my code". Combines general Python best practices with Sherpa's architecture rules (headless core, plain-file storage, LLM output handling, test conventions).
---

# Clean Code Skill

Apply these standards whenever you write or change code in this repo.
When invoked to review, check the current diff (`git diff` plus untracked files) against every section below and report violations as `file:line - problem - fix`.
When invoked while building, follow the rules as you write and run the checklist at the end before saying the work is done.

---

## 1. Architecture rules (Sherpa-specific, non-negotiable)

- **Headless core.** All business logic lives in the `sherpa/` package.
  `sherpa/cli.py` and `app.py` only gather input, call `sherpa.*`, and format output.
  If a UI file contains parsing, prompting, file writes, or decisions, move that logic into the core.
- **Public API through the package.** New public functions and types are re-exported from `sherpa/__init__.py` and added to `__all__`.
  UI layers call `sherpa.<name>`; sub-module imports in UIs are limited to constants and types.
- **Paths come from `workspace_paths()`.** Never build `~/.sherpa/...` paths by hand.
  A new storage location gets a key in `workspace_paths()`, a `mkdir` in `init_workspace()`, and an entry in `_USER_DATA` if it holds user data.
- **Plain files only.** Append-only JSONL for records, Markdown with front-matter for sessions and drafts.
  No database, no hidden state, no background threads or daemons.
- **Config through `get_config()`.** No hard-coded hosts, model names, or thresholds.
  Read them from config so they hot-reload; give a sensible default in code when a key is missing.
- **LLM access through `get_provider(tier)`.** Pick the tier that fits (`default`, `extraction`, `interviewer`).
  Never instantiate `ollama.Client` outside `llm_router.py`.
- **Tools through `run_tool_loop()`.** Expose capabilities to the model as tool schemas plus a registry dict, as `scout.py` does with `fetch_full_resume`.
  Keep prompts lean: send an index or summary up front and let the model fetch large content on demand.

## 2. Handling LLM output

LLM output is untrusted input.
Treat it like data from an external API.

- Parse with `parse_json_object()` from `agent_loop.py`; never `json.loads` raw model text directly.
- Validate every field you use: check the type, strip strings, and apply explicit defaults.
  Use `value is True`, not truthiness, for booleans (models return `"yes"`, `"false"`, `1`).
- Enforce hard limits (lengths, enums, required fields) in code.
  A prompt instruction is a request, not a guarantee.
- When required data is missing, fail loudly with a typed result (`ValidationGap`) or a specific exception (`OutreachError`).
  Never write a half-complete record.
- Sanitize anything written into front-matter or a single-line field (collapse newlines).
- Prompts are module-level constants named `_SOMETHING_PROMPT`, with placeholders filled by `.format()`.
  State the exact output contract (keys, types, limits) and forbid invented facts when the model writes on the user's behalf.

## 3. Functions and naming

- One function, one job.
  If you need "and" to describe it, split it.
  Aim for under ~40 lines; extract helpers when a block needs a comment to explain what it does.
- Names say what, not how: `draft_outreach`, `resume_index`, `is_blocked`.
  Booleans read as questions (`is_`, `has_`, `used_`).
  No abbreviations beyond common ones (`cfg`, `msg`, `fn`).
- Private helpers start with `_`.
  Module-level constants are `UPPER_CASE`; private ones are `_UPPER_CASE`.
- Prefer early returns over nested `if` blocks.
- Pass dependencies in or fetch them through the existing seams (`get_provider`, `workspace_paths`) so tests can patch them.
- No magic numbers or strings: name them as constants next to the code that uses them.

## 4. Types and data

- Every module starts with `from __future__ import annotations`.
- Type-hint all function signatures, including return types.
  Use built-in generics (`list[str]`, `dict[str, Path]`) and `X | None`.
- Return structured results as `@dataclass` types, not loose dicts or tuples, when a caller reads more than two fields.
- Use `Enum` (subclassing `str`) for fixed sets of states, as `interviewer.State` does.
- Use `pathlib.Path`, never string path concatenation.
  Read and write text with `encoding="utf-8"`.

## 5. Errors

- Catch the narrowest exception that can actually happen (`FileNotFoundError`, `json.JSONDecodeError`).
  A bare `except Exception` is allowed only at an integration boundary that must not crash the caller (e.g. the Google Sheets write), and it must log to stderr with context.
- Core code raises; UI code reports.
  `cli.py` converts known exceptions to `click.ClickException`; `app.py` shows them with `st.error` or `st.warning`.
- Never swallow an error silently.
  Either handle it with a fallback that the user can see, or let it propagate.
- Error messages tell the user what to do next ("Paste one, pipe it in, or pass --file.").

## 6. Files and storage

- Writes are safe to repeat and never destroy user data.
  Choose file names that cannot collide with existing ones (number from the highest existing index, not from a count).
- `init_workspace(force=True)` only reseeds packaged defaults.
  It must never touch `applications.jsonl`, `interviews/`, `outreach/`, or `credentials.json`.
- Never read or log secrets: `credentials.json`, `.env`, API keys.

## 7. Tests

- Every behavior change ships with tests in `tests/test_<module>.py`.
  Cover the happy path, each failure path, and the edge cases you guarded against in code.
- Use the `tmp_workspace` fixture for anything that touches `~/.sherpa/`.
- No real network or LLM calls.
  Patch `get_provider` (with a small fake provider that replays scripted responses) or patch `run_tool_loop` at the module under test.
- Test behavior through public functions; test a private helper directly only when it has tricky logic (e.g. `_trim_to_limit`).
- Test CLI commands with `click.testing.CliRunner`, asserting on exit code and visible output.
- Name tests after the behavior: `test_unusable_output_raises_and_saves_nothing`, not `test_draft_3`.
- The suite stays fast (a few seconds) and deterministic: no sleeps, no wall-clock dependence without patching.

## 8. Comments and docs

- Code says what; comments say why.
  Delete comments that restate the code.
- Public functions get a one-line docstring, plus a short paragraph when the behavior has a non-obvious contract.
- When you add a module, command, storage path, or public API, update `CLAUDE.md` (module table, commands, storage layout, test count) and `README.md` in the same change.
- Do not edit `CHANGELOG.md` or generated files.
- Never use the em dash character in code, comments, or docs; use a plain dash.

## 9. Simplicity

- Build what the spec asks for, no speculative options or extension points.
- Reuse before you write: check `agent_loop.py`, `scout.py`, and `workspace.py` for an existing helper first.
  If two modules need the same logic, move it to the lower layer and have both call it.
- Delete dead code instead of commenting it out.
- No new dependency without a clear need that the standard library or existing deps cannot meet.
  Add it to `pyproject.toml` when you do.

---

## Before you call the work done

Run through this checklist and report the result of each check.
Do not tick anything you did not actually verify.

1. `.venv/bin/pytest -q` passes, and the reported test count matches `CLAUDE.md`.
2. The new behavior was exercised end to end through the real entry point (`sherpa <command>` or `streamlit run app.py`), not only through unit tests.
3. No business logic in `cli.py` or `app.py`.
4. New public API is re-exported from `sherpa/__init__.py`.
5. LLM output is parsed, validated, and limited in code.
6. Docs updated (`CLAUDE.md`, `README.md`).
7. `git diff` contains no debug prints, stray files, secrets, or em dashes.
