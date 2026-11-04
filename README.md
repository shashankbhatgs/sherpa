# Interview Sherpa

[![Tests](https://github.com/shashankbhatgs/sherpa/actions/workflows/tests.yml/badge.svg)](https://github.com/shashankbhatgs/sherpa/actions/workflows/tests.yml)

A local-first AI coach for software engineers who are job hunting.
It runs realistic mock interviews, keeps your application tracker up to date from plain-English notes, drafts tailored outreach, and stops you before you burn out.

## Why

Interview prep pulls in two directions at once.
There is hard, high-focus work: system design, low-level design, and data structures under interview pressure.
And there is draining admin work: tracking applications across job boards and referrals, and writing cold messages to recruiters.

Doing both for weeks wears people down, and tired candidates interview worse.
Sherpa takes the admin off your plate, makes practice feel like the real thing, and enforces a daily study limit.

## What it does

**Mock interviews that follow a real interview's structure.**
The interviewer moves through four stages: clarifying requirements, high-level design, a deep dive into failure modes and consistency, and finally structured feedback against an SDE2 rubric.
It will not let you skip ahead: you cannot start designing until you have pinned down scale (users, requests per second, latency targets).
Sessions are saved as you go, so a crash or closed terminal never loses your progress.

**Code review during practice.**
Paste code into an interview and it is scored for correctness and edge cases, thread safety, and time/space complexity before the interviewer replies, so the follow-up questions target your actual weak spots.

```text
Code review - correctness 3/5, thread safety 2/5, complexity 1/5
Time: get O(n), put O(n) | Space: O(capacity)
- self.order.remove(key) in get is an O(n) list scan, violating the required O(1) get
- Capacity 0: order.pop(0) is called on an empty list and raises IndexError
- No locking around data/order, so concurrent get/put can corrupt the order list
```

**Application tracking from one sentence.**
Write `"Applied to Stripe for SDE2 via a referral from Priya"` and it becomes a structured row (company, role, status, date, notes) in a local file and, optionally, a Google Sheet.
If something essential is missing, it asks for just that field instead of guessing.

**Tailored outreach.**
Paste a job post and get a LinkedIn connection note (under 300 characters) and a short cold email.
The model first sees a four-line summary of your profile, and only reads your full resume when the role is a real match, so messages cite your actual projects and never invent experience.

**A burnout guard.**
Sherpa tracks active interview-practice minutes per day.
Past your limit, it refuses new sessions and turns any open session into a short wrap-up with a coach instead of more questions.
Tracking and outreach always stay available, because that work is low-stress.

## Quick start

Requirements: Python 3.14+, and either the [Claude Code](https://claude.com/claude-code) CLI (logged in) or [Ollama](https://ollama.com).

```bash
git clone https://github.com/shashankbhatgs/sherpa.git
cd sherpa
python -m venv .venv && source .venv/bin/activate
pip install -e .

sherpa init        # creates ~/.sherpa with default config and sample questions
```

Then fill in `~/.sherpa/profile.json` (skills, years of experience, target roles) and `~/.sherpa/resume.md`.

```bash
sherpa interview --list                      # see the question bank
sherpa interview -q hld_distributed_cache    # start a mock interview
sherpa interview                             # resume today's unfinished session
sherpa track "Got an OA from Zerodha for SDE2 today"
pbpaste | sherpa outreach                    # draft outreach for a copied job post
sherpa review --file solution.py --problem "LRU cache"
sherpa stats                                 # today's practice minutes and activity

streamlit run app.py                         # the same features in a web UI
```

In an interview, paste code between triple-backtick fences; type `quit` to pause.

## Choosing a model

Sherpa works with the model you already have:

- **Claude Code** (default): uses your existing Claude Code login, so no API key is needed.
- **Ollama** (fallback): a free local model such as `qwen2.5:7b`, used automatically if a Claude Code call fails.

Each task (drafting, extraction, interviewing) picks its own model in `~/.sherpa/config.json`, and changes apply without a restart:

```json
"model_routing": {
  "interviewer": {
    "provider": "claude_code", "model": "sonnet",
    "fallback": {"provider": "ollama", "model": "qwen2.5:7b"}
  }
}
```

If you use several Claude Code accounts, set `claude_code.config_dir` to the one Sherpa should use.
To mirror applications to Google Sheets, put a service-account key at `~/.sherpa/credentials.json`, share the sheet with that account, and set `tracker.spreadsheet_url`.

## How it works

```text
   Streamlit web UI (app.py)        Click CLI (sherpa/cli.py)
                │                              │
                └──────────────┬───────────────┘
                               ▼
                   sherpa/ core package
   interviewer · code_review · question_bank · tracker · scout
        telemetry (burnout guard) · agent_loop (tool calls)
           llm_router: Claude Code CLI ──fallback──▶ Ollama
                               │
                               ▼
              ~/.sherpa/  plain JSON, JSONL and Markdown
```

Design choices worth noting:

- **Both interfaces are thin.** The CLI and web UI only collect input and display results; every rule lives in one core package, so they always behave the same.
- **Plain files, no database.** Config is JSON, applications are an append-only JSONL log, and each interview is a Markdown file with a small header recording its stage. Everything is readable, editable, and easy to back up.
- **Crash-safe sessions.** Every turn is written to disk as it happens; resuming rebuilds the conversation from the file.
- **Model output is treated as untrusted input.** JSON from the model is validated in code: scores are clamped, statuses must be from a fixed list, placeholder values like "Unknown" count as missing, and message lengths are enforced. Prompts ask; code guarantees.
- **Lean prompts.** The model gets a short profile summary and calls a tool to fetch the full resume only when it needs it.
- **Backend-agnostic tool calling.** Tools work the same on Ollama's native function calling and on the Claude Code CLI, where tool calls are expressed as a small JSON protocol.
- **Config hot-reloads.** Edit `~/.sherpa/config.json` while Sherpa runs and the next call picks it up.

## Your data

Everything lives in `~/.sherpa/` on your machine:

```text
~/.sherpa/
├── config.json          # models, burnout limit, tracker settings
├── profile.json         # your skills and target roles
├── resume.md            # your resume in Markdown
├── applications.jsonl   # one line per tracked application
├── interviews/          # one Markdown file per mock interview
├── outreach/            # saved LinkedIn notes and emails
└── question_bank/       # interview questions; add your own as .md files
```

Prompts are sent only to the model provider you configure.
With Ollama, nothing leaves your machine.

## Development

```bash
pip install -e ".[dev]"
pytest                                # 191 tests, a few seconds, no network calls
python -m pyflakes sherpa app.py tests
```

CI runs both on every push.
`CLAUDE.md` describes the architecture in detail, and `ROADMAP.md` lists planned work, such as interview flows tailored to coding and object-oriented design questions.

## Acknowledgements

Inspired by Sau Sheong's [Felix agent](https://sausheong.com/i-built-my-own-ai-agent-and-named-it-after-a-cat-471b456dc26c) and its local-first, plain-file design.
