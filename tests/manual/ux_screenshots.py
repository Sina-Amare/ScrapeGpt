"""Dev-only UX screenshot harness.

Logs in ONCE and navigates entirely CLIENT-SIDE (react-router link clicks), so
the SPA never full-reloads and the in-memory access token persists for the whole
run (no repeated token refreshes, no rate-limit/logout artifacts). Captures
desktop then resizes the same page to mobile. Public pages use a logged-out
context. Not part of the automated suite.

Usage:
    UX_BASE=http://127.0.0.1:5191 \
    venv\\Scripts\\python.exe -m tests.manual.ux_screenshots .ux-shots/round
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("UX_BASE", "http://127.0.0.1:5191")
# Credentials are read from the environment — never hardcode real ones in a
# committed file. Set UX_EMAIL / UX_PASSWORD (a throwaway/dev account) first.
EMAIL = os.environ.get("UX_EMAIL", "")
PASSWORD = os.environ.get("UX_PASSWORD", "")
OUT = sys.argv[1] if len(sys.argv) > 1 else ".ux-shots/round"

if not EMAIL or not PASSWORD:
    sys.exit("Set UX_EMAIL and UX_PASSWORD env vars (a dev/throwaway account) before running.")

PUBLIC = [("login", "/login"), ("register", "/register"), ("forgot", "/forgot-password")]
# (sidebar href, screenshot name) — order matches the sidebar.
NAV = [
    ("/dashboard", "dashboard"),
    ("/projects", "projects"),
    ("/projects/new", "projects-new"),
    ("/providers", "providers"),
    ("/sessions", "sessions"),
    ("/help", "help"),
    ("/health", "health"),
]
DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}


def snap(page, name):
    page.wait_for_timeout(1100)
    page.screenshot(path=os.path.join(OUT, name + ".png"), full_page=True)
    print("shot", name)


def nav_click(page, href, mobile):
    if mobile:
        # Open the hamburger drawer first (desktop sidebar is display:none).
        try:
            page.locator("header button:has(svg.lucide-menu)").first.click()
            page.wait_for_timeout(350)
            page.locator(f"div.z-40 a[href='{href}']").first.click()
        except Exception:
            page.goto(BASE + href)
    else:
        page.locator(f"aside a[href='{href}']").first.click()
    page.wait_for_load_state("networkidle")


def capture_projects(page, prefix, mobile):
    """Open up to two projects from the list (workspace + results screens)."""
    nav_click(page, "/projects", mobile)
    page.wait_for_timeout(900)
    hrefs = page.eval_on_selector_all(
        "tbody tr a[href^='/projects/']", "els => els.map(e => e.getAttribute('href'))"
    )
    hrefs = [h for h in dict.fromkeys(hrefs) if h and not h.endswith("/new")][:2]
    for i, href in enumerate(hrefs):
        try:
            page.locator(f"a[href='{href}']").first.click()
            page.wait_for_url("**" + href, timeout=8000)
            snap(page, f"{prefix}_project{i + 1}")
            nav_click(page, "/projects", mobile)
            page.wait_for_timeout(700)
        except Exception as exc:
            print("project capture failed", href, exc)


def capture(page, prefix, mobile):
    # Dashboard is the post-login landing page — snap without navigating.
    snap(page, f"{prefix}_dashboard")
    for href, name in NAV[1:]:
        nav_click(page, href, mobile)
        snap(page, f"{prefix}_{name}")
    capture_projects(page, prefix, mobile)


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport=DESKTOP)
        page = ctx.new_page()
        page.goto(BASE + "/login", wait_until="networkidle", timeout=25000)
        page.fill("input[type=email]", EMAIL)
        page.fill("input[type=password]", PASSWORD)
        page.press("input[type=password]", "Enter")
        page.wait_for_url("**/dashboard", timeout=15000)
        capture(page, "desktop", mobile=False)
        # Resize the SAME page to mobile — session persists (no reload).
        page.set_viewport_size(MOBILE)
        page.goto(BASE + "/dashboard", wait_until="networkidle")  # one reload to reflow
        capture(page, "mobile", mobile=True)
        ctx.close()

        for prefix, vp in (("desktop", DESKTOP), ("mobile", MOBILE)):
            pubctx = browser.new_context(viewport=vp)
            pubpage = pubctx.new_page()
            for name, path in PUBLIC:
                pubpage.goto(BASE + path, wait_until="networkidle", timeout=25000)
                snap(pubpage, f"{prefix}_{name}")
            pubctx.close()
        browser.close()
    print("DONE ->", OUT)


if __name__ == "__main__":
    main()
