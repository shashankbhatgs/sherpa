# Interview Sherpa

Local-first AI agent for SDE2 technical interview prep.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Sherpa uses the [Claude Code](https://claude.com/claude-code) CLI as its model, through your existing Claude Code login (no API key needed).
If you have several Claude Code accounts, set `claude_code.config_dir` in `~/.sherpa/config.json` to the one to use (for example `~/.claude-personal`).

[Ollama](https://ollama.ai) is an optional local backup, used only when a Claude Code call fails:

```bash
ollama pull qwen2.5:7b
```

Each task's model and fallback are set under `model_routing` in `~/.sherpa/config.json`.

## Bootstrap workspace

```bash
python -c "from sherpa import init_workspace; print(init_workspace())"
```

## Draft outreach for a job post

Paste a job description and get a LinkedIn connection note (max 300 characters) and a cold email.
The agent reads a short summary of your profile first and only loads your full resume when the role is a match.
Fill in `~/.sherpa/profile.json` and `~/.sherpa/resume.md` first.

```bash
pbpaste | sherpa outreach          # from the clipboard
sherpa outreach --file job.txt     # from a file
sherpa outreach                    # opens $EDITOR to paste into
```

Drafts are saved to `~/.sherpa/outreach/`.
The same flow is in the Scout tab of the web UI (`streamlit run app.py`).

## Practice from the question bank

```bash
sherpa interview --list
sherpa interview -q lld_parking_lot
```

Add your own questions as Markdown files in `~/.sherpa/question_bank/`; the first `# ` heading is the title.

## Review code

```bash
sherpa review --file solution.py --problem "LRU cache with O(1) get/put"
```

Scores correctness and edge cases, thread safety, and complexity (1-5), with concrete issues.
In `sherpa interview`, paste code between ``` fences and it is reviewed automatically before the interviewer replies.

## Run tests

```bash
pytest
```

## Smoke test LLM

```python
from sherpa import get_provider
p = get_provider("default")
r = p.chat([{"role": "user", "content": "Say 'hello sherpa' and nothing else."}])
print(r["message"]["content"])
```
