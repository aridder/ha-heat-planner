"""Build a realistic *example* card export for the README screenshots.

Runs the integration's own pure modules (planner, optimiser, tariff, peak,
occupancy, solar) on made-up example data, so the screenshots show what the
code really produces. Nothing here talks to Home Assistant.

    python3 tools/docs/make_mock.py [en|nb] [spot|spot_support|fixed] > mock.json   # done by shoot.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from dataclasses import asdict
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "custom_components" / "heat_planner"

# Load the pure modules without the package __init__ (which needs Home Assistant).
_pkg = types.ModuleType("hp")
_pkg.__path__ = [str(PKG)]
sys.modules["hp"] = _pkg


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"hp.{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"hp.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


planner = _load("planner")
_load("optimizer")
tariff = _load("tariff")
peak = _load("peak")
occupancy = _load("occupancy")
solar = _load("solar")

TZ = ZoneInfo("Europe/Oslo")
# Example moment: Monday 16 November 2026, 10:20 in Oslo.
NOW = datetime(2026, 11, 16, 10, 20, tzinfo=TZ)
LAT, LON = 59.91, 10.75

# Example Nord Pool NO1 spot prices, NOK/kWh ex VAT (a cold, windless November week).
SPOT = {
    0: [0.62, 0.58, 0.55, 0.54, 0.57, 0.71, 1.12, 1.58, 1.74, 1.46, 1.21, 1.08,
        1.01, 0.98, 1.04, 1.22, 1.63, 2.05, 2.31, 1.86, 1.42, 1.15, 0.92, 0.74],
    1: [0.66, 0.61, 0.59, 0.58, 0.60, 0.74, 1.05, 1.41, 1.52, 1.31, 1.12, 1.02,
        0.97, 0.95, 0.99, 1.13, 1.44, 1.78, 1.92, 1.61, 1.27, 1.04, 0.86, 0.71],
}
CLOUD = {h: (85 if h < 11 or h > 14 else 40) for h in range(24)}  # a few sunny midday hours
OUTDOOR = [-3.0, -3.4, -3.8, -4.0, -4.1, -4.0, -3.6, -3.0, -2.2, -1.2, -0.4, 0.3,
           0.8, 1.0, 0.7, 0.0, -0.8, -1.5, -2.0, -2.4, -2.7, -2.9, -3.1, -3.3]

LANG = sys.argv[1] if len(sys.argv) > 1 else "en"

# The contract (spot / spot_support / fixed) + the Elvia 2026 grid tariff.
CONTRACT = sys.argv[2] if len(sys.argv) > 2 else "spot"
TARIFF = tariff.TariffConfig.from_options({"mode": CONTRACT, "preset": "elvia_2026"})

start = NOW.replace(minute=0)
day0 = start.replace(hour=0)
spot: dict[datetime, float] = {}
for d in (0, 1):
    for h in range(24):
        spot[(day0 + timedelta(days=d)).replace(hour=h)] = SPOT[d][h]
prices = tariff.effective_prices(spot, TARIFF)
outdoor = {k: OUTDOOR[k.hour] for k in spot}
sol = {k: round(solar.solar_factor(k, LAT, LON, CLOUD[k.hour]), 3) for k in spot}
rules = planner.PriceRules()

# Arrivals learned from presence history (Mondays): an early estimate of 15:20.
ARRIVAL_EXPECTED = time(15, 20)

ZONES = [
    dict(zone_id="living_room", name="Living room", heater_kw=1.6, src="measured", room=20.6,
         thermal=planner.ThermalModel(gross_rate=1.3, tau_hours=42, learned=True, samples=1240,
                                      solar_gain=0.45, lag_hours=1.0, rmse=0.22),
         settings=dict(comfort=21.0, away_until_hour=occupancy.AUTO_HOUR)),
    dict(zone_id="kitchen", name="Kitchen", heater_kw=0.9, src="measured", room=21.2,
         thermal=planner.ThermalModel(gross_rate=1.6, tau_hours=30, learned=True, samples=1180,
                                      solar_gain=0.2, lag_hours=0.5, rmse=0.41),
         settings=dict(comfort=21.5, away_until_hour=occupancy.AUTO_HOUR)),
    dict(zone_id="bathroom", name="Bathroom", heater_kw=0.6, src="estimate", room=None, mode="percent",
         thermal=planner.ThermalModel(), settings=dict(away_until_hour=occupancy.AUTO_HOUR)),
]


def lead_hours(z) -> float:  # same as Coordinator.lead_hours
    if z.is_percent:
        return 2.0
    net = max(z.thermal.gross_rate - 0.3, 0.2)
    return max(0.5, min(6.0, abs(z.away_offset) / net + z.thermal.lag_hours))


configs = []
for spec in ZONES:
    s = dict(spec["settings"])
    zc = planner.ZoneConfig(zone_id=spec["zone_id"], name=spec["name"], mode=spec.get("mode", "setpoint"),
                            heater_kw=spec["heater_kw"], thermal=spec["thermal"],
                            **{k: v for k, v in s.items() if k != "away_until_hour"})
    if zc.is_percent:
        zc.min_value, zc.max_value, zc.price_shift, zc.preheat, zc.away_offset = 0, 100, 20, 10, -20
    configs.append((spec, zc))

lead = max(lead_hours(z) for _, z in configs if not z.is_percent)
warm_from = occupancy.warm_from_hour(ARRIVAL_EXPECTED, lead)

zones_out = []
for spec, zc in configs:
    zc.away_until_hour = warm_from  # "Auto": learned from arrivals
    sched = planner.default_schedule(zc.mode)
    plan = planner.build_plan(
        zc, sched, {}, prices, rules, NOW, 36, away_now=False, room_temp=spec["room"],
        outdoor=outdoor, outdoor_default=0.0, method="optimize", solar=sol,
    )
    th = asdict(zc.thermal)
    q = planner.model_quality(zc.thermal)
    th["quality"] = {"poor": "uncertain", "unknown": None}.get(q, q)
    settings = {k: getattr(zc, k) for k in (
        "comfort", "min_value", "max_value", "away_offset", "away_until_hour", "protected_from_hour",
        "protected_to_hour", "price_shift", "preheat", "extreme_off", "safety_min_temp", "heater_kw",
        "price_aggressiveness", "max_off_hours")}
    settings["away_until_hour"] = occupancy.AUTO_HOUR
    zones_out.append({
        "zone_id": zc.zone_id, "name": zc.name, "mode": zc.mode,
        "climate_entity": f"climate.{zc.zone_id}", "number_entity": None,
        "settings": settings, "thermal": th, "heater_kw": zc.heater_kw, "heater_kw_source": spec["src"],
        "schedule": {str(k): v for k, v in sched.items()}, "overrides": {}, "auto": True,
        "manual_until": None, "room_temperature": spec["room"],
        "plan": {**plan.as_dict(), "explanation": planner.explain_savings(plan, LANG, "kr")},
        "feedback": [],
    })

# Capacity tariff: whole-house meter history for 1–15 November (example daily peaks, kW).
daily = [5.1, 4.6, 6.2, 5.8, 4.9, 5.5, 6.8, 6.1, 4.4, 5.2, 5.7, 4.8, 5.3, 6.4, 5.9]
hourly = {datetime(2026, 11, i + 1, 18, tzinfo=TZ): v for i, v in enumerate(daily)}
hourly[datetime(2026, 11, 16, 8, tzinfo=TZ)] = 4.3  # today so far
cfg = peak.PeakConfig()
peaks = peak.monthly_peaks({k: v for k, v in hourly.items() if k.date() != NOW.date()})
hours = sorted(prices)
caps = peak.caps_for_plan(hours, peaks, 4.3, NOW, cfg)
avg = peak.top3_average(peaks)
step = cfg.step_for(avg)


def _f(v):
    return f"{v:g}".replace(".", ",")


peak_state = {
    "cap_kw": caps.get(start), "top3_kw": sorted((v for _, v in peaks), reverse=True)[:3],
    "month_avg_kw": round(avg, 2), "today_kw": 4.3, "step_label": f"{_f(step.lower_kw)}–{_f(step.upper_kw)} kW",
    "step_cost": step.cost, "next_step_kw": step.upper_kw, "metered": True, "shed": [],
}

levels = planner.classify_prices(prices, rules)
export = {
    "entry_id": "example", "title": "Home", "currency": "NOK", "price_unit": "NOK/kWh",
    "price_source": "Nord Pool NO1", "price_extra": 0.0, "price_extra_configured": 0.0,
    "rules": asdict(rules), "optimize": True, "features": ["feedback"],
    "arrival": {"expected": ARRIVAL_EXPECTED.strftime("%H:%M"), "count": 6, "source": "weekday",
                "weekday": NOW.weekday(), "warm_from": warm_from},
    "tariff": {"mode": TARIFF.mode, "label": tariff.describe(TARIFF, LANG), **TARIFF.as_dict()},
    "peak": peak_state, "away": False, "history": True, "house": None, "outdoor_now": 0.3,
    "prices": [{"start": h.isoformat(), "price": round(p, 4), "level": levels.get(h)} for h, p in prices.items()],
    "zones": zones_out, "updated": NOW.isoformat(),
}
json.dump({"now": NOW.isoformat(), "entries": [export]}, sys.stdout, ensure_ascii=False, indent=1)
