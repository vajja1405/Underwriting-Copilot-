"""Screenshot the Founder Studio and Lender Desk feature pages against the mock API, with Playwright + Chromium.

Run after `npm run build` (from the repository root):

    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \\
        uv run --with playwright==1.56.0 --with pillow python frontend/scripts/screenshots_mock.py --small

(`make web-screenshots-mock` runs exactly this.) PNGs go to docs/web/screenshots/mock/; --small saves them with a
256-color palette so the docs folder stays small (Pillow is only needed for --small).

What it does:
1. Starts the mock API (frontend/mock-api/server.mjs, Node) on a free local port. It serves frontend/out and replays
   answers recorded from the real backend on SYNTHETIC inputs (frontend/scripts/record_mock_fixtures.py).
2. At 1280 px and 375 px, in light and dark, signs in through the DEV LOGIN form as a SYNTHETIC demo founder and
   saves full-page PNGs of: the plan list, the wizard (first step and the sites step with the map), the results
   dashboard, the path section after editing the planning targets (a recorded edit), the lender queue, a lender
   file, the guideline-section drawer, a refused decision and a recorded one, and two error states (a 503 "took too
   long" plan, a signed-out file page).
3. Fails (exit 1) on any sideways overflow, any browser console error, or a missing element it waits for.

No internet access is needed. The pages' numbers all come from the recorded answers of the real API.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, ConsoleMessage, Locator, Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"
DEFAULT_SCREENSHOT_DIR = REPO_ROOT / "docs" / "web" / "screenshots" / "mock"
WIDTHS = (1280, 375)
COLOR_SCHEMES = ("light", "dark")
# The recorded SYNTHETIC demo plan (frontend/mock-api/fixtures, written by record_mock_fixtures.py).
DEMO_PLAN = json.loads((FRONTEND / "mock-api" / "fixtures" / "project_demo.json").read_text(encoding="utf-8"))["id"]
DEMO_FILE = "APP-S-001"
# The first recorded target edit for which the path search found plan changes (None if none was recorded).
EDITED_TARGETS = next(
    (
        entry["targets"]
        for entry in json.loads((FRONTEND / "mock-api" / "fixtures" / "path_demo_targets.json").read_text("utf-8"))
        if entry["result"]["status"] == "paths_found"
    ),
    None,
)
# Tallest single capture (Chromium repeats the top of the page below about 8,192 px); taller pages are saved in
# equal parts no taller than PART_HEIGHT (see save()).
SINGLE_MAX_HEIGHT = 8000
PART_HEIGHT = 7000


def free_port() -> int:
    """Ask the OS for an unused local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def mock_server() -> Iterator[str]:
    """Start `node mock-api/server.mjs` and yield its base URL."""
    port = free_port()
    env = {**os.environ, "MOCK_PORT": str(port)}
    process = subprocess.Popen(
        ["node", str(FRONTEND / "mock-api" / "server.mjs")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(base_url + "/api/health", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        yield base_url
    finally:
        process.terminate()
        process.wait(timeout=10)


class Checks:
    """Console errors and layout problems collected over the run (and whether to save small PNGs)."""

    def __init__(self, small: bool = False) -> None:
        self.problems: list[str] = []
        self.small = small

    def watch(self, page: Page, name: str) -> None:
        def on_console(message: ConsoleMessage) -> None:
            # 4xx/5xx answers the page expects (the 503 and 401 error paths) show up as resource errors; skip those.
            if message.type == "error" and "Failed to load resource" not in message.text:
                self.problems.append(f"{name}: console: {message.text}")

        page.on("console", on_console)
        page.on("pageerror", lambda error: self.problems.append(f"{name}: pageerror: {error}"))


def settle(page: Page, wait_for: str | None = None, timeout_ms: int = 20_000) -> None:
    """Wait for the network to go quiet, fonts to load and (optionally) a piece of text to appear."""
    page.wait_for_load_state("networkidle")
    if wait_for:
        page.get_by_text(wait_for, exact=False).first.wait_for(timeout=timeout_ms)
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(500)


def finish(path: Path, checks: Checks) -> None:
    """With --small, rewrite the PNG with a 256-color palette (about a third of the size, for docs/); then log it."""
    if checks.small:
        from PIL import Image  # only needed with --small: `uv run --with pillow`

        with Image.open(path) as image:
            small = image.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        small.save(path, optimize=True)
    print(f"saved {path}")


def save(page: Page, path: Path, checks: Checks, *, full_page: bool = True) -> None:
    """Screenshot; note a problem if the page scrolls sideways."""
    overflow = int(page.evaluate("document.documentElement.scrollWidth - window.innerWidth"))
    if overflow > 0:
        checks.problems.append(f"{path.name}: page is {overflow}px wider than the viewport")
    if not full_page:
        page.screenshot(path=str(path))
        finish(path, checks)
        return
    # A full-page capture of a scrolled page draws the sticky header where the viewport was; start at the top.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    height = int(page.evaluate("document.documentElement.scrollHeight"))
    width = int(page.evaluate("window.innerWidth"))
    if height <= SINGLE_MAX_HEIGHT:
        page.screenshot(path=str(path), full_page=True)
        finish(path, checks)
        return
    parts = math.ceil(height / PART_HEIGHT)
    part_height = math.ceil(height / parts)
    for index, top in enumerate(range(0, height, part_height), start=1):
        part = path.with_name(f"{path.stem}-part{index}{path.suffix}")
        clip = {"x": 0, "y": top, "width": width, "height": min(part_height, height - top)}
        page.screenshot(path=str(part), full_page=True, clip=clip)
        finish(part, checks)


def percent_text(share: float) -> str:
    """0.45 -> "45" (what a founder types into a percent field)."""
    return f"{share * 100:g}"


def save_element(page: Page, locator: Locator, path: Path, checks: Checks) -> None:
    """Screenshot one part of the page: a clip of a full-page capture taken from the top, so the sticky header
    stays at the top of the page instead of being drawn over the part."""
    box = locator.evaluate(
        "element => { const r = element.getBoundingClientRect();"
        " return { x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height }; }"
    )
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    margin = 16
    clip = {
        "x": max(0, box["x"] - margin),
        "y": max(0, box["y"] - margin),
        "width": box["width"] + 2 * margin,
        "height": box["height"] + 2 * margin,
    }
    page.screenshot(path=str(path), full_page=True, clip=clip)
    finish(path, checks)


def dev_login(page: Page, base_url: str, username: str, role_label: str) -> None:
    """Sign in through the DEV LOGIN form (the mock API says dev_auth is on)."""
    page.goto(base_url + "/signin/")
    settle(page, "Dev login")
    page.get_by_label("Username").fill(username)
    page.get_by_role("combobox", name="Role").click()
    page.get_by_role("option", name=role_label).click()
    page.get_by_role("button", name="Sign in (dev)").click()
    page.wait_for_url("**/studio/" if role_label == "Founder" else "**/lender/")


def studio_pages(page: Page, base_url: str, tag: str, out: Path, checks: Checks, wide: bool) -> None:
    """Plan list, wizard, results dashboard and the 503 error path."""
    page.goto(base_url + "/studio/")
    settle(page, "Your plans")
    save(page, out / f"studio-plans-{tag}.png", checks)

    page.goto(base_url + "/studio/new/")
    settle(page, "Which city?")
    save(page, out / f"studio-wizard-city-{tag}.png", checks)
    page.get_by_text("Synthetic Metro (SYNTHETIC)").first.click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_text("Laundromat", exact=True).first.click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_label("Your idea in your own words").fill(
        "A self-service laundromat near apartment blocks (SYNTHETIC test)."
    )
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()  # empty money step: shows the errors
    settle(page, "Please fix")
    save(page, out / f"studio-wizard-money-errors-{tag}.png", checks)
    page.get_by_label("Build-out and equipment budget").fill("500000")
    page.get_by_label("Your own cash (equity)").fill("200000")
    page.get_by_label("Loan amount").fill("400000")
    page.get_by_role("button", name="Continue").click()
    settle(page, "Candidate sites")
    page.locator(".maplibregl-canvas").first.wait_for(timeout=20_000)
    page.get_by_role("button", name="Suggest areas for a laundromat").click()
    settle(page, "Suggested areas for this business type")
    page.get_by_role("button", name="Use this area").first.click()
    page.get_by_label("Or pick a census tract from the list").select_option(index=5)
    page.get_by_role("button", name="Add as a site").click()
    page.wait_for_timeout(800)
    save(page, out / f"studio-wizard-sites-{tag}.png", checks)
    page.get_by_role("button", name="Continue").click()
    settle(page, "Check and create the plan")
    save(page, out / f"studio-wizard-review-{tag}.png", checks)

    page.goto(base_url + f"/studio/project/?id={DEMO_PLAN}")
    settle(page, "Every input the simulation sampled", timeout_ms=30_000)
    settle(page, "What the search never changes", timeout_ms=30_000)
    page.locator(".recharts-surface").first.wait_for()
    save(page, out / f"studio-results-{tag}.png", checks)

    if EDITED_TARGETS:
        # Edit the planning targets (the recorded edit) and screenshot the path section with the plan changes found.
        page.get_by_role("button", name="Edit the targets").click()
        page.get_by_label("Year-2 DSCR at least").fill(percent_text(EDITED_TARGETS["p_dscr_min"]))
        page.get_by_label("With revenue").fill(percent_text(EDITED_TARGETS["p_stressed_min"]))
        page.get_by_label("Cash below $0").fill(percent_text(EDITED_TARGETS["p_cash_negative_max"]))
        page.get_by_role("button", name="Search again with these targets").click()
        settle(page, "to reach the simulated targets", timeout_ms=30_000)
        path_section = page.locator("section[aria-labelledby='path-title']")
        save_element(page, path_section, out / f"studio-path-edited-{tag}.png", checks)

    page.goto(base_url + "/studio/project/?id=timeout-check")
    settle(page, "The server is busy or this took too long")
    page.get_by_text("The server is busy or this took too long").first.scroll_into_view_if_needed()
    page.evaluate("window.scrollBy(0, 200)")
    save(page, out / f"studio-results-timeout-{tag}.png", checks, full_page=False)


def lender_pages(page: Page, base_url: str, tag: str, out: Path, checks: Checks, wide: bool) -> None:
    """Queue, a file, the section drawer, a refused decision and a recorded one."""
    page.goto(base_url + "/lender/")
    settle(page, "Demo queue")
    settle(page, "demo files")
    save(page, out / f"lender-queue-{tag}.png", checks)

    page.goto(base_url + f"/lender/file/?id={DEMO_FILE}")
    settle(page, "The policy suggests")
    save(page, out / f"lender-file-{tag}.png", checks)

    page.get_by_role("button", name="Open guideline section").first.click()
    page.get_by_role("dialog").wait_for()
    page.wait_for_timeout(600)
    save(page, out / f"lender-drawer-{tag}.png", checks, full_page=False)
    page.keyboard.press("Escape")
    page.get_by_role("dialog").wait_for(state="hidden")

    page.get_by_role("button", name="Request more information").click()
    page.get_by_label("Why request more information? (required)").fill(
        "Need the signed lease and equipment quotes (SYNTHETIC demo)."
    )
    page.get_by_role("button", name="Record: Request more information").click()
    settle(page, "Decision record")
    save(page, out / f"lender-decision-recorded-{tag}.png", checks)


def signed_out_page(context: BrowserContext, base_url: str, tag: str, out: Path, checks: Checks) -> None:
    """A file page with nobody signed in shows the sign-in prompt that comes back here."""
    context.clear_cookies()
    page = context.new_page()
    checks.watch(page, f"signed-out-{tag}")
    page.goto(base_url + f"/lender/file/?id={DEMO_FILE}")
    settle(page, "Sign in to open Lender Desk")
    save(page, out / f"lender-file-signed-out-{tag}.png", checks, full_page=False)
    page.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--screenshot-dir", type=Path, default=DEFAULT_SCREENSHOT_DIR)
    parser.add_argument("--only", choices=["studio", "lender"], default=None)
    parser.add_argument("--widths", default=",".join(str(width) for width in WIDTHS))
    parser.add_argument("--schemes", default=",".join(COLOR_SCHEMES))
    parser.add_argument(
        "--small", action="store_true", help="Save 256-color PNGs (needs Pillow: uv run --with pillow)."
    )
    args = parser.parse_args()
    if not (FRONTEND / "out" / "index.html").exists():
        print("frontend/out has no index.html: run `npm run build` in frontend/ first.", file=sys.stderr)
        return 1
    args.screenshot_dir.mkdir(parents=True, exist_ok=True)

    checks = Checks(small=args.small)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        try:
            for scheme in args.schemes.split(","):
                for width in (int(value) for value in args.widths.split(",")):
                    tag = f"{width}-{scheme}"
                    # A fresh mock server per run: its in-memory plans and demo decisions start from the fixtures.
                    with mock_server() as base_url:
                        run_variant(browser, base_url, width, scheme, tag, args, checks)
        finally:
            browser.close()

    for problem in checks.problems:
        print(f"PROBLEM {problem}", file=sys.stderr)
    if checks.problems:
        return 1
    print("no horizontal overflow and no browser console errors (mock API)")
    return 0


def run_variant(
    browser: Browser, base_url: str, width: int, scheme: str, tag: str, args: argparse.Namespace, checks: Checks
) -> None:
    """Every page for one width and color scheme."""
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = context.new_page()
    checks.watch(page, tag)
    dev_login(page, base_url, "demo-founder", "Founder")
    if args.only in (None, "studio"):
        studio_pages(page, base_url, tag, args.screenshot_dir, checks, width >= 768)
    if args.only in (None, "lender"):
        lender_pages(page, base_url, tag, args.screenshot_dir, checks, width >= 768)
        signed_out_page(context, base_url, tag, args.screenshot_dir, checks)
    context.close()


if __name__ == "__main__":
    raise SystemExit(main())
