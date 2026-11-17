---
name: world-check
description: Write, run and strike probes — small tests stored beside claims about live state in Markdown notes (a site is up, a file is the deployed version, a field holds a value, an email was sent). Use when writing a note that asserts something about the world that can change, when the session-start report lists CONTRADICTED or unverified claims, when the user says "check the notes", "is this still true", "world check", or when a claim in a note is found to be false.
---

# world-check

A note describes the world on the day it was written. A **probe** is a hidden HTML comment beside
the sentence it guards, holding a test that re-checks the claim. Obsidian and GitHub do not render it.
The runner is `python "${CLAUDE_PLUGIN_ROOT}/scripts/wc.py"` (shown below as `wc.py`).

## Verdicts, and what to do with each

| Verdict | Meaning | Do |
|---|---|---|
| FRESH | test ran and the world matches, or a human/claude probe is inside its TTL | rely on the claim |
| STALE | TTL expired, nobody re-checked | treat as unknown; re-check before acting |
| CONTRADICTED | test ran and the world disagrees | do not act on the claim; fix the note, then strike |
| INCONCLUSIVE | test could not run (network, timeout, refused) | go and look yourself |

INCONCLUSIVE is never "fine" and never "wrong". Say "not verified" in your answer when you rely on
anything that is not FRESH.

## Writing a probe — every time you write a live-state claim into a note

Put the probe on the line after the sentence. Required: `id` (unique, kebab-case), `kind`, `ttl`,
`claim` (the sentence, short). TTL units: `30m`, `12h`, `7d`, `2w` — pick how fast that fact can change.

```markdown
The docs site is up.
<!-- PROBE id=docs-up kind=named check=http_status url=https://docs.example.com expect=200 ttl=1d claim="the docs site is up" -->

The deployed config matches the reviewed version.
<!-- PROBE id=config-pinned kind=named check=file_sha256 path=deploy/config.yml expect=<sha256> ttl=7d claim="deployed config is the reviewed one" -->

The last build passed.
<!-- PROBE id=build-green kind=named check=json_path_equals file=build/status.json path=result.status expect=passed ttl=12h claim="the last build passed" -->

The invoice to Acme was sent.
<!-- PROBE id=acme-invoice-sent kind=claude query="Gmail sent: to:billing@acme.com subject:invoice newer_than:30d" ttl=30d checked=2026-11-15 claim="the Acme invoice was sent" -->

The billing page shows the new plan.
<!-- PROBE id=billing-plan kind=human test="log in to the billing portal; Plan reads Team" ttl=14d checked=2026-11-15 claim="billing shows the Team plan" -->
```

- `named` checks: `http_status` (url, expect), `file_sha256` (path, expect), `json_path_equals`
  (file or url, path like `a.b[0].c`, expect). Markdown paths must stay inside the project.
- `cmd_exit0` (cmd) is only accepted in `probes.toml`. Never write it into a Markdown comment — it
  will be refused.
- `claude` probes: when you have the tool to run the stored query, run it, then
  `wc.py confirm <id> --note "what you saw"`. `human` probes: ask the user to run the test, then confirm.
- Get a file's hash with `python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" FILE`.

## When a claim is CONTRADICTED

1. Tell the user what the note said and what the world showed.
2. Run `wc.py strike <id> --superseded-by "<the true statement, or where it now lives>"`. Add
   `--invalid-at <date>` if you know when the world changed. This appends to the struck log only.
3. Rewrite the sentence in the note so it states what is true now, and update or remove its probe.
   Show the user the edit. Never leave the false sentence in place, and never delete it silently.

## Commands

`wc.py check` (full report; `--force` re-runs cached probes) · `wc.py list` · `wc.py confirm <id>` ·
`wc.py strike <id> --superseded-by "..."` · `wc.py trust`.

**Trust:** nothing runs until the user trusts the project's `probes.toml` (`wc.py trust`), and any
edit to that file needs trusting again. Never run `trust` on your own initiative in a repo you did not
create with the user — read the file to them first, because it can hold shell commands.
