"""Runs approved/index.html in headless Chromium and checks the Calendly link it builds.

Calendly's widget.js is replaced by a stub that records the URL; every other
external request is aborted so no test data leaves the machine.
Run: scripts/run-tests
"""
import functools
import http.server
import json
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STUB = ("window.Calendly={initInlineWidget:function(o){(window.__cal=window.__cal||[]).push(o.url);"
        "(window.__calSearch=window.__calSearch||[]).push(location.search);}};")
UID = "0f8b6c1e-1234-4abc-9def-0123456789ab"


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class ApprovedAttribution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(Quiet, directory=str(ROOT))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()

    def booking(self, query, storage=None, posts=None):
        """Load /approved?query with localStorage preset; return the Calendly URL params.

        The legacy Apps Script attribution POST is answered locally; its JSON
        body is appended to `posts` when a list is given.
        """
        ctx = self.browser.new_context()
        try:
            page = ctx.new_page()

            def route(r):
                url = r.request.url
                if url.startswith(self.base):
                    return r.continue_()
                if "assets.calendly.com" in url and url.endswith("widget.js"):
                    return r.fulfill(status=200, content_type="text/javascript", body=STUB)
                if url.startswith("https://script.google.com/") and r.request.method == "POST":
                    if posts is not None:
                        posts.append(json.loads(r.request.post_data))
                    return r.fulfill(status=200, body="ok")
                return r.abort()

            page.route("**/*", route)
            page.add_init_script(
                "(function(s){for(var k in s)localStorage.setItem(k,s[k]);})(%s)" % json.dumps(storage or {})
            )
            page.goto(f"{self.base}/approved/?{query}")
            page.wait_for_function("window.__cal && window.__cal.length")
            qs = parse_qs(urlparse(page.evaluate("window.__cal[0]")).query)
            if posts is not None:
                # Wait (up to 5 s) for the attribution POST itself, not a fixed delay.
                for _ in range(50):
                    if posts:
                        break
                    page.wait_for_timeout(100)
            return {k: v[0] for k, v in qs.items()}
        finally:
            ctx.close()

    def tokens(self, cal):
        return cal.get("utm_content", "").split("|")

    def test_placeholder_values_are_empty(self):
        cal = self.booking("utm_source=google&utm_medium=_____&msclkid=_____&fbclid=&gclid=G1")
        self.assertEqual(cal["utm_source"], "google")
        self.assertNotIn("utm_medium", cal)
        toks = self.tokens(cal)
        self.assertIn("gclid_G1", toks)
        self.assertFalse(any(t.startswith(("msclkid_", "fbclid_")) for t in toks))

    def test_stored_click_time_used_for_same_click(self):
        cal = self.booking(
            "utm_source=google&gclid=G1",
            {"quiz_click": json.dumps({"k": "gclid", "v": "G1", "t": 1792000000000}), "gb_uid": UID},
        )
        toks = self.tokens(cal)
        self.assertIn("clickat_1792000000000", toks)
        self.assertIn("gbuid_" + UID, toks)

    def test_stored_click_time_ignored_for_other_click(self):
        cal = self.booking(
            "utm_source=google&gclid=G2",
            {"quiz_click": json.dumps({"k": "gclid", "v": "G1", "t": 1792000000000})},
        )
        self.assertFalse(any(t.startswith("clickat_") for t in self.tokens(cal)))

    def test_click_at_param_wins_over_storage(self):
        cal = self.booking(
            "utm_source=google&gclid=G1&click_at=1791000000000",
            {"quiz_click": json.dumps({"k": "gclid", "v": "G1", "t": 1792000000000})},
        )
        self.assertIn("clickat_1791000000000", self.tokens(cal))

    def test_placeholders_removed_from_page_address(self):
        ctx = self.browser.new_context()
        try:
            page = ctx.new_page()
            page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(self.base) else
                       r.fulfill(status=200, content_type="text/javascript", body=STUB)
                       if "assets.calendly.com" in r.request.url and r.request.url.endswith("widget.js") else r.abort())
            page.goto(f"{self.base}/approved/?name=Test&utm_source=google&utm_term=_____&msclkid=_____&fbclid=")
            page.wait_for_function("window.__cal && window.__cal.length")
            # The address Calendly could copy is the one present when its widget starts.
            qs = parse_qs(page.evaluate("window.__calSearch[0]").lstrip("?"), keep_blank_values=True)
        finally:
            ctx.close()
        self.assertEqual(qs.get("utm_source"), ["google"])
        self.assertNotIn("utm_term", qs)
        self.assertNotIn("msclkid", qs)
        self.assertEqual(qs.get("fbclid"), [""])

    def test_stored_click_time_for_bing_and_facebook_clicks(self):
        for key in ("msclkid", "fbclid"):
            with self.subTest(click=key):
                stored = {"quiz_click": json.dumps({"k": key, "v": "C1", "t": 1792000000000})}
                same = self.tokens(self.booking(f"utm_source=x&{key}=C1", stored))
                self.assertIn(f"{key}_C1", same)
                self.assertIn("clickat_1792000000000", same)
                other = self.tokens(self.booking(f"utm_source=x&{key}=C2", stored))
                self.assertFalse(any(t.startswith("clickat_") for t in other))

    def test_attribution_post_gets_cleaned_values(self):
        posts = []
        self.booking("email=t%40example.com&utm_source=google&utm_term=_____&gclid=G1&msclkid=_____", posts=posts)
        self.assertEqual(len(posts), 1)
        data = posts[0]["data"]
        self.assertEqual(data["email"], "t@example.com")
        self.assertEqual((data["utm_source"], data["gclid"]), ("google", "G1"))
        self.assertEqual((data["utm_term"], data["msclkid"]), ("", ""))

    def test_invalid_visitor_id_is_dropped(self):
        cal = self.booking("utm_source=google", {"gb_uid": "not-a-uuid"})
        self.assertFalse(any(t.startswith("gbuid_") for t in self.tokens(cal)))


if __name__ == "__main__":
    unittest.main()
