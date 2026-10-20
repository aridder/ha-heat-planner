"""Tables for the console and ``RESULTS.md`` (numbers come from ``results.json``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import world as W

OURS = ("heat_planner_rules", "heat_planner_opt", "heat_planner_opt_cap")
SHORT = {"spot": "spot", "spot_support": "spot+støtte", "norgespris": "Norgespris"}


def _ms(cell: dict[str, Any], m: str, d: int = 0) -> str:
    v = cell[m]
    if v["sd"] == 0:
        return f"{v['mean']:.{d}f}"
    return f"{v['mean']:.{d}f} ± {v['sd']:.{d}f}"


def _order(summary: dict[str, Any]) -> list[str]:
    keys = list(summary)
    return [k for k in keys if k not in OURS] + [k for k in OURS if k in keys]


def console(data: dict[str, Any]) -> None:
    s = data["summary"]
    cells = [(season, c) for season in data["meta"]["seasons"] for c in data["meta"]["contracts"]]
    head = "strategy".ljust(26) + "".join(f"{season[:3]}/{SHORT[c][:6]:6s}".rjust(14) for season, c in cells)
    print("\nvalue score = cost + %g kr per °C·h more than 0.3 °C below target (mean over seeds); cost | deficit" %
          data["meta"]["comfort_penalty_kr_per_degree_hour"])
    print(head)
    for k in _order(s):
        line = k.ljust(26)
        for season, c in cells:
            cell = s[k].get(season, {}).get(c)
            line += (f"{cell['value']['mean']:.0f}".rjust(14) if cell else "-".rjust(14))
        print(line)
        line = "".ljust(26)
        for season, c in cells:
            cell = s[k].get(season, {}).get(c)
            line += (f"{cell['cost']['mean']:.0f}|{cell['deficit']['mean']:.0f}".rjust(14) if cell else "".rjust(14))
        print(line)


def _cell_table(s: dict[str, Any], season: str, contract: str) -> list[str]:
    rows = [k for k in _order(s) if season in s[k] and contract in s[k][season]]
    if not rows:
        return []
    ref = s.get("fixed_schedule", {}).get(season, {}).get(contract)
    best = min(s[k][season][contract]["value"]["mean"] for k in rows)
    out = [
        "| strategy | total cost (kr) | Δ cost vs fixed | below target (°C·h) | of which > 0.3 °C | below 18 °C "
        "| too warm | longest cold (h) | heating kWh | top-3 kW | writes/day | value (strict) | **value** |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k in rows:
        c = s[k][season][contract]
        d = "" if not ref else f"{c['cost']['mean'] - ref['cost']['mean']:+.1f}"
        val = _ms(c, "value")
        if abs(c["value"]["mean"] - best) < 0.05:
            val = f"**{val}**"
        name = f"**{k}**" if k in OURS else k
        out.append(
            f"| {name} | {_ms(c, 'cost')} | {d} | {_ms(c, 'deficit')} | {c['deficit_band']['mean']:.0f} "
            f"| {_ms(c, 'below18', 1)} | {_ms(c, 'overheat')} "
            f"| {c['max_cold_h']['mean']:.1f} | {c['heat_kwh']['mean']:.0f} | {c['top3_kw']['mean']:.2f} "
            f"| {c['writes_day']['mean']:.0f} | {c['value_strict']['mean']:.0f} | {val} |"
        )
    return out


def wins(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Per season × contract: best Heat Planner variant vs the best competitor (value score)."""
    s = data["summary"]
    out = []
    for season in data["meta"]["seasons"]:
        for contract in data["meta"]["contracts"]:
            ours = [(s[k][season][contract]["value"]["mean"], k) for k in OURS if k in s and season in s[k]
                    and contract in s[k][season]]
            comp = [(s[k][season][contract]["value"]["mean"], k) for k in s if k not in OURS and season in s[k]
                    and contract in s[k][season]]
            if not ours or not comp:
                continue
            o, c = min(ours), min(comp)
            oc = s[o[1]][season][contract]
            cc = s[c[1]][season][contract]
            out.append({"season": season, "contract": contract, "ours": o[1], "ours_value": o[0],
                        "ours_cost": oc["cost"]["mean"], "ours_deficit": oc["deficit_band"]["mean"],
                        "best_other": c[1], "other_value": c[0], "other_cost": cc["cost"]["mean"],
                        "other_deficit": cc["deficit_band"]["mean"], "margin": round(c[0] - o[0], 2)})
    return out


def round2_lines(data: dict[str, Any]) -> list[str]:
    """Round 2 table (``results.json["round2"]``): best Heat Planner vs the best competitor per
    cell – round 1 (seeds 1–3), now (seeds 1–3) and on fresh seeds never used in development."""
    r2 = data.get("round2")
    if not r2:
        return []
    now = {(w["season"], w["contract"]): w for w in wins(data)}
    fresh = {(w["season"], w["contract"]): w for w in r2.get("fresh_wins", [])}
    before = {(w["season"], w["contract"]): w for w in r2.get("before_wins", [])}
    seeds = ", ".join(str(s) for s in r2.get("fresh_seeds", []))
    out = [
        "### Round 2 per cell: best Heat Planner vs the best competitor (value score; °C·h > 0.3 °C)",
        "",
        f"| season | contract | round 1, seeds 1–3 | round 2, seeds 1–3 | best competitor | top-3 kW ours / theirs "
        f"| writes/day | round 2, fresh seeds {seeds} |",
        "|---|---|---:|---:|---|---:|---:|---:|",
    ]
    s = data["summary"]
    for key, w in now.items():
        b, f = before.get(key), fresh.get(key)
        oc = s[w["ours"]][key[0]][key[1]]
        cc = s[w["best_other"]][key[0]][key[1]]
        r1 = "–" if not b else f"{b['ours_value']:.0f} vs {b['other_value']:.0f} ({b['margin']:+.0f})"
        r2_now = (f"**{w['ours_value']:.0f}** ({w['ours_deficit']:.1f}) vs {w['other_value']:.0f} "
                  f"({w['other_deficit']:.1f}): **{w['margin']:+.0f}**")
        fr = "–" if not f else (f"{f['margin']:+.0f} ({f['ours_deficit']:.1f} vs {f['other_deficit']:.1f} °C·h, "
                                f"{f['best_other']})")
        out.append(
            f"| {key[0]} | {SHORT[key[1]]} | {r1} | {r2_now} | {w['best_other']} "
            f"| {oc['top3_kw']['mean']:.2f} / {cc['top3_kw']['mean']:.2f} | {oc['writes_day']['mean']:.0f} | {fr} |"
        )
    return out + [""]


def write_markdown(data: dict[str, Any], path: Path) -> None:
    meta, s = data["meta"], data["summary"]
    lines = [
        "# Heating-strategy benchmark",
        "",
        "Generated by `PYTHONPATH=. python -m tools.bench` from `tools/bench/results.json` "
        f"({len(data['runs'])} billed runs, {meta['wall_s']:.0f} s wall / {meta['cpu_s']:.0f} CPU s on "
        f"{meta['procs']} processes). Seeds {meta['seeds']}, {meta['days']} days each. "
        "Mean ± standard deviation over seeds.",
        "",
    ]
    from .findings import FINDINGS

    lines += [FINDINGS.strip(), ""]
    lines += round2_lines(data)
    lines += [
        "## Summary: value score per season and contract",
        "",
        f"Value score = total cost + {meta['comfort_penalty_kr_per_degree_hour']:g} kr per °C·h the rooms were more "
        f"than {meta.get('comfort_band', 0.3):g} °C below the occupied target (the thermostats' own hysteresis), "
        "lower is better. Bold: best in the column. The strict variant (from the target itself) is in the detailed "
        "tables and `results.json` (`value_strict`).",
        "",
    ]
    cells = [(season, c) for season in meta["seasons"] for c in meta["contracts"]]
    lines.append("| strategy | " + " | ".join(f"{season} / {SHORT[c]}" for season, c in cells) + " |")
    lines.append("|---|" + "---:|" * len(cells))
    best = {}
    for season, c in cells:
        vals = [s[k][season][c]["value"]["mean"] for k in s if season in s[k] and c in s[k][season]]
        best[(season, c)] = min(vals) if vals else None
    for k in _order(s):
        row = []
        for season, c in cells:
            cell = s[k].get(season, {}).get(c)
            if not cell:
                row.append("–")
                continue
            v = cell["value"]["mean"]
            txt = f"{v:.0f}"
            row.append(f"**{txt}**" if best[(season, c)] is not None and abs(v - best[(season, c)]) < 0.05 else txt)
        name = f"**{k}**" if k in OURS else k
        lines.append(f"| {name} | " + " | ".join(row) + " |")
    lines += ["", "### Heat Planner vs the best competitor (value score)", "",
              "| season | contract | best Heat Planner | value (cost / °C·h > 0.3 °C) | best competitor "
              "| value (cost / °C·h > 0.3 °C) | margin (+ = we win) |", "|---|---|---|---:|---:|---:|---:|"]
    for w in wins(data):
        lines.append(
            f"| {w['season']} | {SHORT[w['contract']]} | {w['ours']} | {w['ours_value']:.0f} "
            f"({w['ours_cost']:.0f} / {w['ours_deficit']:.0f}) | {w['best_other']} | {w['other_value']:.0f} "
            f"({w['other_cost']:.0f} / {w['other_deficit']:.0f}) | {w['margin']:+.0f} |"
        )
    lines += ["", "## Detailed tables", ""]
    for season, label in meta["seasons"].items():
        for c in meta["contracts"]:
            t = _cell_table(s, season, c)
            if t:
                lines += [f"### {label} – {W.CONTRACT_LABEL[c]}", ""] + t + [""]
    lines += ["## Strategy parameters", "", "| strategy | parameters |", "|---|---|"]
    for k, v in meta["strategies"].items():
        lines.append(f"| {k} | `{v['class']}` {v['params'] or 'defaults'} |")
    lines.append("")
    path.write_text("\n".join(lines))
