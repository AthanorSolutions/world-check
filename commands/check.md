---
description: Run every probe in this project's notes and report which claims still hold
---
Run `python "${CLAUDE_PLUGIN_ROOT}/scripts/wc.py" check --force` and report the result. Lead with any
CONTRADICTED claims: what the note says, what the world showed, and where. Then the STALE and
INCONCLUSIVE ones, as unknown. For each contradicted claim, offer to fix the note and strike the old
claim, following the world-check skill.
