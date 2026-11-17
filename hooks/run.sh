#!/usr/bin/env bash
# Find a Python 3.11+ and run wc.py. Never fails the session: exits 0 whatever happens.
here="$(cd "$(dirname "$0")" && pwd)"
for py in python3 python py; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    "$py" "$here/../scripts/wc.py" "$@"
    exit 0
  fi
done
[ "$1" = "session-start" ] || echo "world-check: needs Python 3.11 or newer on PATH" >&2
exit 0
