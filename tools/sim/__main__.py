"""Run the scenario matrix and print a KPI table.

    PYTHONPATH=. python -m tools.sim [--days 7] [--quick] [--only name,name]
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import time

from .harness import run
from .scenarios import PEAK_PAIRS, matrix

COLS = (
    ("scenario", 18), ("days", 4), ("minT", 6), ("<safe", 5), ("prot", 4), ("off_h", 5), ("wr/d", 4),
    ("dDef", 5), ("cost", 7), ("base", 7), ("real", 6), ("claim", 6), ("top3", 23), ("np", 3),
    ("ms", 6), ("ok", 3),
)


def row(r, top3: str) -> str:
    k = r.kpi
    vals = (
        r.scenario, f"{r.days:g}", k["min_temp"], k["below_safety_min"], k["protected_bad"], k["max_off_h"],
        k["writes_day"], k["deficit_extra"], k["cost_ctl"], k["cost_base"], k["realised"], k["claimed"],
        top3, k["norgespris_shift"], k["replan_ms_max"], "ok" if r.ok() else "NO",
    )
    return " ".join(str(v)[:w].ljust(w) for v, (_, w) in zip(vals, COLS))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {s for s in args.only.split(",") if s}
    print(" ".join(n.ljust(w) for n, w in COLS))
    failures = []
    for sc in matrix(args.days, quick=args.quick):
        if only and sc.name not in only:
            continue
        t0 = time.time()
        r = run(sc)
        top3 = ",".join(f"{v:g}" for v in r.kpi["top3"].values())
        if sc.name in PEAK_PAIRS and sc.peak_limit:
            off = run(replace(sc, name=sc.name + "/nolim", peak_limit=False))
            top3 += " /" + ",".join(f"{v:g}" for v in off.kpi["top3"].values())
            for month, v in r.kpi["top3"].items():
                if v > off.kpi["top3"].get(month, 0.0) + 0.05:
                    r.violations.append(f"top-3 peak {month} {v} with limiting > {off.kpi['top3'][month]} without")
        print(row(r, top3), f"{time.time() - t0:.0f}s")
        for v in r.violations:
            print("   !", v)
            failures.append((sc.name, v))
    print(f"\n{len(failures)} invariant violation(s)")


if __name__ == "__main__":
    main()
