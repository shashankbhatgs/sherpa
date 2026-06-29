---
name: commit
description: Create one or more well-formed git commits. Analyzes the full diff, groups changes into logical units, and executes them as separate Conventional Commits — one per coherent concern. Warns on pytest failures, hard-blocks sensitive files, warns on main/master, supports --amend. Never pushes.
---

# Commit Skill

Follow these steps in order. If invoked as `/commit --amend`, jump directly to the [Amend](#amend) section.

---

## Step 1 — Nothing to commit?

Run `git status`. If the working tree is clean, report "Nothing to commit." and stop.

---

## Step 2 — Sensitive file hard block

Before touching staging, scan all modified and untracked files for sensitive patterns.

**Blocked filenames:** `.env`, `*.env`, `.env.*`, `credentials.json`, `*_key.json`, `*.pem`, `*.p12`, `*.pfx`, `*.key`

**Blocked content** (check files < 10 KB): strings starting with `sk-`, `AKIA`, `ghp_`, `xoxb-`, `xoxp-`, or lines containing `-----BEGIN`

If any match is found, **stop immediately**. Name the file and do not proceed under any circumstances.

---

## Step 3 — Branch warning

Run `git branch --show-current`. If the branch is `main` or `master`, warn:

```
Warning: you are about to commit directly to `{branch}`.
```

Ask for confirmation before continuing.

---

## Step 4 — Pre-commit check (warn only)

Run pytest once upfront before any staging:

```bash
source .venv/bin/activate && pytest -q 2>&1 | tail -20
```

If pytest fails, show the summary and ask: "Tests are failing — continue anyway?" If no, stop.

---

## Step 5 — Analyze and plan commits

Run `git status` and `git diff` to read all changes in full.

Group the changes into **logical commit units** — each unit should represent one coherent concern. Good splitting signals:

- A new module and its tests → one commit per module (e.g. `feat(tracker): ...` and `test(tracker): ...` can be one commit if they landed together, or two if the test is substantial)
- An unrelated bug fix found alongside a feature → separate commits
- Config/dependency changes → separate from feature code (e.g. `chore: add gspread dependency`)
- Refactors that touch many files but are mechanical → one commit
- Multiple independent features changed in the same session → one commit each

**Present the full commit plan** before doing anything:

```
Proposed commit plan (3 commits):

  1. feat(tracker): add NL-to-JSON extraction and JSONL fallback
     Files: sherpa/tracker.py, tests/test_tracker.py

  2. feat(agent_loop): add generic tool-dispatch loop
     Files: sherpa/agent_loop.py, tests/test_agent_loop.py

  3. chore: add gspread and google-auth dependencies
     Files: pyproject.toml
```

Ask: **"Proceed with this plan? (y / edit / split differently)"**

- `y` → execute commits in order
- `edit` → let the user adjust groupings or messages before proceeding
- `split differently` → ask the user how they want the changes grouped

---

## Step 6 — Execute each commit in sequence

For each planned commit:

1. **Stage** the exact files for this commit:
   ```bash
   git add <file1> <file2> ...
   ```
   Never use `git add -A` or `git add .`.

2. **Show the staged diff** (`git diff --staged`) and the drafted message. Ask: **"Commit with this message? (y / n / edit)"**
   - `y` → commit
   - `n` → unstage with `git reset HEAD` and skip this commit (continue to next)
   - `edit` → accept the user's revised message

3. **Commit:**
   ```bash
   git commit -m "$(cat <<'EOF'
   <message here>
   EOF
   )"
   ```

4. **Confirm** with `git log --oneline -1`.

After all commits are done, show a summary:
```
Done. 3 commits created:
  abc1234  chore: add gspread and google-auth dependencies
  def5678  feat(agent_loop): add generic tool-dispatch loop
  ghi9012  feat(tracker): add NL-to-JSON extraction and JSONL fallback
```

---

## Commit message rules

```
<type>(<scope>): <subject>

<optional body>
```

**Types:** `feat` · `fix` · `refactor` · `test` · `docs` · `chore` · `perf` · `ci`

**Rules:**
- Subject: imperative mood, ≤72 chars, no trailing period
- Scope: the module or area (e.g. `tracker`, `interviewer`, `telemetry`, `cli`) — omit if broadly cross-cutting
- Body: only when the *why* is non-obvious from the diff; explain motivation, not mechanics
- **Never** add a `Co-Authored-By` line

---

## Amend

Use this path only when invoked as `/commit --amend`.

1. Show the current HEAD: `git log --oneline -1`

2. Check if it has already been pushed:
   ```bash
   git log origin/$(git branch --show-current)..HEAD --oneline
   ```
   If empty, the commit is on the remote. Warn loudly — amending will require a force-push and rewrites shared history. Ask for explicit confirmation.

3. If there are new staged or unstaged changes, offer to fold them into the amended commit.

4. Run Step 2 (sensitive file check) on any newly included files.

5. Draft a revised message using the commit message rules above, pre-filled with the current message for reference.

6. Present the message with the `y / n / edit` prompt.

7. Commit:
   ```bash
   git commit --amend -m "$(cat <<'EOF'
   <message here>
   EOF
   )"
   ```

8. Confirm with `git log --oneline -1`.
