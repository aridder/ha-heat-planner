"""Automated UI checks for the card, its editor and the guide (Playwright + the mock harness).

    python3 tools/docs/check.py [--shots DIR]

Light/dark × nb/en × 360/820/1200 px: no console errors, no horizontal page overflow,
text contrast ≥ 4.5:1 (3:1 for large text), touch targets ≥ 44 px at 360 px. Plus keyboard
navigation on the chart, the visual editor (with a stub <ha-form>), the loading/error/empty/
stale/unavailable states, render skipping on identical pushes and unsubscribe on disconnect.
Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shoot import CHROMIUM, mock, open_page  # noqa: E402

CARD = "document.querySelector('heat-planner-card').shadowRoot"

# Walks the shadow roots and reports text whose contrast against its backdrop is too low.
CONTRAST_JS = r"""
(rootSel) => {
  const parse = (c) => {
    if (!c) return null;
    let m = c.match(/rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p[3] ?? 1]; }
    m = c.match(/color\(srgb ([^)]+)\)/);
    if (m) { const p = m[1].split(/[ /]+/).filter(Boolean).map(Number); return [p[0] * 255, p[1] * 255, p[2] * 255, p[3] ?? 1]; }
    return null;
  };
  const over = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const page = parse(getComputedStyle(document.body).backgroundColor);
  const parentOf = (n) => n.parentElement || (n.getRootNode() instanceof ShadowRoot ? n.getRootNode().host : null);
  const backdrop = (n) => {
    const layers = [];
    for (let e = n; e; e = parentOf(e)) {
      const b = parse(getComputedStyle(e).backgroundColor);
      if (b && b[3] > 0) { layers.push(b); if (b[3] >= 1) break; }
    }
    let bg = page;
    for (const l of layers.reverse()) bg = over(l, bg);
    return bg;
  };
  const opacity = (n) => { let o = 1; for (let e = n; e; e = parentOf(e)) o *= +getComputedStyle(e).opacity; return o; };
  const bad = [];
  const visit = (root) => {
    for (const e of root.querySelectorAll("*")) {
      if (e.shadowRoot) visit(e.shadowRoot);
      const own = [...e.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim());
      if (!own) continue;
      if (e.closest(".sr-only, style, [disabled], [aria-disabled=true], option, select")) continue;
      const r = e.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const cs = getComputedStyle(e);
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      const isSvg = e instanceof SVGElement;
      let fg = parse(isSvg ? cs.fill : cs.color);
      if (!fg) continue;
      fg = [...fg.slice(0, 3), fg[3] * opacity(e)];
      let bg = backdrop(e);
      const mk = e.closest("g.mk");  // number badges: text on a filled circle
      if (mk) { const c = parse(getComputedStyle(mk.querySelector("circle")).fill); if (c) bg = over(c, bg); }
      const rr = ratio(over(fg, bg), bg);
      const size = parseFloat(cs.fontSize), bold = +cs.fontWeight >= 600;
      const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
      if (rr < need - 0.05) bad.push(`${rr.toFixed(2)} < ${need}: "${e.textContent.trim().slice(0, 40)}" (${e.tagName.toLowerCase()}.${e.getAttribute("class") || ""})`);
    }
  };
  visit(document.querySelector(rootSel).shadowRoot || document.querySelector(rootSel));
  return [...new Set(bad)];
}
"""

TARGETS_JS = r"""
(rootSel) => {
  const small = [];
  const visit = (root) => {
    for (const e of root.querySelectorAll("button, a, summary, [role=switch], [role=tab], input")) {
      const r = e.getBoundingClientRect();
      if (!r.width) continue;
      if (r.width < 43.5 || r.height < 43.5) small.push(`${Math.round(r.width)}×${Math.round(r.height)} ${e.tagName.toLowerCase()} "${(e.textContent || e.getAttribute("aria-label") || "").trim().slice(0, 30)}"`);
    }
    for (const e of root.querySelectorAll("*")) if (e.shadowRoot && e.tagName !== "HEAT-PLANNER-HELP") visit(e.shadowRoot);
  };
  visit(document.querySelector(rootSel).shadowRoot);
  return small;
}
"""

OVERFLOW_JS = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=Path, default=None, help="write screenshots here")
    args = ap.parse_args()
    shots = args.shots
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    fails: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    import shoot
    with sync_playwright() as p:
        kw = {"args": ["--headless=new"]}
        if CHROMIUM.is_file():
            kw["executable_path"] = str(CHROMIUM)
        b = p.chromium.launch(**kw)
        for lang in ("en", "nb"):
            shoot.LANG = lang
            d = mock("spot")
            for theme in ("light", "dark"):
                for width in (360, 820, 1200):
                    tag = f"{lang}-{theme}-{width}"
                    ctx, pg, err = open_page(b, d, width, f"&theme={theme}")
                    pg.wait_for_timeout(200)
                    pg.evaluate(f"{CARD}.querySelector('details').open = true")
                    pg.wait_for_timeout(100)
                    check(pg.evaluate(OVERFLOW_JS) <= 0, f"{tag}: page overflows horizontally")
                    for c in pg.evaluate(CONTRAST_JS, "heat-planner-card"):
                        fails.append(f"{tag}: contrast {c}")
                    if width == 360:
                        for s in pg.evaluate(TARGETS_JS, "heat-planner-card"):
                            fails.append(f"{tag}: small target {s}")
                    if shots:
                        pg.locator("heat-planner-card").screenshot(path=str(shots / f"card-{tag}.png"))
                    fails += [f"{tag}: console: {e}" for e in err]
                    ctx.close()
                    # the guide
                    ctx, pg, err = open_page(b, d, width, f"&theme={theme}&help=1")
                    check(pg.evaluate(OVERFLOW_JS) <= 0, f"{tag}: guide overflows horizontally")
                    for c in pg.evaluate(CONTRAST_JS, "heat-planner-help-card heat-planner-help"):
                        fails.append(f"{tag}: guide contrast {c}")
                    toc = pg.evaluate("""() => { const r = document.querySelector('heat-planner-help-card heat-planner-help').shadowRoot;
                        const ids = [...r.querySelectorAll('[data-go]')].map((b) => b.dataset.go);
                        const secs = [...r.querySelectorAll('section')].map((s) => s.id);
                        return { ids, secs }; }""")
                    check(toc["ids"] == toc["secs"], f"{tag}: guide TOC {toc['ids']} != sections {toc['secs']}")
                    if shots and width == 360:
                        pg.screenshot(path=str(shots / f"help-{tag}.png"), full_page=True)
                    fails += [f"{tag}: guide console: {e}" for e in err]
                    ctx.close()
        shoot.LANG = "en"
        d = mock("spot")

        # Keyboard: Tab to the chart, move with arrows, Enter edits, Up raises, Escape after discard.
        ctx, pg, err = open_page(b, d, 820)
        pg.keyboard.press("Tab")
        for _ in range(12):
            if pg.evaluate(f"{CARD}.activeElement?.dataset?.fk") == "chart":
                break
            pg.keyboard.press("Tab")
        check(pg.evaluate(f"{CARD}.activeElement?.dataset?.fk") == "chart", "keyboard: chart not reachable with Tab")
        pg.wait_for_timeout(100)
        tip0 = pg.evaluate(f"{CARD}.querySelector('.tip')?.textContent || ''")
        pg.keyboard.press("ArrowRight")
        pg.wait_for_timeout(120)
        tip1 = pg.evaluate(f"{CARD}.querySelector('.tip')?.textContent || ''")
        check(bool(tip1) and tip1 != tip0, f"keyboard: ArrowRight did not move the tooltip ({tip0!r} → {tip1!r})")
        live = pg.evaluate(f"{CARD}.querySelector('[aria-live]').textContent")
        check("Target" in live or "Off" in live, f"keyboard: live region not updated ({live!r})")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(400)
        check(pg.evaluate(f"!!{CARD}.querySelector('.editor')"), "keyboard: Enter did not start editing")
        check(pg.evaluate(f"{CARD}.activeElement?.dataset?.fk") == "chart", "keyboard: focus lost after Enter")
        before = pg.evaluate(f"{CARD}.querySelectorAll('circle.handle.dirty').length")
        pg.keyboard.press("ArrowUp")
        pg.wait_for_timeout(300)
        after = pg.evaluate(f"{CARD}.querySelectorAll('circle.handle.dirty').length")
        check(after == before + 1, "keyboard: ArrowUp did not change the value")
        check(pg.evaluate(f"{CARD}.activeElement?.dataset?.fk") == "chart", "keyboard: focus lost after ArrowUp")
        check(not pg.evaluate(f"{CARD}.querySelector('button[data-fk=save]').disabled"), "keyboard: Save not enabled after edit")
        if shots:
            pg.locator("heat-planner-card").screenshot(path=str(shots / "keyboard-edit.png"))
        fails += [f"keyboard: console: {e}" for e in err]
        ctx.close()

        # Identical pushes don't re-render; disconnect unsubscribes.
        ctx, pg, err = open_page(b, d, 820)
        pg.evaluate(f"window.__body = {CARD}.querySelector('ha-card').lastElementChild")
        pg.evaluate("window.__push()")
        pg.wait_for_timeout(100)
        check(pg.evaluate(f"window.__body === {CARD}.querySelector('ha-card').lastElementChild"), "perf: identical push re-rendered")
        pg.evaluate("document.querySelector('heat-planner-card').remove()")
        pg.wait_for_timeout(50)
        check(pg.evaluate("window.__unsubscribed") == 1, "perf: no unsubscribe on disconnect")
        ctx.close()

        # 12-hour clock
        ctx, pg, err = open_page(b, d, 1200, "&h12=1")
        axis = pg.evaluate(f"[...{CARD}.querySelectorAll('.axis text')].map((t) => t.textContent).join(' ')")
        check("PM" in axis or "AM" in axis, f"12h: axis not in 12-hour format ({axis[:60]!r})")
        fails += [f"12h: console: {e}" for e in err]
        ctx.close()

        # States
        expect = {"loading": "[aria-busy=true]", "error": "button[data-fk=retry]", "empty": "a[data-fk=setup]",
                  "nozones": "a[data-fk=integration]", "stale": ".notice", "unavailable": ".notice"}
        for state, sel in expect.items():
            ctx, pg, err = open_page(b, d, 360, f"&state={state}")
            check(pg.evaluate(f"!!{CARD}.querySelector('{sel}')"), f"state {state}: {sel} missing")
            check(pg.evaluate(OVERFLOW_JS) <= 0, f"state {state}: page overflows")
            if shots:
                pg.locator("heat-planner-card").screenshot(path=str(shots / f"state-{state}.png"))
            fails += [f"state {state}: console: {e}" for e in err if state != "error"]
            ctx.close()

        # Visual editor with a stub ha-form
        ctx, pg, err = open_page(b, d, 820, "&editor=1")
        pg.wait_for_timeout(200)
        names = pg.evaluate("[...document.querySelector('heat-planner-card-editor').shadowRoot.querySelectorAll('ha-form label')].map((l) => l.dataset.name)")
        check(names == ["zones", "hours", "show_help"], f"editor: schema {names}")
        pg.evaluate("document.querySelector('heat-planner-card-editor').shadowRoot.querySelector('ha-form [data-name=zones] input').click()")
        pg.wait_for_timeout(50)
        cfgs = pg.evaluate("window.__configs")
        check(bool(cfgs) and cfgs[-1].get("zones") == ["living_room"] and cfgs[-1].get("type") == "custom:heat-planner-card", f"editor: config {cfgs}")
        if shots:
            pg.locator("heat-planner-card-editor").screenshot(path=str(shots / "editor.png"))
        fails += [f"editor: console: {e}" for e in err]
        ctx.close()
        b.close()

    if fails:
        print("FAILED:", *fails, sep="\n  ")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
