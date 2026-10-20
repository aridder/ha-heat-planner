"""Build a realistic *example* card export for the README screenshots.

Runs the integration's own pure modules (planner, optimiser, tariff, peak,
occupancy, solar) on made-up example data, so the screenshots show what the
code really produces. Nothing here talks to Home Assistant.

    python3 tools/docs/make_mock.py [en|nb] [spot|spot_support|fixed] > mock.json   # done by shoot.py
"""

from __future__ import annotations

import importlib.util
import json
import math
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


def _base_kw(hour: int) -> float:  # the rest of the house: mornings and evenings are busy
    return 0.9 + (2.6 if hour in (7, 8, 17, 18, 19) else 0.8 if 9 <= hour <= 22 else 0.0)


# House load for the plan horizon (like Coordinator._load_export): base load + planned heating,
# the current hour projected from the meter.
_planned: dict[str, float] = {}
for _z in zones_out:
    for _h in _z["plan"]["hours"]:
        _planned[_h["start"]] = _planned.get(_h["start"], 0.0) + _h["kwh"]
peak_state["load"] = [
    {"start": k, "kw": round(_base_kw(datetime.fromisoformat(k).hour) + v, 2), **({"live": True} if k == start.isoformat() else {})}
    for k, v in sorted(_planned.items())
]

levels = planner.classify_prices(prices, rules)

# --------------------------------------------------------------------------- look-back (heat_planner/history)
# The last 48 hours, planned by the same code from Saturday 10:00, with "measured" room
# temperatures a little off the prediction, one manual change, a capacity set-back and an off hour.
past_spot = dict(spot)
for d, (src, f) in ((-2, (1, 1.05)), (-1, (0, 0.97))):
    for h in range(24):
        past_spot[(day0 + timedelta(days=d)).replace(hour=h)] = round(SPOT[src][h] * f, 3)
past_prices = tariff.effective_prices(past_spot, TARIFF)
past_levels = planner.classify_prices(past_prices, rules)
past_outdoor = {k: OUTDOOR[k.hour] + 1.0 for k in past_spot}
past_sol = {k: round(solar.solar_factor(k, LAT, LON, CLOUD[k.hour]), 3) for k in past_spot}
HSTART = start - timedelta(hours=48)
yesterday = (day0 - timedelta(days=1)).date()


def _at(h):
    return (day0 - timedelta(days=1)).replace(hour=h)


hist_zones = []
house = {}
for (spec, zc), zout in zip(configs, zones_out):
    plan = planner.build_plan(
        zc, planner.default_schedule(zc.mode), {}, past_prices, rules, HSTART, 49, room_temp=20.2 if not zc.is_percent else None,
        outdoor=past_outdoor, outdoor_default=0.0, method="optimize", solar=past_sol,
    )
    rows, prev = [], 20.2
    for i, h in enumerate(plan.hours):
        partial = i == len(plan.hours) - 1
        flags, mode, target, reasons = [], h.mode, h.target, list(h.reasons)
        if zc.zone_id == "living_room" and h.start in (_at(17), _at(18)):
            flags, target, reasons = ["shed"], (target or 21) - 1.0, reasons + ["peak_limit"]
        if zc.zone_id == "living_room" and h.start == _at(20):
            flags, mode, target, reasons = ["manual", "paused"], "manual", 23.0, []
        if zc.zone_id == "living_room" and h.start == _at(21):
            flags, mode, target, reasons = ["paused"], "manual", 23.0, []
        if zc.zone_id == "kitchen" and h.start == _at(18):
            flags, mode, target, reasons = ["off"], "off", None, ["extreme_off"]
        temp = None
        if not zc.is_percent:
            pred = h.predicted if h.predicted is not None else prev
            temp = (prev + pred) / 2 + 0.16 * math.sin(i * 2.1) + 0.09 * math.sin(i * 0.7)
            if mode == "manual":
                temp += 0.5 if "manual" in flags else 0.9
            prev = pred
            if partial:
                temp = spec["room"]
        heating = None
        if zc.is_percent and target is not None:
            heating = max(0.0, min(1.0, target / 100 + 0.04 * math.sin(i * 1.3)))
        kwh = 0.0 if target is None else round(h.kwh * (1.3 if mode == "manual" else 1.0), 3)
        price = round(past_prices.get(h.start, 0.0), 4)
        rows.append({
            "start": h.start.isoformat(), "target": target, "mode": mode, "reasons": reasons, "price": price,
            "level": past_levels.get(h.start), "predicted": None if zc.is_percent else h.predicted,
            "temp": None if temp is None else round(temp, 2), "heating": None if heating is None else round(heating, 3),
            "flags": flags, "kwh": kwh, "cost": round(kwh * price, 4), "src": "live", "partial": partial,
        })
        house[h.start] = house.get(h.start, 0.0) + kwh
    hist_zones.append({
        "zone_id": zc.zone_id, "name": zc.name, "mode": zc.mode, "has_temperature": not zc.is_percent,
        "heater_kw": zc.heater_kw, "hours": rows,
    })
home_rows = []
for k in sorted(house):
    base = _base_kw(k.hour)
    home_rows.append({"start": k.isoformat(), "house_kwh": round(base + house[k], 2), "cap_kw": None, "live": k == start})
history = {
    "entry_id": "example", "hours": 48, "now": NOW.isoformat(), "start": HSTART.isoformat(),
    "end": (start + timedelta(hours=1)).isoformat(), "currency": "NOK", "price_unit": "NOK/kWh", "metered": True,
    "zones": hist_zones, "home": home_rows,
}

for r in home_rows:
    r["cap_kw"] = round(peak_state["cap_kw"], 2)
export = {
    "entry_id": "example", "title": "Home", "currency": "NOK", "price_unit": "NOK/kWh",
    "price_source": "Nord Pool NO1", "price_extra": 0.0, "price_extra_configured": 0.0,
    "rules": asdict(rules), "optimize": True, "features": ["feedback", "history_log"],
    "arrival": {"expected": ARRIVAL_EXPECTED.strftime("%H:%M"), "count": 6, "source": "weekday",
                "weekday": NOW.weekday(), "warm_from": warm_from},
    "tariff": {"mode": TARIFF.mode, "label": tariff.describe(TARIFF, LANG), **TARIFF.as_dict()},
    "peak": peak_state, "away": False, "history": True, "house": None, "outdoor_now": 0.3,
    "prices": [{"start": h.isoformat(), "price": round(p, 4), "level": levels.get(h)} for h, p in prices.items()],
    "zones": zones_out, "updated": NOW.isoformat(),
}
json.dump({"now": NOW.isoformat(), "entries": [export], "history": history}, sys.stdout, ensure_ascii=False, indent=1)
