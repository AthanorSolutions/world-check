"""Run: python -m unittest discover -s tests"""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WC = os.path.join(HERE, "..", "scripts", "wc.py")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import wc  # noqa: E402


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ok":
            body, code = b'{"build": {"status": "passed"}}', 200
        elif self.path == "/slow":
            import time
            time.sleep(3)
            body, code = b"", 200
        else:
            body, code = b"", 404
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class WorldCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.data = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, CLAUDE_PROJECT_DIR=self.root, WORLD_CHECK_HOME=self.data.name)
        os.environ.update(CLAUDE_PROJECT_DIR=self.root, WORLD_CHECK_HOME=self.data.name)

    def tearDown(self):
        self.tmp.cleanup()
        self.data.cleanup()

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def run_wc(self, *args):
        r = subprocess.run([sys.executable, WC, *args], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def verdicts(self, budget=5.0):
        cfg = wc.load_config(wc.find_config(self.root))
        res = wc.evaluate(wc.find_probes(self.root, cfg), self.root, {}, budget)
        return {p["id"]: (v, d) for p, v, d, _ in res}

    # ── the four checks ──
    def test_http_status(self):
        self.write("probes.toml", "")
        self.write("n.md", f"""
<!-- PROBE id=up kind=named check=http_status url={self.base}/ok expect=200 ttl=1d -->
<!-- PROBE id=gone kind=named check=http_status url={self.base}/missing expect=200 ttl=1d -->
<!-- PROBE id=down kind=named check=http_status url=http://127.0.0.1:1/ expect=200 ttl=1d -->
""")
        v = self.verdicts()
        self.assertEqual(v["up"][0], "FRESH")
        self.assertEqual(v["gone"][0], "CONTRADICTED")
        self.assertEqual(v["down"][0], "INCONCLUSIVE")  # unreachable is never "wrong"

    def test_file_sha256(self):
        self.write("probes.toml", "")
        self.write("a.txt", "hello")
        h = hashlib.sha256(b"hello").hexdigest()
        self.write("n.md", f"""
<!-- PROBE id=same kind=named check=file_sha256 path=a.txt expect={h} ttl=1d -->
<!-- PROBE id=changed kind=named check=file_sha256 path=a.txt expect={'0' * 64} ttl=1d -->
<!-- PROBE id=missing kind=named check=file_sha256 path=nope.txt expect={h} ttl=1d -->
<!-- PROBE id=escape kind=named check=file_sha256 path=../../etc/passwd expect={h} ttl=1d -->
""")
        v = self.verdicts()
        self.assertEqual(v["same"][0], "FRESH")
        self.assertEqual(v["changed"][0], "CONTRADICTED")
        self.assertEqual(v["missing"][0], "CONTRADICTED")  # the note said it was there
        self.assertEqual(v["escape"][0], "INCONCLUSIVE")
        self.assertIn("refused", v["escape"][1])

    def test_json_path_equals(self):
        self.write("probes.toml", "")
        self.write("s.json", json.dumps({"a": {"list": [{"v": 3}]}, "ok": True}))
        self.write("n.md", f"""
<!-- PROBE id=deep kind=named check=json_path_equals file=s.json path=a.list[0].v expect=3 ttl=1d -->
<!-- PROBE id=bool kind=named check=json_path_equals file=s.json path=ok expect=true ttl=1d -->
<!-- PROBE id=wrong kind=named check=json_path_equals file=s.json path=a.list[0].v expect=4 ttl=1d -->
<!-- PROBE id=nokey kind=named check=json_path_equals file=s.json path=a.zzz expect=1 ttl=1d -->
<!-- PROBE id=url kind=named check=json_path_equals url={self.base}/ok path=build.status expect=passed ttl=1d -->
""")
        v = self.verdicts()
        self.assertEqual(v["deep"][0], "FRESH")
        self.assertEqual(v["bool"][0], "FRESH")
        self.assertEqual(v["wrong"][0], "CONTRADICTED")
        self.assertEqual(v["nokey"][0], "CONTRADICTED")
        self.assertEqual(v["url"][0], "FRESH")

    def test_cmd_exit0_only_from_toml(self):
        self.write("probes.toml", f"""
[[probe]]
id = "good"
check = "cmd_exit0"
cmd = '"{sys.executable}" -c "pass"'
ttl = "1d"
[[probe]]
id = "bad"
check = "cmd_exit0"
cmd = '"{sys.executable}" -c "import sys; sys.exit(3)"'
ttl = "1d"
""")
        self.write("n.md", '<!-- PROBE id=sneaky kind=named check=cmd_exit0 cmd="echo pwned" ttl=1d -->')
        v = self.verdicts()
        self.assertEqual(v["good"][0], "FRESH")
        self.assertEqual(v["bad"][0], "CONTRADICTED")
        self.assertEqual(v["sneaky"][0], "INCONCLUSIVE")
        self.assertIn("refused", v["sneaky"][1])

    # ── TTL, human/claude, cache, budget ──
    def test_human_and_claude_ttl(self):
        self.write("probes.toml", "")
        self.write("n.md", """
<!-- PROBE id=recent kind=human test="look" ttl=3650d checked=2026-01-01 -->
<!-- PROBE id=old kind=human test="look" ttl=1d checked=2020-01-01 -->
<!-- PROBE id=never kind=claude query="q" ttl=1d -->
""")
        v = self.verdicts()
        self.assertEqual(v["recent"][0], "FRESH")
        self.assertEqual(v["old"][0], "STALE")
        self.assertEqual(v["never"][0], "STALE")

    def test_cache_skips_fresh_and_edit_invalidates(self):
        self.write("probes.toml", "")
        self.write("n.md", f"<!-- PROBE id=up kind=named check=http_status url={self.base}/ok ttl=1d -->")
        cfg = wc.load_config(wc.find_config(self.root))
        cache = {}
        r1 = wc.evaluate(wc.find_probes(self.root, cfg), self.root, cache, 5)
        self.assertTrue(r1[0][3])  # ran
        r2 = wc.evaluate(wc.find_probes(self.root, cfg), self.root, cache, 5)
        self.assertFalse(r2[0][3])  # cached
        self.write("n.md", f"<!-- PROBE id=up kind=named check=http_status url={self.base}/ok expect=200 ttl=1d -->")
        r3 = wc.evaluate(wc.find_probes(self.root, cfg), self.root, cache, 5)
        self.assertTrue(r3[0][3])  # probe edited -> old result no longer applies

    def test_budget(self):
        self.write("probes.toml", "")
        self.write("n.md", f"<!-- PROBE id=slow kind=named check=http_status url={self.base}/slow ttl=1d -->")
        v = self.verdicts(budget=0.5)
        self.assertEqual(v["slow"][0], "INCONCLUSIVE")

    def test_bad_ttl_and_prose_marker(self):
        self.write("probes.toml", "")
        self.write("n.md", "A claim carries a <!-- PROBE --> next to it.\n"
                           "<!-- PROBE id=x kind=human ttl=forever -->")
        v = self.verdicts()
        self.assertEqual(list(v), ["x"])
        self.assertEqual(v["x"][0], "INCONCLUSIVE")

    # ── trust gate and session start ──
    def test_trust_gate(self):
        self.write("probes.toml", "")
        self.write("n.md", f"<!-- PROBE id=gone kind=named check=http_status url={self.base}/missing ttl=1d -->")
        out = self.run_wc("session-start")
        self.assertIn("not trusted", out)
        self.assertNotIn("CONTRADICTED", out)
        self.run_wc("trust")
        out = self.run_wc("session-start")
        self.assertIn("CONTRADICTED", out)
        self.assertIn("gone", out)
        self.write("probes.toml", "# edited\n")
        self.assertIn("not trusted", self.run_wc("session-start"))

    def test_session_silent_when_clean_and_without_config(self):
        self.assertEqual(self.run_wc("session-start"), "")  # no probes.toml: opt-in, silent
        self.write("probes.toml", "")
        self.write("n.md", f"<!-- PROBE id=up kind=named check=http_status url={self.base}/ok ttl=1d -->")
        self.run_wc("trust")
        self.assertEqual(self.run_wc("session-start"), "")

    def test_session_survives_broken_config(self):
        self.write("probes.toml", "this is [not toml")
        self.run_wc("session-start")  # must exit 0 (asserted in run_wc)

    # ── confirm and strike ──
    def test_confirm_then_strike(self):
        self.write("probes.toml", "")
        self.write("n.md", '<!-- PROBE id=mail kind=claude query="q" ttl=7d claim="the email went out" -->')
        self.run_wc("trust")
        self.run_wc("confirm", "mail", "--note", "seen in sent")
        out = self.run_wc("check")
        self.assertIn("FRESH", out)
        out = self.run_wc("strike", "mail", "--superseded-by", "it bounced; see Mail.md")
        self.assertIn("struck mail", out)
        log = open(os.path.join(self.root, "STRUCK.md"), encoding="utf-8").read()
        for field in ("valid_at", "invalid_at", "expired_at", "superseded_by", "the email went out", "it bounced"):
            self.assertIn(field, log)
        # the struck log quotes the claim but is never scanned for probes
        self.assertEqual(self.run_wc("list").count("mail"), 1)


if __name__ == "__main__":
    unittest.main()
