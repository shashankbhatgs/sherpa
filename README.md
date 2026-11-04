# Interview Sherpa

Local-first AI agent for SDE2 technical interview prep.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requires [Ollama](https://ollama.ai) running locally with `qwen2.5:7b` pulled:

```bash
ollama pull qwen2.5:7b
```

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
