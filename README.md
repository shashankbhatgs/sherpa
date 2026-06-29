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
