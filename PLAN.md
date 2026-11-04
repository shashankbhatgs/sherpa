# Interview Sherpa — Phase 1 Implementation Plan

> Historical: this was the Phase 1 build plan and is not kept up to date.
> For current status and remaining work, see `ROADMAP.md`.

## Context

We're bootstrapping a brand-new Python project per `sherpa_spec.md`: a local-first AI agent that helps an SDE2 candidate juggle high-ambiguity technical prep (HLD/LLD/DSA) with low-cognition admin work (application tracking, outreach). The repo is currently empty — only an empty `.venv` (Python 3.14) and the spec exist. There is no source code, no `pyproject.toml`, no package structure.

Phase 1's job is to lay the foundation everything else builds on: the `sherpa/` Python package, the `~/.sherpa/` workspace bootstrap, a hot-reload config manager, and an LLM router that talks to local Ollama. Spec phases 2–4 (tracker, interviewer state machine, UIs + burnout guard) re-plan when started; they're sketched at the end of this doc so we can sanity-check that Phase 1's surfaces will actually carry them.

**Locked decisions:**
- **LLM**: Ollama-only for MVP. Router has a `LLMProvider` Protocol so a cloud provider can drop in later without touching callers.
- **Default local model**: `qwen2.5:7b` (~4.7GB Q4, strong tool-calling + code reasoning).
- **Agent style**: Hand-rolled with the official `ollama` Python SDK — no PydanticAI, no LangChain. Explicit message history, explicit tool-loop, transparent to read.
- **Plan scope**: Phase 1 in detail; phases 2–4 high-level only.

---

## Phase 1 — Detailed Plan

### Repo layout to create

```
interview-sherpa/
├── pyproject.toml              # PEP 621 metadata, deps, hatchling build backend
├── README.md                   # one-screen usage notes
├── .gitignore                  # .venv/, __pycache__/, *.pyc, .pytest_cache/
├── sherpa/
│   ├── __init__.py             # exposes get_config, get_provider, init_workspace
│   ├── workspace.py            # ~/.sherpa bootstrap + path helpers
│   ├── config.py               # hot-reload JSON config manager
│   ├── llm_router.py           # LLMProvider Protocol + OllamaProvider
│   └── seeds/                  # packaged defaults, copied into ~/.sherpa on init
│       ├── config.default.json
│       ├── profile.template.json
│       ├── resume.template.md
│       └── question_bank/
│           ├── hld_distributed_cache.md
│           ├── lld_parking_lot.md
│           └── dsa_lru_cache.md
└── tests/
    ├── __init__.py
    ├── conftest.py             # tmp workspace fixture
    ├── test_workspace.py
    ├── test_config.py
    └── test_llm_router.py
```

### Dependencies — `pyproject.toml`

Prefer `pyproject.toml` over `requirements.txt`: standard metadata, supports editable installs (`pip install -e .`) which Phase 4 needs for the `sherpa` CLI entrypoint, and cleanly separates runtime vs. dev deps.

Phase 1 runtime:
- `ollama>=0.3.0` — official Ollama Python SDK
- `click>=8.1.0` — CLI dep, only stubbed in Phase 1 (real commands in Phase 4)

Dev:
- `pytest>=8.0`
- `pytest-mock`

Deferred to later phases (do **not** add yet):
- `streamlit` (Phase 4)
- `gspread`, `google-auth` (Phase 2)

### `sherpa/workspace.py`

Single source of truth for paths under `~/.sherpa/` and idempotent first-run setup.

```python
SHERPA_DIR: Path = Path.home() / ".sherpa"

def workspace_paths() -> dict[str, Path]: ...
    # keys: root, config, profile, resume, applications,
    #       interviews, question_bank, credentials

def init_workspace(force: bool = False) -> dict[str, str]: ...
    # Creates ~/.sherpa/, interviews/, question_bank/.
    # For each seed file: if missing (or force=True), copy from
    # importlib.resources.files("sherpa.seeds") into the workspace.
    # Returns {path_str: "created" | "exists"} for caller logging.
```

Behavior:
- **Idempotent** by default — re-running never destroys user-edited files.
- **`force=True`** overwrites seeded files only (never `applications.jsonl` or `interviews/*`).
- Uses `importlib.resources` so seeds work after `pip install -e .` from any cwd.

### `sherpa/config.py`

Hot-reload JSON config with mtime polling on each read. No background thread, no `watchdog` dependency — the file is small and human-edited; polling is simpler and good enough.

```python
class Config:
    def __init__(self, path: Path): ...
    def get(self, key: str, default=None): ...   # checks mtime, reloads if changed
    def reload(self) -> None: ...                # force reload
    @property
    def data(self) -> dict: ...                  # snapshot accessor

def get_config() -> Config: ...                  # process-wide singleton
```

Notes:
- On read, compare `self._path.stat().st_mtime` to cached mtime; reload if different.
- If file is mid-write (JSON decode error), keep last known good and log a warning to stderr — never crash callers.
- Singleton wired in `sherpa/__init__.py`; tests reset via a `_reset_config()` helper.

### `sherpa/llm_router.py`

Thin abstraction over chat completions. Keeps a `Protocol` so Phase 2+ modules import a single function (`get_provider`) and never see Ollama's API directly — but the implementation is one straight-through class, no factory factories.

```python
class LLMProvider(Protocol):
    def chat(self,
             messages: list[dict],
             tools: list[dict] | None = None,
             **kwargs) -> dict: ...
    def stream(self,
               messages: list[dict],
               **kwargs) -> Iterator[str]: ...

class OllamaProvider:
    def __init__(self, host: str, model: str): ...

def get_provider(tier: str = "default") -> LLMProvider: ...
    # Reads config.model_routing[tier]; for now only "ollama" is wired.
    # Raises NotImplementedError for unrecognized providers (forward-compat hook).
```

**Tool-loop pattern** (documented in module docstring; full implementation lands in Phase 2 alongside the first real tool):

```text
1. Build tools=[{"type": "function", "function": {...}}, ...] in Ollama's
   function-calling format.
2. Call provider.chat(messages, tools=tools).
3. If response.message.tool_calls is non-empty:
     - For each call: dispatch to a local Python function from a registry dict
       {name: callable}, append the result as a {"role": "tool", ...} message.
     - Loop back to step 2.
4. Otherwise: return response.message.content to caller.
```

Phase 1 ships `OllamaProvider.chat` accepting a `tools` argument — the loop helper lives in Phase 2's `sherpa/agent_loop.py` (introduced where the first real tool, `fetch_full_resume`, is consumed).

### Seeded files

**`sherpa/seeds/config.default.json`**
```json
{
  "ollama_host": "http://localhost:11434",
  "model_routing": {
    "default":     {"provider": "ollama", "model": "qwen2.5:7b"},
    "extraction":  {"provider": "ollama", "model": "qwen2.5:7b"},
    "interviewer": {"provider": "ollama", "model": "qwen2.5:7b"}
  },
  "burnout": {
    "daily_minutes_threshold": 180,
    "cooldown_hours": 12
  },
  "tracker": {
    "spreadsheet_url": null
  }
}
```

Three named tiers from day one (`default` / `extraction` / `interviewer`) so Phases 2–3 callers wire `get_provider("extraction")` etc. without code changes when the user later wants different models per task.

**`profile.template.json`** — empty skeleton with the SDE2 metadata shape (`name`, `years_experience`, `core_competencies: []`, `target_companies: []`, `target_roles: []`).

**`resume.template.md`** — one-line H1 placeholder.

**`question_bank/*.md`** — three seed prompts (one HLD, one LLD, one DSA) matching the filenames in the spec (`hld_distributed_cache.md`, `lld_parking_lot.md`, `dsa_lru_cache.md`). Each is a short markdown brief with problem statement + scale hints.

### Testing strategy

- `conftest.py` provides a `tmp_workspace` fixture that monkeypatches `sherpa.workspace.SHERPA_DIR` to a `tmp_path` and resets the config singleton.
- `test_workspace.py`: `init_workspace()` creates expected tree; second call reports `"exists"` for everything; `force=True` overwrites seeds but never the user-data files.
- `test_config.py`: write file → instantiate `Config` → read → modify file on disk → next read reflects change. Also verify mid-write JSONDecodeError path keeps last good value.
- `test_llm_router.py`: monkeypatch `ollama.Client` with a fake. Verify `get_provider("default")` reads `model_routing.default.model` from config and passes it to `Client.chat`. Verify unknown provider raises `NotImplementedError`.

### Critical files (Phase 1)

| Path | Purpose |
|---|---|
| `pyproject.toml` | Package metadata + deps + hatchling build |
| `sherpa/__init__.py` | Re-exports `init_workspace`, `get_config`, `get_provider` |
| `sherpa/workspace.py` | `~/.sherpa/` paths + idempotent bootstrap |
| `sherpa/config.py` | Hot-reload JSON config (mtime polling) |
| `sherpa/llm_router.py` | `LLMProvider` Protocol + `OllamaProvider` + `get_provider` |
| `sherpa/seeds/config.default.json` | Default config copied on first init |
| `sherpa/seeds/profile.template.json` | Profile skeleton |
| `sherpa/seeds/resume.template.md` | Resume placeholder |
| `sherpa/seeds/question_bank/{hld_distributed_cache,lld_parking_lot,dsa_lru_cache}.md` | Seed prompts |
| `tests/conftest.py` | `tmp_workspace` fixture |
| `tests/test_workspace.py` / `test_config.py` / `test_llm_router.py` | Unit coverage |

### Verification (end-to-end smoke test for Phase 1)

1. `pip install -e ".[dev]"`
2. `pytest` — all tests green.
3. `python -c "from sherpa import init_workspace; print(init_workspace())"` — verify `~/.sherpa/` is created with all seeded files.
4. Edit `~/.sherpa/config.json` (change `model_routing.default.model` to a typo) → next `get_provider("default")` call should reflect the typo without restarting Python.
5. Start Ollama, `ollama pull qwen2.5:7b`.
6. Smoke script:
   ```python
   from sherpa import get_provider
   p = get_provider("default")
   r = p.chat([{"role": "user", "content": "Say 'hello sherpa' and nothing else."}])
   print(r["message"]["content"])
   ```
   Expect a reasonable greeting from the model.

---

## Phases 2–4 — High-Level Outline

These are sketched only so Phase 1's surfaces (`get_config`, `get_provider`, workspace paths) match what later phases actually need. Each re-plans when started.

### Phase 2 — Tracker + Outreach foundations
- `sherpa/tracker.py` — `gspread` client built lazily from `~/.sherpa/credentials.json` + `config.tracker.spreadsheet_url`; append-row API; JSONL fallback to `applications.jsonl` when sheet write fails.
- `sherpa/agent_loop.py` — first real tool-loop helper, used by tracker's natural-language → JSON extraction. Returns a `ValidationGap` when required fields (company, state) are missing instead of inserting a half-row.
- `sherpa/scout.py` (Module 1) — Resume Index pattern: a 4-line metadata string derived from `profile.json`; `fetch_full_resume()` is the first real registered tool, reads `~/.sherpa/resume.md` on demand.
- New deps: `gspread`, `google-auth`.

### Phase 3 — Interviewer state machine
- `sherpa/interviewer.py` — class enforcing 4 states `CLARIFYING → HIGH_LEVEL → DEEP_DIVE → FEEDBACK`. Per-state exit criteria block transitions (e.g., `CLARIFYING` requires DAU/QPS/latency captured before advancing).
- **Session ledger**: markdown file `~/.sherpa/interviews/session_YYYYMMDD_N.md` with YAML front-matter (`state:`, `started_at:`, `topic:`); each turn appends a fenced `## user` / `## assistant` block. State updates rewrite only the front-matter.
- **Crash-resilient hydration**: on resume, parse front-matter for state + replay message history from markdown body into the LLM's `messages` list.
- Semantic code evaluation uses dedicated rubric prompts; reuses `get_provider("interviewer")`.

### Phase 4 — UIs + Burnout Guard
- `sherpa/telemetry.py` — walks `~/.sherpa/interviews/` (and tracker events), sums per-day active minutes by diffing turn timestamps. Compares to `config.burnout.daily_minutes_threshold`.
- **Circuit breaker**: when threshold tripped, `llm_router` (or a thin wrapper used by the interviewer) prepends an "advisory mode" system message and `interviewer.start_session()` refuses new sessions until `cooldown_hours` elapses; tracker writes still pass.
- `cli.py` (Click) — commands: `sherpa init`, `sherpa track "<text>"`, `sherpa interview <topic>`, `sherpa stats`. Registered via `pyproject.toml`'s `[project.scripts]`.
- `app.py` (Streamlit) — tabs: Track / Interview / Scout / Stats. Calls the same `sherpa.*` modules; no business logic in the UI layer.

---

## Out of scope (explicitly)

- No database layer (spec mandates plain files).
- No web scraping (spec mandates clipboard paste for job ingestion).
- No cloud LLM wiring in Phase 1 — only the Protocol seam.
- No `watchdog` / inotify — mtime polling is sufficient for a single-user CLI.
- No tool-loop implementation in Phase 1 — `OllamaProvider.chat` accepts the `tools` parameter, but the dispatch loop lands with Phase 2's first real tool.
