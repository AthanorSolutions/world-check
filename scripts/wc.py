#!/usr/bin/env python3
"""world-check: notes that carry their own falsifier.

A note says what the world looked like on the day it was written. world-check lets every claim
about live state carry a small test (a "probe") right next to the sentence it guards, re-runs the
tests, and tells Claude which claims still hold before it acts on any of them.

    The marketing site is up.
    <!-- PROBE id=site-up kind=named check=http_status url=https://example.com expect=200 ttl=1d -->

Commands
    check           run every probe, print a full report
    session-start   what the SessionStart hook runs: silent when clean, short when not
    confirm ID      record that a human/claude probe was checked just now
    strike ID       append a dead claim to the struck log (never edits the note)
    trust           trust this project's probes.toml (required before anything runs)
    list            list the probes found, without running them

Rules it lives by
    1. It never edits a note. Results go to a cache outside the notes.
    2. "I could not check" is INCONCLUSIVE, never FRESH and never CONTRADICTED.
    3. Session start can never break: hard time budget, always exits 0.
    4. Nothing runs in a project until the user trusts its probes.toml, and cmd_exit0
       only ever runs from that trusted file, never from a Markdown comment.

Python 3.11+, standard library only.
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

VERSION = "0.1.0"
SELF = f'"{sys.executable}" "{os.path.abspath(__file__)}"'  # python3 on macOS, python on Windows
FRESH, STALE, CONTRA, INC = "FRESH", "STALE", "CONTRADICTED", "INCONCLUSIVE"
ORDER = {CONTRA: 0, INC: 1, STALE: 2, FRESH: 3}

PROBE_RE = re.compile(r"<!--\s*PROBE\s+(.*?)-->", re.S)
FIELD_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
CONFIG_NAMES = ("probes.toml", os.path.join(".claude", "probes.toml"))
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".obsidian"}
DEFAULT_TTL = "1d"
DEFAULT_BUDGET = 8.0
HTTP_TIMEOUT = 5.0


# ── time ────────────────────────────────────────────────────────────────────
def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def parse_dt(s):
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_ttl(s):
    m = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", str(s or ""))
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    unit_name = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[unit]
    return timedelta(**{unit_name: n})


# ── config and trust ────────────────────────────────────────────────────────
def project_root():
    return os.path.abspath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def data_dir():
    # One fixed home, not ${CLAUDE_PLUGIN_DATA}: the hook sees that variable but a `wc.py trust`
    # typed in a terminal does not, and trust written to one folder and read from another never runs.
    d = os.environ.get("WORLD_CHECK_HOME") or os.path.join(os.path.expanduser("~"), ".world-check")
    os.makedirs(d, exist_ok=True)
    return d


def find_config(root):
    for name in CONFIG_NAMES:
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return p
    return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def trust_path():
    return os.path.join(data_dir(), "trusted.json")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def is_trusted(cfg_path):
    trusted = load_json(trust_path(), {})
    return trusted.get(os.path.abspath(cfg_path)) == sha256_file(cfg_path)


def load_config(cfg_path):
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    scan = cfg.get("scan", {})
    return {
        "paths": scan.get("paths", ["."]),
        "exclude": set(scan.get("exclude", [])),
        "budget": float(scan.get("budget_seconds", DEFAULT_BUDGET)),
        "struck_log": cfg.get("strike", {}).get("log", "STRUCK.md"),
        "probes": cfg.get("probe", []),
    }


# ── finding probes ──────────────────────────────────────────────────────────
def unquote(v):
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('\\"', '"')
    return v


def parse_markdown(path, root):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError:
        return []
    out = []
    for m in PROBE_RE.finditer(src):
        fields = {k: unquote(v) for k, v in FIELD_RE.findall(m.group(1))}
        if "id" not in fields:  # prose that merely names the marker
            continue
        fields["_src"] = os.path.relpath(path, root)
        fields["_line"] = src[: m.start()].count("\n") + 1
        fields["_from"] = "markdown"
        out.append(fields)
    return out


def find_probes(root, cfg):
    probes = []
    struck = os.path.abspath(os.path.join(root, cfg["struck_log"]))
    for rel in cfg["paths"]:
        base = os.path.abspath(os.path.join(root, rel))
        if not base.startswith(root):
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d not in cfg["exclude"]]
            for fn in files:
                p = os.path.join(dirpath, fn)
                if not fn.endswith(".md") or os.path.abspath(p) == struck:
                    continue  # the struck log quotes dead probes; never resurrect them
                if os.path.relpath(p, root) in cfg["exclude"]:
                    continue
                probes.extend(parse_markdown(p, root))
    for i, p in enumerate(cfg["probes"]):
        p = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in p.items()}
        p.setdefault("id", f"toml-{i}")
        p.setdefault("kind", "named")
        p["_src"], p["_line"], p["_from"] = os.path.relpath(find_config(root), root), 0, "toml"
        probes.append(p)
    return probes


def probe_key(p):
    """Cache key: id + the probe's own fields. Edit the probe and its old result no longer applies."""
    body = json.dumps({k: v for k, v in p.items() if not k.startswith("_") and k != "checked"}, sort_keys=True)
    return p["id"] + ":" + hashlib.sha256(body.encode()).hexdigest()[:12]


# ── the four checks ─────────────────────────────────────────────────────────
class Inconclusive(Exception):
    pass


def inside_root(path, root):
    full = os.path.abspath(os.path.join(root, path))
    return full if full == root or full.startswith(root + os.sep) else None


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"world-check/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read(2_000_000)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # DNS, refused, timeout, TLS: we could not look
        raise Inconclusive(f"could not reach {url}: {type(e).__name__}: {e}")


def check_http_status(p, root):
    url = p.get("url") or ""
    if not url.startswith(("http://", "https://")):
        raise Inconclusive("http_status needs url=http(s)://...")
    expect = str(p.get("expect", "200"))
    code, _ = fetch(url)
    return str(code) == expect, f"HTTP {code} (expected {expect})"


def check_file_sha256(p, root):
    path = inside_root(p.get("path", ""), root) if p["_from"] == "markdown" else \
        os.path.abspath(os.path.join(root, os.path.expanduser(p.get("path", ""))))
    if not path:
        raise Inconclusive("refused: a Markdown probe may not read outside the project")
    if not os.path.isfile(path):
        return False, f"file not found: {p.get('path')}"
    got = sha256_file(path)
    want = (p.get("expect") or "").lower()
    return got == want, f"sha256 {got[:16]}… (expected {want[:16]}…)"


def walk_json(obj, path):
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            idx = int(part[1:-1])
            if not isinstance(obj, list) or idx >= len(obj):
                raise KeyError(part)
            obj = obj[idx]
        else:
            if not isinstance(obj, dict) or part not in obj:
                raise KeyError(part)
            obj = obj[part]
    return obj


def check_json_path_equals(p, root):
    src = p.get("url") or p.get("file")
    if not src:
        raise Inconclusive("json_path_equals needs url= or file=")
    if p.get("url"):
        code, body = fetch(p["url"])
        if code >= 400:
            raise Inconclusive(f"HTTP {code} fetching {p['url']}")
    else:
        path = inside_root(p["file"], root) if p["_from"] == "markdown" else \
            os.path.abspath(os.path.join(root, os.path.expanduser(p["file"])))
        if not path:
            raise Inconclusive("refused: a Markdown probe may not read outside the project")
        if not os.path.isfile(path):
            return False, f"file not found: {p['file']}"
        with open(path, "rb") as f:
            body = f.read()
    try:
        doc = json.loads(body)
    except ValueError:
        raise Inconclusive(f"{src} is not JSON")
    try:
        got = walk_json(doc, p.get("path", ""))
    except KeyError:
        return False, f"{p.get('path')} is missing in {src}"
    want = p.get("expect", "")
    got_s = got if isinstance(got, str) else json.dumps(got)
    return got_s == want, f"{p.get('path')} = {got_s[:80]} (expected {want[:80]})"


def check_cmd_exit0(p, root):
    if p["_from"] != "toml":
        raise Inconclusive("refused: cmd_exit0 only runs from probes.toml, never from a Markdown comment")
    cmd = p.get("cmd")
    if not cmd:
        raise Inconclusive("cmd_exit0 needs cmd=")
    try:
        r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True,
                           timeout=float(p.get("timeout", 10)))
    except subprocess.TimeoutExpired:
        raise Inconclusive(f"command timed out: {cmd}")
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    return r.returncode == 0, f"exit {r.returncode}" + (f": {tail[0][:120]}" if tail[0] else "")


CHECKS = {
    "http_status": check_http_status,
    "file_sha256": check_file_sha256,
    "json_path_equals": check_json_path_equals,
    "cmd_exit0": check_cmd_exit0,
}


# ── verdicts ────────────────────────────────────────────────────────────────
def run_named(p, root):
    fn = CHECKS.get(p.get("check", ""))
    if not fn:
        return INC, f"unknown check '{p.get('check')}'"
    try:
        ok, detail = fn(p, root)
        return (FRESH if ok else CONTRA), detail
    except Inconclusive as e:
        return INC, str(e)
    except Exception as e:  # a crash in a check is a check that did not run
        return INC, f"check crashed: {type(e).__name__}: {e}"


def last_confirmed(p, entry):
    """For human/claude probes: the later of `checked=` in the note and a `confirm` in the cache."""
    times = [parse_dt(p.get("checked")), parse_dt((entry or {}).get("confirmed_at"))]
    times = [t for t in times if t]
    return max(times) if times else None


def evaluate(probes, root, cache, budget, force=False):
    """Returns [(probe, verdict, detail, ran_now)]. Updates `cache` in place."""
    t = now()
    results, to_run = [], []
    for p in probes:
        key, entry = probe_key(p), cache.get(probe_key(p), {})
        ttl = parse_ttl(p.get("ttl", DEFAULT_TTL))
        if ttl is None:
            results.append((p, INC, f"bad ttl '{p.get('ttl')}' (use 30m, 12h, 7d, 2w)", False))
            continue
        kind = p.get("kind", "named")
        if kind in ("human", "claude"):
            when = last_confirmed(p, entry)
            how = p.get("test") or p.get("query") or "see the probe"
            if when and t - when <= ttl:
                results.append((p, FRESH, f"confirmed {iso(when)}", False))
            else:
                age = f"last confirmed {iso(when)}" if when else "never confirmed"
                results.append((p, STALE, f"{age}; {kind} check: {how}", False))
            continue
        if kind != "named":
            results.append((p, INC, f"unknown kind '{kind}'", False))
            continue
        last_ok = parse_dt(entry.get("fresh_at"))
        if not force and entry.get("verdict") == FRESH and last_ok and t - last_ok <= ttl:
            results.append((p, FRESH, entry.get("detail", "") + " (cached)", False))
            continue
        to_run.append(p)

    if to_run:
        deadline = time.monotonic() + budget
        pool = cf.ThreadPoolExecutor(max_workers=min(16, len(to_run)))
        futs = {pool.submit(run_named, p, root): p for p in to_run}
        done, pending = cf.wait(futs, timeout=max(0.1, deadline - time.monotonic()))
        for f in done:
            p = futs[f]
            verdict, detail = f.result()
            results.append((p, verdict, detail, True))
        for f in pending:
            results.append((futs[f], INC, f"did not finish inside the {budget:g}s budget", True))
        pool.shutdown(wait=False, cancel_futures=True)

    for p, verdict, detail, ran in results:
        if not ran:
            continue
        e = cache.setdefault(probe_key(p), {})
        e.update({"id": p["id"], "verdict": verdict, "detail": detail, "checked_at": iso(t),
                  "claim": p.get("claim", "")})
        if verdict == FRESH:
            e["fresh_at"] = iso(t)
    results.sort(key=lambda r: (ORDER[r[1]], r[0]["id"]))
    return results


# ── output ──────────────────────────────────────────────────────────────────
def where(p):
    return f"{p['_src']}:{p['_line']}" if p["_line"] else p["_src"]


def full_report(results):
    if not results:
        return "world-check: no probes found."
    counts = {v: sum(1 for r in results if r[1] == v) for v in ORDER}
    lines = [f"world-check: {len(results)} probes — " +
             ", ".join(f"{counts[v]} {v}" for v in ORDER if counts[v])]
    for p, v, detail, _ in results:
        claim = f' "{p["claim"]}"' if p.get("claim") else ""
        lines.append(f"  {v:<12} {p['id']}{claim}\n               {detail}  [{where(p)}]")
    return "\n".join(lines)


def session_report(results):
    contra = [r for r in results if r[1] == CONTRA]
    unsure = [r for r in results if r[1] in (STALE, INC)]
    if not contra and not unsure:
        return ""
    out = []
    if contra:
        out.append(f"world-check: {len(contra)} claim(s) in your notes are CONTRADICTED by the world. "
                   "Do not act on them; fix the note first and strike the old claim "
                   f"({SELF} strike ID --superseded-by \"...\").")
        for p, _, detail, _ in contra:
            claim = p.get("claim") or "(no claim text)"
            out.append(f"- {p['id']}: the note says \"{claim}\" — the world shows: {detail} [{where(p)}]")
    if unsure:
        ids = ", ".join(r[0]["id"] for r in unsure[:8]) + (" …" if len(unsure) > 8 else "")
        out.append(f"world-check: {len(unsure)} claim(s) unverified (stale or could not be checked): {ids}. "
                   f"Treat them as unknown, not true. Details: {SELF} check")
    return "\n".join(out)


# ── commands ────────────────────────────────────────────────────────────────
def setup(require_trust=True):
    root = project_root()
    cfg_path = find_config(root)
    if not cfg_path:
        return root, None, None, "no probes.toml in this project (world-check is opt-in per project)"
    if require_trust and not is_trusted(cfg_path):
        return root, None, cfg_path, (f"{os.path.relpath(cfg_path, root)} is not trusted yet (new or edited). "
                                      f"Read it, then run: {SELF} trust")
    return root, load_config(cfg_path), cfg_path, None


def cache_path():
    return os.path.join(data_dir(), "cache.json")


def cmd_check(args, session=False):
    root, cfg, cfg_path, err = setup()
    if err:
        if session:
            return "" if not cfg_path else f"world-check: {err}"
        return f"world-check: {err}"
    cache = load_json(cache_path(), {})
    results = evaluate(find_probes(root, cfg), root, cache, cfg["budget"], force=getattr(args, "force", False))
    try:
        save_json(cache_path(), cache)
    except OSError:
        pass
    return session_report(results) if session else full_report(results)


def find_one(probe_id):
    root, cfg, _, err = setup(require_trust=False)
    if err and not cfg:
        cfg_path = find_config(root)
        if not cfg_path:
            sys.exit(f"world-check: {err}")
        cfg = load_config(cfg_path)
    for p in find_probes(root, cfg):
        if p["id"] == probe_id:
            return root, cfg, p
    sys.exit(f"world-check: no probe with id={probe_id}")


def cmd_confirm(args):
    root, cfg, p = find_one(args.id)
    if p.get("kind", "named") == "named":
        return f"world-check: {args.id} is a named probe; it confirms itself. Run: python wc.py check --force"
    cache = load_json(cache_path(), {})
    e = cache.setdefault(probe_key(p), {})
    e.update({"id": p["id"], "confirmed_at": iso(now()), "confirmed_note": args.note or ""})
    save_json(cache_path(), cache)
    return f"world-check: {args.id} confirmed at {e['confirmed_at']}; trusted for ttl={p.get('ttl', DEFAULT_TTL)}."


def cmd_strike(args):
    root, cfg, p = find_one(args.id)
    entry = load_json(cache_path(), {}).get(probe_key(p), {})
    log = os.path.join(root, cfg["struck_log"])
    valid_at = entry.get("fresh_at") or entry.get("confirmed_at") or p.get("checked") or "unknown"
    lines = [
        f"\n## {iso(now())[:10]} — {p['id']}",
        f"- **Dead claim:** \"{p.get('claim') or '(no claim text on the probe)'}\"",
        f"- **Where:** `{where(p)}`",
        f"- **valid_at** (last confirmed true): {valid_at}",
        f"- **invalid_at** (when the world changed): {args.invalid_at or 'unknown'}",
        f"- **expired_at** (when we caught it): {iso(now())}",
        f"- **superseded_by:** {args.superseded_by}",
        f"- **Evidence:** {entry.get('verdict', 'no cached result')} — {entry.get('detail', 'run `wc.py check` first')}",
    ]
    new = not os.path.exists(log)
    with open(log, "a", encoding="utf-8") as f:
        if new:
            f.write("# Struck log\n\nClaims found false and removed from the notes. Nothing below is true.\n")
        f.write("\n".join(lines) + "\n")
    return (f"world-check: struck {p['id']} into {os.path.relpath(log, root)}. "
            "Now rewrite the note so it states only what is true, and remove or update its probe.")


def cmd_trust(args):
    root = project_root()
    cfg_path = find_config(root)
    if not cfg_path:
        return "world-check: no probes.toml here to trust."
    load_config(cfg_path)  # refuse to trust a file that does not parse
    trusted = load_json(trust_path(), {})
    trusted[os.path.abspath(cfg_path)] = sha256_file(cfg_path)
    save_json(trust_path(), trusted)
    cmds = [p.get("cmd") for p in load_config(cfg_path)["probes"] if p.get("check") == "cmd_exit0"]
    msg = f"world-check: trusted {os.path.relpath(cfg_path, root)}."
    if cmds:
        msg += " It runs these commands at session start:\n" + "\n".join(f"  {c}" for c in cmds)
    return msg


def cmd_list(args):
    root, cfg, _, err = setup(require_trust=False)
    if err and "no probes.toml" in err:
        return f"world-check: {err}"
    cfg = cfg or load_config(find_config(root))
    probes = find_probes(root, cfg)
    if not probes:
        return "world-check: no probes found."
    return "\n".join(f"{p['id']:<28} {p.get('kind', 'named'):<7} {p.get('check', ''):<17} "
                     f"ttl={p.get('ttl', DEFAULT_TTL):<4} {where(p)}" for p in probes)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wc.py", description="Notes that carry their own falsifier.")
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--force", action="store_true", help="re-run named probes even if cached fresh")
    sub.add_parser("session-start")
    c = sub.add_parser("confirm")
    c.add_argument("id")
    c.add_argument("--note", default="")
    c = sub.add_parser("strike")
    c.add_argument("id")
    c.add_argument("--superseded-by", required=True)
    c.add_argument("--invalid-at", default="")
    sub.add_parser("trust")
    sub.add_parser("list")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    except (AttributeError, ValueError):
        pass

    if args.cmd == "session-start":
        try:
            out = cmd_check(args, session=True)
        except Exception as e:  # session start must never break
            out = f"world-check: INCONCLUSIVE, the checker itself failed ({type(e).__name__}: {e})"
        if out:
            print(out)
        sys.stdout.flush()
        os._exit(0)  # do not wait on probe threads that blew the budget

    out = {"check": cmd_check, "confirm": cmd_confirm, "strike": cmd_strike,
           "trust": cmd_trust, "list": cmd_list}[args.cmd](args)
    print(out)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
