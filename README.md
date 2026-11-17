# world-check

**Notes that carry their own falsifier.** A Claude Code plugin that attaches a small test to every
claim about live state in your Markdown notes, re-runs the tests when a session opens, and tells Claude
which claims no longer hold before it acts on them.

```markdown
The docs site is up.
<!-- PROBE id=docs-up kind=named check=http_status url=https://docs.example.com expect=200 ttl=1d claim="the docs site is up" -->
```

Obsidian and GitHub don't render the comment, so the note reads normally. If someone deletes the
sentence, the probe goes with it, which means the list of tests can't drift away from the notes it protects.

## Why

A note that says "the invoice is unsent" reads exactly the same after the invoice goes out. A fresh
session believes it and tells you to send it again. Writing "always verify" into a rules file doesn't
fix this: agents seldom re-check a fact they inherited that already looks settled. So the check has to
run at a moment the harness controls, whether or not the model remembers to.

Memory tools that verify against a **code repository** already exist (they check a cited file or
re-run a test). world-check is for everything that isn't code: sites, servers, config values,
emails, things only a logged-in person can see. And it lives **inside the note itself**, next to the
sentence it guards.

## What a session sees

When every claim holds, nothing is printed. When one doesn't:

```
world-check: 1 claim(s) in your notes are CONTRADICTED by the world. Do not act on them; fix the note
first and strike the old claim (... wc.py strike ID --superseded-by "...").
- release-stable: the note says "1.4.0 is the stable release" — the world shows: channels.stable = 1.3.2 (expected 1.4.0) [Status.md:10]
world-check: 3 claim(s) unverified (stale or could not be checked): homepage-up, launch-email, status-green.
Treat them as unknown, not true.
```

## Four verdicts

| Verdict | Meaning |
|---|---|
| **FRESH** | The test ran and the world matches, or a human/claude probe is still within its TTL. |
| **STALE** | The TTL expired and nobody has re-checked. The claim may be true; nobody knows. |
| **CONTRADICTED** | The test ran and the world disagrees. The note is wrong; fix it before acting on it. |
| **INCONCLUSIVE** | The test couldn't run (network down, timeout, refused). Go and look yourself. |

"I couldn't reach it" never counts as "it's fine", and it never counts as "it's wrong" either. A site
that times out because your Wi-Fi dropped hasn't gone down. A file the note says exists, and doesn't,
is a real contradiction.

## Three kinds of probe

Some things can't be checked by a script, and pretending otherwise is how notes start lying.

- **`named`**: the plugin runs it. It has four checks:
  - `http_status` takes `url` and `expect`. Note that redirects are followed.
  - `file_sha256` takes `path` and `expect`.
  - `json_path_equals` takes `file` or `url`, a `path` like `a.b[0].c`, and `expect`.
  - `cmd_exit0` takes `cmd`, and is only accepted in `probes.toml`.
- **`claude`**: only Claude can check it, for example through a connected Gmail. The probe stores the
  exact query to run. Once Claude has run it, it records the result with `wc.py confirm <id>`.
- **`human`**: only a logged-in person can check it. The probe stores the exact test, the person
  runs it, and the result is recorded with `wc.py confirm <id>`.

Every probe has a **TTL** (`30m`, `12h`, `7d`, `2w`): how long a confirmation stays trusted. After
that the claim reads as STALE, the way a blood test stops counting once the results are too old.

## The struck log

When a claim is CONTRADICTED, Claude rewrites the note to say what's true, and the old sentence moves
to a struck log so the same mistake can't be written back next month. Each entry carries two clocks:

```
wc.py strike release-stable --superseded-by "1.3.2 is stable; 1.4.0 is beta" --invalid-at 2026-11-02
```

- `valid_at` is when the claim was last confirmed true.
- `invalid_at` is when the world changed, which is often unknown.
- `expired_at` is when the probe caught it.
- `superseded_by` is the claim that replaced it.

Each entry also records the probe output that proved the claim wrong. If the gap between `valid_at`
and `expired_at` keeps running to weeks, the TTL is too long. `strike` only appends to the log, and
the note itself is rewritten in the open, where you can see the change.

## Install

Requires Python 3.11 or newer and nothing else (standard library only).

```
/plugin marketplace add <owner>/world-check
/plugin install world-check@world-check
```

world-check is **opt-in per project**. It does nothing until the project has a `probes.toml`:

1. Copy [`examples/probes.toml`](examples/probes.toml) to your project root (or `.claude/probes.toml`) and set `paths`.
2. Run `/world-check:trust`. Claude shows you every command and URL the file will touch, then records it as trusted.
3. Write probes next to your claims. The bundled skill teaches Claude to add one whenever it writes a live-state claim into a note.
4. Paste [`CLAUDE-snippet.md`](CLAUDE-snippet.md) into your CLAUDE.md.

To try it without your own notes, the [`demo/`](demo) project contains one contradicted claim, one
stale claim of each human/claude kind, and two that hold.

## Safety

- **Nothing runs until you trust `probes.toml`**, and any edit to that file means trusting it again.
  This stops a cloned repo from running commands on your machine the first time you open it.
- **`cmd_exit0` only runs from the trusted `probes.toml`**, never from a comment in a Markdown file.
- **Markdown probes can't read files outside the project.**
- **It never edits your notes.** Results are cached in `~/.world-check/`, or in `WORLD_CHECK_HOME` if you set it.
- **It can't break a session.** Session start has a hard time budget (8 seconds by default), always
  exits cleanly, and a crash shows up as INCONCLUSIVE.

## Commands

- `wc.py check [--force]`: the full report. `--force` re-runs probes whose cached results are still fresh.
- `wc.py list`: list the probes found, without running them.
- `wc.py confirm <id> [--note "..."]`: record that a human or claude probe was checked just now.
- `wc.py strike <id> --superseded-by "..." [--invalid-at DATE]`: append a dead claim to the struck log.
- `wc.py trust`: trust this project's `probes.toml`.

## Troubleshooting

**HTTPS probes come back INCONCLUSIVE with `CERTIFICATE_VERIFY_FAILED`.** Your Python can't find the
certificate authority that signed the site's certificate, which is common with some Windows and
macOS Python builds. On macOS, run `Install Certificates.command` from your Python folder. On Windows,
open the site once in Edge so Windows fetches the missing root certificate. world-check reports these
as INCONCLUSIVE, not CONTRADICTED, because the site was never reached.

## Tests

```
python -m unittest discover -s tests
```

## License

MIT
