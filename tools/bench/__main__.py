"""Run the heating-strategy benchmark and write ``results.json`` and ``RESULTS.md``.

    PYTHONPATH=. python -m tools.bench                 # full matrix (3 seasons × 3 contracts × 3 seeds)
    PYTHONPATH=. python -m tools.bench --quick         # 1 seed, 4 days (smoke test)
    PYTHONPATH=. python -m tools.bench --tune          # re-tune the competitors (writes tuning.json)
    PYTHONPATH=. python -m tools.bench --seeds 101,102 --only heat_planner_opt,emhass_tuned
    PYTHONPATH=. python -m tools.bench --report        # rebuild RESULTS.md from results.json

Seeds 1–3 are the reported (held-out) seeds; 101–102 are used for tuning the
competitors and for developing Heat Planner itself.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from multiprocessing import get_context
import os
from pathlib import Path
import statistics
import time
from typing import Any

from . import world as W
from .ours import Ours
from .report import write_markdown
from .sim import COMFORT_BAND, COMFORT_PENALTY, Case, Strategy, run
from .strategies import CheapestHours, Emhass, Fixed, NordpoolDiff, PriceAnalyzer, RoomMind, Sparegris

HERE = Path(__file__).parent
REPORT_SEEDS = (1, 2, 3)
TUNE_SEEDS = (101, 102)

GRIDS: dict[str, tuple[type[Strategy], list[dict[str, Any]]]] = {
    "priceanalyzer": (PriceAnalyzer, [
        {"multiply": m, "boost": b, "save": s} for m in (1.0, 2.0) for b in (2, 3) for s in (2, 3)
    ]),
    "nordpool_diff": (NordpoolDiff, [
        {"length": n, "gain": g, "limit": lim} for n in (6, 10, 16) for g in (1.5, 3.0, 5.0) for lim in (1.0, 2.0)
    ]),
    "cheapest_hours": (CheapestHours, [
        {"cheap_n": c, "exp_n": e, "delta": d} for c in (3, 6) for e in (2, 4, 6) for d in (1.0, 2.0)
    ]),
    "sparegris": (Sparegris, [
        {"max_wh": w, "low": lo, "high": hi, "extreme": ex}
        for w in (5000.0, 10000.0, "seasonal") for lo, hi, ex in ((1.0, -1.0, -1.0), (1.0, -1.0, -2.0), (2.0, -1.0, -2.0))
    ]),
    "emhass": (Emhass, [
        {"horizon": hz, **m} for hz in (24, 48) for m in (
            {"mode": "temp", "lag_aware": True, "bias": 0.5},
            {"mode": "temp", "lag_aware": True, "bias": 1.0},
            {"mode": "power"},
        )
    ]),
    "roommind": (RoomMind, [{"coast": c, "margin_h": m} for c in (False, True) for m in (0.25, 0.5)]),
}


def tuned_params() -> dict[str, dict[str, Any]]:
    path = HERE / "tuning.json"
    if not path.exists():
        return {}
    return {k: v["best"] for k, v in json.loads(path.read_text()).items()}


def strategies() -> dict[str, Any]:
    """Strategy key -> zero-argument factory (picklable via the key)."""
    tuned = tuned_params()
    out: dict[str, Any] = {"fixed_schedule": (Fixed, {})}
    for key, (cls, _) in GRIDS.items():
        out[key] = (cls, {})
        if key in tuned and {**cls.DEFAULTS, **tuned[key]} != cls.DEFAULTS:  # skip if tuning kept the defaults
            out[f"{key}_tuned"] = (cls, tuned[key])
    out["heat_planner_rules"] = (Ours, {"method": "rules"})
    out["heat_planner_opt"] = (Ours, {"method": "optimize"})
    out["heat_planner_opt_cap"] = (Ours, {"method": "optimize", "peak_limit": True})
    return out


def make(key: str, catalogue: dict[str, Any] | None = None) -> Strategy:
    cls, params = (catalogue or strategies())[key]
    s = cls(**params)
    s.name = key
    return s


def _job(args: tuple[str, Any, Case]) -> dict[str, Any]:
    key, spec, case = args
    cls, params = spec
    s = cls(**params)
    s.name = key
    r = run(s, case)
    r["strategy"] = key
    return r


def jobs_for(keys: list[str], seeds, seasons, contracts, days: int, catalogue) -> list[tuple[str, Any, Case]]:
    out = []
    for key in keys:
        cls, params = catalogue[key]
        blind = not cls(**params).price_aware
        for season in seasons:
            for seed in seeds:
                for contract in (("spot_support",) if blind else contracts):
                    out.append((key, catalogue[key], Case(season, seed, contract, days)))

    def weight(j) -> int:  # heavy first
        k, _, c = j
        w = 1
        if k.startswith("heat_planner_opt") and c.contract != "norgespris":
            w = 20
        elif k.startswith("heat_planner"):
            w = 6
        elif k.startswith("emhass"):
            w = 8
        return -w
    out.sort(key=weight)
    return out


def execute(jobs, procs: int) -> list[dict[str, Any]]:
    results = []
    t0 = time.time()
    if procs <= 1:
        it = map(_job, jobs)
    else:
        pool = get_context("fork").Pool(procs)
        it = pool.imap_unordered(_job, jobs)
    for i, r in enumerate(it, 1):
        results.append(r)
        if i % 20 == 0 or i == len(jobs):
            print(f"  {i}/{len(jobs)} runs, {time.time() - t0:.0f}s", flush=True)
    if procs > 1:
        pool.close()
    return results


def expand(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (strategy, season, seed, contract): price-blind runs are billed under every contract."""
    rows = []
    for r in results:
        for contract, b in r["bills"].items():
            row = {k: v for k, v in r.items() if k != "bills"}
            row["contract"] = contract
            row.update(b)
            rows.append(row)
    return rows


METRICS = ("cost", "value", "value_strict", "deficit", "deficit_band", "below18", "overheat", "max_cold_h", "heat_kwh", "capacity", "top3_kw",
           "writes_day", "energy", "grid")


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r["strategy"], r["season"], r["contract"])].append(r)
    out: dict[str, Any] = {}
    for (s, season, contract), rs in sorted(groups.items()):
        cell = {"n": len(rs), "seeds": sorted(r["seed"] for r in rs)}
        for m in METRICS:
            vals = [r[m] for r in rs]
            cell[m] = {"mean": round(statistics.fmean(vals), 2),
                       "sd": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0}
        out.setdefault(s, {}).setdefault(season, {})[contract] = cell
    return out


def tune(procs: int, days: int) -> None:
    catalogue_jobs = []
    for key, (cls, grid) in GRIDS.items():
        for i, params in enumerate(grid):
            spec = (cls, params)
            for season in W.SEASONS:
                for seed in TUNE_SEEDS:
                    catalogue_jobs.append((f"{key}#{i}", spec, Case(season, seed, "spot_support", days)))
    print(f"tuning: {len(catalogue_jobs)} runs")
    results = execute(catalogue_jobs, procs)
    scores: dict[str, list[float]] = defaultdict(list)
    for r in results:
        scores[r["strategy"]].append(r["bills"]["spot_support"]["value"])
    out = {}
    for key, (_, grid) in GRIDS.items():
        table = []
        for i, params in enumerate(grid):
            vals = scores[f"{key}#{i}"]
            table.append({"params": params, "value": round(statistics.fmean(vals), 2)})
        best = min(table, key=lambda x: x["value"])
        out[key] = {"best": best["params"], "objective": f"mean value score (cost + {COMFORT_PENALTY:g} kr/°C·h beyond {COMFORT_BAND:g} °C) "
                    f"over {len(W.SEASONS)} seasons × seeds {list(TUNE_SEEDS)}, spot + strømstøtte, {days} days",
                    "grid": table}
        print(f"{key}: best {best}")
    (HERE / "tuning.json").write_text(json.dumps(out, indent=1, default=str))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="1 seed, 4 days")
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--days", type=int, default=W.DAYS)
    ap.add_argument("--only", default="", help="strategy keys")
    ap.add_argument("--seasons", default=",".join(W.SEASONS))
    ap.add_argument("--contracts", default=",".join(W.CONTRACTS))
    ap.add_argument("--procs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--report", action="store_true", help="only rebuild RESULTS.md from results.json")
    args = ap.parse_args()
    if args.report:
        data = json.loads((Path(args.out) / "results.json").read_text())
        write_markdown(data, Path(args.out) / "RESULTS.md")
        return
    if args.tune:
        tune(args.procs, args.days)
        return
    seeds = tuple(int(s) for s in args.seeds.split(",") if s) or ((1,) if args.quick else REPORT_SEEDS)
    days = 4 if args.quick else args.days
    catalogue = strategies()
    keys = [k for k in catalogue if not args.only or k in args.only.split(",")]
    jobs = jobs_for(keys, seeds, args.seasons.split(","), args.contracts.split(","), days, catalogue)
    print(f"{len(jobs)} runs on {args.procs} processes")
    t0 = time.time()
    results = execute(jobs, args.procs)
    rows = expand(results)
    summary = summarise(rows)
    meta = {
        "seeds": list(seeds), "days": days, "seasons": {k: W.SEASONS[k].label for k in args.seasons.split(",")},
        "contracts": {c: W.CONTRACT_LABEL[c] for c in args.contracts.split(",")},
        "comfort_penalty_kr_per_degree_hour": COMFORT_PENALTY, "comfort_band": COMFORT_BAND,
        "strategies": {k: {"class": catalogue[k][0].__name__, "params": catalogue[k][1]} for k in keys},
        "wall_s": round(time.time() - t0, 1), "cpu_s": round(sum(r["cpu_s"] for r in results), 1),
        "procs": args.procs,
    }
    print(f"done in {meta['wall_s']} s ({meta['cpu_s']} CPU s)")
    data = {"meta": meta, "summary": summary, "runs": sorted(rows, key=lambda r: (r["strategy"], r["season"],
                                                                                   r["contract"], r["seed"]))}
    if args.no_write:
        from .report import console
        console(data)
        return
    out = Path(args.out)
    (out / "results.json").write_text(json.dumps(data, indent=1, default=str))
    write_markdown(data, out / "RESULTS.md")
    from .report import console
    console(data)


if __name__ == "__main__":
    main()
