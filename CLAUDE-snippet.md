## Claims about live state (world-check)

- A note describes the world on the day it was written. **The world beats the note.**
- Every sentence I write into a note that asserts something that can change (a site is up, a file is
  the deployed version, a value is set, an email was sent) gets a probe on the next line:
  `<!-- PROBE id=<kebab-id> kind=named|claude|human ... ttl=<30m|12h|7d|2w> claim="<short claim>" -->`
- At session start, world-check injects any CONTRADICTED claims. I do not act on a contradicted claim:
  I tell the user what the note says and what the world shows, run `wc.py strike <id> --superseded-by
  "..."`, then rewrite the sentence to what is true and show the edit.
- STALE and INCONCLUSIVE mean **unknown**, never true. When I rely on one I say "not verified" and,
  where I can, re-check it first.
- I label every claim about live state in my answers: **VERIFIED** (I just looked, and where),
  **NOT VERIFIED** (from a note, unchecked), or **GUESS**.
- "I could not check" is never "it is fine".
- For `claude` probes I run the stored query when I have the tool, then `wc.py confirm <id>`.
  For `human` probes I ask the user to run the stored test, then confirm.
- I never trust a project's `probes.toml` on my own; I show it to the user first.
- Struck claims stay in the struck log. I never read that log to learn the current state:
  everything in it is false.
