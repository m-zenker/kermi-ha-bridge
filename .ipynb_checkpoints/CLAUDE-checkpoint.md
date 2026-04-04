# Claude Code — Project Instructions

## Communication
- Narrate what you're about to do before each tool call.
- Keep text responses short and direct. Lead with the action or answer.

## Commits
- Concise, single-line subject. No bullet-point bodies unless truly necessary.
- Co-author line required.
- Never push unless explicitly instructed. If a push seems warranted, ask first.

## Workflow
- **Always plan before implementing.** Present the plan, wait for approval, then implement.
- **Plan format**: list changes file-by-file with explicit rationale. No vague prose.
- **Always work on a feature branch.** Never commit directly to `main`.
- **`main` branch**: tracks current public release state.
- After implementing, update CHANGELOG.md, README.md (if user-facing behaviour changed), and MEMORY.md.

## Upstream
This repo is a mirror of the `kermi_bridge` app from `ha-energy-manager`. Changes originate there and are synced here on every `ha-energy-manager` main merge. Direct changes in this repo should be coordinated to avoid drift.

## Proactivity
- Spotted a small, minimal-risk bug unrelated to the task? Fix it silently.
- Spotted a larger issue or improvement opportunity? Flag it briefly to the user. Do not implement it unless asked.

## Memory
- Canonical memory file: `MEMORY.md` at the repo root (gitignored).
- Update it after completing any meaningful unit of work.
- Auto-memory stub at `~/.claude/projects/.../memory/MEMORY.md` is a pointer only — keep content in the repo-root file.
