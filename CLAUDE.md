@AGENTS.md

# Claude Code notes
- Task recipes live in `.claude/skills/` (verify eBay assumptions, tune the classical analyzer, add a search/watch card, debug missed/noisy listings). Use them instead of improvising those workflows.
- Before claiming something about eBay's API behavior, check `docs/VERIFY.md`; if it is unverified, say so.
- After code changes run `python -m pytest -q tests`. After config-shape changes also run `python -m pokescan.check --dry-run`.
