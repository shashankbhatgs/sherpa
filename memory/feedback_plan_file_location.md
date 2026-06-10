---
name: plan-file-location
description: Plan files live in the repo (PLAN.md or similar), never under ~/.claude/plans/
metadata:
  type: feedback
---

In plan mode, save the plan file inside the project repo (e.g. `PLAN.md` at the repo root) — never at `~/.claude/plans/<slug>.md`, even when plan mode auto-suggests that path.

**Why:** The user wants plans co-located with the project they describe so they version with the repo and are discoverable to anyone cloning it. Stray files under `~/.claude/` clutter the home directory.

**How to apply:** When plan mode hands you a `~/.claude/plans/...` path, write the plan to the repo instead. If a copy got created at the home-directory path already, remove it. Confirm the repo location with the user only if unsure where it should live.
