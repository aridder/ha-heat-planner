"""Render the card in its main states for design review (not committed images).

    python3 tools/docs/states.py OUTDIR [width]

Writes OUTDIR/<contract>-<zone>-<view>-<theme>.png for spot and Norgespris, every zone,
the plan and the 24 h look-back, light and dark.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

CARD = shoot.CARD


def main() -> None:
    out = Path(sys.argv[1])
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 800
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        kw = {"args": ["--headless=new"]}
        if shoot.CHROMIUM.is_file():
            kw["executable_path"] = str(shoot.CHROMIUM)
        b = p.chromium.launch(**kw)
        for contract in ("spot", "fixed"):
            data = shoot.mock(contract)
            zones = [z["zone_id"] for z in data["entries"][0]["zones"]]
            for theme in ("light", "dark"):
                for i, zid in enumerate(zones):
                    for view in ("plan", "past"):
                        if theme == "dark" and (view == "past" or i):
                            continue
                        ctx, pg, err = shoot.open_page(b, data, width, f"&theme={theme}")
                        pg.evaluate(f"(() => {{ const t = [...{CARD}.querySelectorAll('[role=tab]')]; t[{i}] && t[{i}].click(); }})()")
                        pg.wait_for_timeout(150)
                        if view == "past":
                            pg.evaluate(f"(() => {{ const b = [...{CARD}.querySelectorAll('button')].find((x) => /24/.test(x.textContent)); b && b.click(); }})()")
                            pg.wait_for_timeout(400)
                        pg.locator("heat-planner-card").screenshot(path=str(out / f"{contract}-{zid}-{view}-{theme}.png"))
                        errors += err
                        ctx.close()
        b.close()
    if errors:
        print("page errors:", *errors, sep="\n  ")
    print("wrote", len(list(out.glob("*.png"))), "images to", out)


if __name__ == "__main__":
    main()
