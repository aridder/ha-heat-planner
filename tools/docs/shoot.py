"""Render the README screenshots (docs/*.png) from the real card and help.

    python3 tools/docs/shoot.py            # writes docs/*.png (English, 2x)

Needs Playwright with Chromium (``pip install playwright && playwright install chromium``).
The data is example data from make_mock.py, run through the integration's own
planner/optimiser; the clock is frozen at Monday 16 November 2026, 10:20 (Oslo).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOCS = ROOT / "docs"
LANG = "en"
CHROMIUM = Path("/opt/pw-browsers/chromium")


def mock(contract: str) -> dict:
    out = subprocess.run(
        [sys.executable, str(HERE / "make_mock.py"), LANG, contract], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out)


def open_page(browser, data: dict, width: int, query: str = ""):
    ctx = browser.new_context(
        viewport={"width": width, "height": 1000}, device_scale_factor=2,
        locale="en-GB", timezone_id="Europe/Oslo",
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.clock.set_fixed_time(datetime.fromisoformat(data["now"]))
    page.add_init_script(f"window.__MOCK = {json.dumps(data)};")
    page.goto(f"{(HERE / 'harness.html').as_uri()}?lang={LANG}{query}")
    page.wait_for_timeout(400)
    return ctx, page, errors


def padded(page, element, path: Path, pad: int = 16) -> None:
    """Screenshot an element with some of the card around it (sections have no margin of their own)."""
    box = element.bounding_box()  # viewport coordinates
    box["y"] += page.evaluate("window.scrollY")
    vw = page.viewport_size["width"]
    x = max(0, box["x"] - pad)
    page.screenshot(path=str(path), full_page=True, clip={
        "x": x, "y": max(0, box["y"] - pad / 2), "width": min(vw - x, box["width"] + 2 * pad), "height": box["height"] + pad,
    })


CARD = "document.querySelector('heat-planner-card').shadowRoot"
HELP = "document.querySelector('heat-planner-help-card heat-planner-help').shadowRoot"


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    spot = mock("spot")
    errors: list[str] = []
    with sync_playwright() as p:
        kw = {"args": ["--headless=new"]}
        if CHROMIUM.is_file():
            kw["executable_path"] = str(CHROMIUM)
        b = p.chromium.launch(**kw)

        # Main card, light and dark
        for theme, name in (("light", "card.png"), ("dark", "dark.png")):
            ctx, pg, err = open_page(b, spot, 940, f"&theme={theme}")
            pg.locator("heat-planner-card").screenshot(path=str(DOCS / name))
            errors += err
            ctx.close()

        # Settings panel (optimiser switch, Auto warm-up, model notes)
        ctx, pg, err = open_page(b, spot, 940)
        pg.evaluate(f"{CARD}.querySelector('details').open = true")
        pg.wait_for_timeout(150)
        padded(pg, pg.locator("heat-planner-card").locator("details"), DOCS / "settings.png", 18)
        errors += err
        ctx.close()

        # Editing a day
        ctx, pg, err = open_page(b, spot, 940)
        pg.evaluate(f"[...{CARD}.querySelectorAll('button.btn')].find((x) => x.textContent === 'Edit day').click()")
        pg.wait_for_timeout(400)
        pg.locator("heat-planner-card").screenshot(path=str(DOCS / "edit.png"))
        errors += err
        ctx.close()

        # The guide: one image per section
        ctx, pg, err = open_page(b, spot, 800, "&help=1")
        for sid in ("day", "rules", "tariff", "peak", "away", "arrival", "manual", "feedback", "safety", "learn", "confidence"):
            handle = pg.evaluate_handle(f"{HELP}.getElementById('{sid}')")
            padded(pg, handle.as_element(), DOCS / f"help-{sid}.png", 22)
        errors += err
        ctx.close()
        b.close()
    if errors:
        print("page errors:", *errors, sep="\n  ")
        sys.exit(1)
    print("wrote", ", ".join(sorted(x.name for x in DOCS.glob("*.png"))))


if __name__ == "__main__":
    main()
