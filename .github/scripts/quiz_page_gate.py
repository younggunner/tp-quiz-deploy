#!/usr/bin/env python3
"""Quiz-page CI gate (QS WP5). Hard-fails the defect classes in the quiz spec's
Part 0. Modeled on tinypumper-deploy's beacon-gate + ppc-page-gate.

Each rule is a scar:
  R2  more than one attribution-script marker (June 2026 blanked all quiz UTMs)
  R4  base64 data:image/ or data:video/ (2026-08-18 16.5 MB PPC pages)
  R5  missing noindex,nofollow
  R6  pixel 769334093165499 or any GTM container other than GTM-5PQGM83
  R9  em dashes in visible copy
  R10 GB-only stats in TP files
  R13 missing engagement-beacon include (root meta-refresh stub exempt)
  WP5 missing canonical; wrong Typeform form id; HTML over 500 KB

--self-test plants violations in temp copies and asserts the gate catches them,
then asserts the real tree is clean. That is the WP5 acceptance ("fails on a
deliberately broken test, then passes on main") without a broken commit.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("GITHUB_WORKSPACE") or Path(__file__).resolve().parents[2])
MAX_HTML_BYTES = 500_000
GTM_OK = "GTM-5PQGM83"
PIXEL_BANNED = "769334093165499"
ATTR_MARKER = "Attribution script"
BEACON_NEEDLE = "lander-engagement"
GB_STATS = re.compile(
    r"6%\s*pump[- ]to[- ]net|98%\s*success\s*rate|200%\s*money[- ]back|"
    r"\b2\s+to\s+2,000\s+wells\b",
    re.I,
)


def brand_of(root: Path) -> tuple[str, str]:
    cname = (root / "CNAME").read_text().strip().lower()
    if "tinypumper" in cname:
        return "tp", "uVP2g9QP"
    if "greasebook" in cname:
        return "gb", "sTbsRn"
    raise SystemExit(f"unrecognized CNAME {cname!r}")


def html_files(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*.html"):
        if "/.git/" in str(p) or "/assets/" in str(p):
            continue
        out.append(p)
    return sorted(out)


def is_refresh_stub(text: str) -> bool:
    return bool(re.search(r'http-equiv=["\']refresh["\']', text, re.I))


def visible_text(html: str) -> str:
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return html


def check_file(path: Path, brand: str, form_id: str) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    fails: list[str] = []
    stub = is_refresh_stub(text)

    if text.count(ATTR_MARKER) > 1:
        fails.append(f"{rel}: more than one attribution-script marker (R2)")
    if re.search(r"data:(image|video)/", text, re.I):
        fails.append(f"{rel}: base64 data:image/ or data:video/ URI (R4)")
    if not re.search(r'name=["\']robots["\']\s+content=["\']noindex,\s*nofollow["\']', text, re.I):
        fails.append(f"{rel}: missing noindex,nofollow (R5)")
    if PIXEL_BANNED in text:
        fails.append(f"{rel}: banned pixel {PIXEL_BANNED} (R6)")
    gtms = set(re.findall(r"GTM-[A-Z0-9]+", text))
    if gtms and gtms != {GTM_OK}:
        fails.append(f"{rel}: GTM container {sorted(gtms)} is not {GTM_OK} (R6)")
    if not stub:
        if BEACON_NEEDLE not in text:
            fails.append(f"{rel}: missing engagement-beacon include (R13)")
        if not re.search(r'rel=["\']canonical["\']', text, re.I):
            fails.append(f"{rel}: missing canonical link")
    if path.stat().st_size > MAX_HTML_BYTES:
        fails.append(f"{rel}: HTML file {path.stat().st_size} bytes > 500 KB")
    vis = visible_text(text)
    if "\u2014" in vis:
        fails.append(f"{rel}: em dash in visible copy (R9)")
    if brand == "tp" and GB_STATS.search(text):
        fails.append(f"{rel}: GB-only stats in TP file (R10)")
    for m in re.finditer(r'data-tf-widget=["\']([^"\']+)["\']', text):
        if m.group(1) != form_id:
            fails.append(
                f"{rel}: data-tf-widget={m.group(1)!r} is not this brand's form {form_id}"
            )
    return fails


def run(root: Path) -> list[str]:
    brand, form_id = brand_of(root)
    fails: list[str] = []
    for p in html_files(root):
        fails.extend(check_file(p, brand, form_id))
    return fails


def self_test() -> int:
    """Plant each violation class in a temp copy of the quiz page; assert fail.
    Then assert the real tree is clean."""
    brand, form_id = brand_of(ROOT)
    quiz = ROOT / "quiz" / "index.html"
    src = quiz.read_text(encoding="utf-8")
    plants = {
        "dup-attr": src + "\n<!-- Attribution script DUPLICATE -->\n",
        "base64": src.replace("<body>", '<body><img src="data:image/png;base64,AAAA">'),
        "no-robots": re.sub(r'<meta name="robots"[^>]*>', "", src, count=1),
        "bad-gtm": src.replace(GTM_OK, "GTM-WRONG1"),
        "emdash": src.replace("</p>", " \u2014</p>", 1) if "</p>" in src else src + "\n\u2014\n",
        "no-beacon": src.replace(BEACON_NEEDLE, "NOT-A-BEACON"),
        "no-canon": re.sub(r'<link rel="canonical"[^>]*>', "", src, count=1),
        "wrong-form": src.replace(f'data-tf-widget="{form_id}"', 'data-tf-widget="XXXXXXX"'),
    }
    if brand == "tp":
        plants["gb-stats"] = src.replace("60 seconds.", "6% pump-to-net. 60 seconds.")

    caught = []
    missed = []
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        (tdir / "quiz").mkdir()
        shutil.copy(ROOT / "CNAME", tdir / "CNAME")
        for name, html in plants.items():
            p = tdir / "quiz" / "index.html"
            p.write_text(html)
            # Rewrite ROOT temporarily by invoking check_file with tdir as root
            fails = check_file(p, brand, form_id)
            # check_file uses path.relative_to(ROOT) which will fail; that's ok,
            # we only care that fails is non-empty.
            if fails:
                caught.append(name)
            else:
                missed.append(name)
    if missed:
        print("SELF-TEST MISS (gate did not catch):", ", ".join(missed))
        return 1
    print(f"self-test planted {len(caught)} violations, all caught")
    real = run(ROOT)
    if real:
        print("self-test: real tree is NOT clean:")
        for f in real:
            print(" ", f)
        return 1
    print("self-test: real tree clean")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    fails = run(ROOT)
    if fails:
        for f in fails:
            print(f"::error::{f}")
        print(f"{len(fails)} quiz-page-gate failure(s)")
        return 1
    print("quiz-page-gate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
