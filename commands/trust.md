---
description: Review and trust this project's probes.toml so world-check can run it
---
Read the project's `probes.toml` (or `.claude/probes.toml`) and show the user every `cmd_exit0`
command and every URL it will touch at session start. Ask for a yes. Only after the user says yes, run
`python "${CLAUDE_PLUGIN_ROOT}/scripts/wc.py" trust` and report its output.
