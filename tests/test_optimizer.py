"""Tests for the DP optimiser and the honest-savings fields of the plan."""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.heat_planner.optimizer import (
    R_OPTIMIZED,
    OptimizerWeights,
    optimize_zone,
)
from custom_components.heat_planner.planner import (
    R_PREHEAT,
    R_PREHEAT_EXTREME,
    Plan,
    PriceRules,
    ThermalModel,
    ZoneConfig,
    build_plan,
    default_schedule,
    explain_savings,
)

TZ = ZoneInfo("Europe/Oslo")
DAY = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)  # a Monday
FLAT_SCHEDULE = {wd: [0.0] * 24 for wd in range(7)}
TYPICAL = [1.2, 1.2, 1.1, 1.1, 1.1, 1.2, 1.4, 1.6, 1.8, 1.5, 1.4, 1.3,
           1.1, 1.0, 1.0, 1.2, 1.5, 1.7, 1.9, 1.9, 1.6, 1.5, 1.4, 1.3]


def prices(values: list[float], hours: int = 48) -> dict[datetime, float]:
    return {DAY + timedelta(hours=i): values[i % len(values)] for i in range(hours)}


def zone(**kw) -> ZoneConfig:
    kw.setdefault("thermal", ThermalModel(gross_rate=2.0, tau_hours=30.0))
    kw.setdefault("protected_from_hour", 0)
    kw.setdefault("protected_to_hour", 0)
    kw.setdefault("comfort", 21.0)
    return ZoneConfig(zone_id="stue", name="Stue", **kw)


def plan(z, p, *, hours=24, room=21.0, method="optimize", schedule=None, **kw):
    return build_plan(
        z, schedule or FLAT_SCHEDULE, {}, p, PriceRules(), DAY, hours,
        room_temp=room, outdoor_default=-5.0, method=method, **kw,
    )


def spike(base: float, peak: float) -> list[float]:
    vals = [base] * 24
    for h in (17, 18, 19):
        vals[h] = peak
    return vals


# --------------------------------------------------------------------------- optimiser


def test_preheats_before_big_spike_and_it_pays_off():
    p = plan(zone(), prices(spike(1.0, 6.0)))
    before = p.hours[12:17]
    assert any(h.target is not None and h.target > h.scheduled for h in before)
    assert any(R_PREHEAT in h.reasons or R_PREHEAT_EXTREME in h.reasons for h in before)
    assert any(R_OPTIMIZED in h.reasons for h in p.hours)
    assert p.method == "optimize"
    # pre-heat energy is counted, and it still saves money
    assert p.savings is not None and p.savings > 0.5
    rules = plan(zone(), prices(spike(1.0, 6.0)), method="rules")
    assert p.cost <= rules.cost + 1e-6


def test_no_preheat_when_spike_is_small():
    p = plan(zone(), prices(spike(1.0, 1.1)))
    assert all(h.target is None or h.target <= h.scheduled for h in p.hours[:17])
    assert abs(p.savings) < 0.3
    assert explain_savings(p, "nb") == "Prisstyring sparer nesten ingenting i dag"


def test_safety_floor_holds_when_heating_is_cut():
    # comfort almost free: only the safety floor stops it from cutting deeper
    z = zone(safety_min_temp=19.5, max_off_hours=3,
             thermal=ThermalModel(gross_rate=2.0, tau_hours=20.0))
    p = plan(z, prices([1.0] * 6 + [8.0] * 12 + [1.0] * 6),
             optimizer_weights=OptimizerWeights(comfort=0.001))
    cut = [h for h in p.hours if h.target is None or h.target < h.scheduled]
    assert cut
    for h in cut:
        assert h.predicted >= 19.5 - 0.1, h
    assert min(h.predicted for h in p.hours) >= 19.5 - 0.1


def test_max_off_run_respected():
    # comfort nearly free and a leaky room: switching off beats any lower setpoint
    z = zone(max_off_hours=1, thermal=ThermalModel(gross_rate=4.0, tau_hours=10.0),
             safety_min_temp=5.0)
    p = prices([1.0] * 4 + [9.0] * 16 + [1.0] * 4)
    slots = plan(z, p, method="rules").hours
    out = optimize_zone(z, slots, p, 21.0, {}, -5.0,
                        weights=OptimizerWeights(comfort=0.001, terminal=0.0))
    offs = [h.target is None for h in out]
    assert sum(offs) >= 3
    assert not any(a and b for a, b in zip(offs, offs[1:]))
    assert all(h.mode == "off" for h in out if h.target is None)


def test_no_off_when_extreme_off_disabled():
    z = zone(extreme_off=False, thermal=ThermalModel(gross_rate=2.0, tau_hours=150.0))
    p = plan(z, prices([1.0] * 4 + [9.0] * 16 + [1.0] * 4))
    assert all(h.target is not None for h in p.hours)


def test_protected_window_is_never_lowered():
    z = zone(protected_from_hour=16, protected_to_hour=20)
    p = plan(z, prices(spike(1.0, 6.0)))
    for h in p.hours:
        if 16 <= h.start.hour < 20:
            assert h.target is not None and h.target >= h.scheduled


def test_aggressiveness_zero_returns_schedule():
    z = zone(price_aggressiveness=0.0)
    p = plan(z, prices(spike(1.0, 6.0)))
    assert [h.target for h in p.hours] == [h.scheduled for h in p.hours]
    slots = plan(z, prices(spike(1.0, 6.0)), method="rules").hours
    assert optimize_zone(z, slots, prices(spike(1.0, 6.0)), 21.0, {}, -5.0) is slots


def test_unknown_room_temperature_falls_back_to_rules():
    z = zone()
    p = prices(TYPICAL)
    a = plan(z, p, room=None, method="rules", schedule=default_schedule("setpoint"))
    b = plan(z, p, room=None, method="optimize", schedule=default_schedule("setpoint"))
    assert [h.as_dict() for h in a.hours] == [h.as_dict() for h in b.hours]
    assert b.method == "rules"


def test_peak_cap_respected_when_possible():
    sched = {wd: [-2.0] * 10 + [1.0] * 14 for wd in range(7)}  # step up at 10:00
    z = zone(heater_kw=2.0, thermal=ThermalModel(gross_rate=1.5, tau_hours=40.0))
    p = prices([1.0] * 24)
    free = plan(z, p, room=19.0, schedule=sched)
    cap = {DAY + timedelta(hours=h): 0.8 for h in (10, 11, 12)}
    capped = plan(z, p, room=19.0, schedule=sched, kw_cap=cap)
    peak_free = max(h.kwh for h in free.hours[10:13])
    peak_capped = max(h.kwh for h in capped.hours[10:13])
    assert peak_free > 0.8
    assert peak_capped <= 0.8 + 0.15
    # it got there by heating earlier
    assert any(h.target > h.scheduled for h in capped.hours[6:10])


def test_runtime_bound():
    z = zone()
    p = prices(TYPICAL)
    rule_slots = plan(z, p, hours=36, method="rules", schedule=default_schedule("setpoint")).hours
    t = time.perf_counter()
    for _ in range(5):
        optimize_zone(z, rule_slots, p, 19.0, {}, -5.0)
    per_zone = (time.perf_counter() - t) / 5
    assert per_zone < 0.25  # ~20–40 ms on a laptop; generous for slow CI


def test_percent_zone_optimises_within_bounds():
    z = zone(mode="percent", comfort=0.0, min_value=0.0, max_value=100.0, away_offset=-20.0,
             thermal=ThermalModel(gross_rate=2.0, tau_hours=30.0))
    p = plan(z, prices(spike(1.0, 6.0)), schedule=default_schedule("percent"))
    for h in p.hours:
        assert h.target is None or 0 <= h.target <= 100
    assert p.cost <= p.baseline_cost + 1e-6
    assert p.comfort_deficit is None  # no temperature reference for percent zones


# --------------------------------------------------------------------------- rules path


def test_rules_path_unchanged_by_new_keywords():
    z = ZoneConfig(zone_id="stue", name="Stue")
    p = prices(TYPICAL)
    a = build_plan(z, default_schedule("setpoint"), {}, p, PriceRules(), DAY, 36,
                   room_temp=20.0, outdoor_default=0.0)
    b = build_plan(z, default_schedule("setpoint"), {}, p, PriceRules(), DAY, 36,
                   room_temp=20.0, outdoor_default=0.0, method="rules",
                   kw_cap={DAY: 0.1})
    assert [h.as_dict() for h in a.hours] == [h.as_dict() for h in b.hours]
    assert (a.cost, a.baseline_cost, a.kwh, a.baseline_kwh) == (
        b.cost, b.baseline_cost, b.kwh, b.baseline_kwh)


# --------------------------------------------------------------------------- honest savings


def test_plan_fields_and_export():
    z = zone()
    p = plan(z, prices(spike(1.0, 6.0)), method="rules")
    assert p.extra_kwh == round(p.kwh - p.baseline_kwh, 3)
    assert p.comfort_deficit is not None and p.baseline_comfort_deficit is not None
    assert p.savings_pct == round(100 * p.savings / p.baseline_cost, 1)
    d = p.as_dict()
    for key in ("extra_kwh", "comfort_deficit", "baseline_comfort_deficit",
                "savings_pct", "method"):
        assert key in d
    # baseline and plan start from the same temperature with the same model
    assert p.hours[0].start == DAY
    none = plan(z, prices(spike(1.0, 6.0)), room=None, method="rules")
    assert none.comfort_deficit is None and none.baseline_comfort_deficit is None


def test_comfort_deficit_counts_cold_hours():
    z = zone(thermal=ThermalModel(gross_rate=0.5, tau_hours=30.0))
    p = plan(z, {}, room=18.0, method="rules")
    # starts 3 °C below the 21 °C schedule and warms slowly
    assert p.comfort_deficit > 3.0
    assert p.comfort_deficit == p.baseline_comfort_deficit


def _plan(cost, bcost, kwh, bkwh, cd=None, bcd=None):
    return Plan("z", [], cost, bcost, kwh, bkwh, comfort_deficit=cd, baseline_comfort_deficit=bcd)


def test_explain_savings_texts():
    assert explain_savings(_plan(20.0, 23.1, 10.8, 10.0), "nb") == (
        "Sparer ca. 3,10 kr, men bruker 0,8 kWh mer")
    assert explain_savings(_plan(20.0, 23.1, 10.8, 10.0), "en") == (
        "Saves about 3.10, but uses 0.8 kWh more")
    assert explain_savings(_plan(22.9, 23.0, 10.0, 10.0), "nb") == (
        "Prisstyring sparer nesten ingenting i dag")
    assert explain_savings(_plan(22.9, 23.0, 10.0, 10.0), "en") == (
        "Price control saves almost nothing today")
    assert explain_savings(_plan(24.0, 23.0, 11.0, 10.0), "nb") == (
        "Prisstyring koster ca. 1,00 kr mer i dag")
    assert explain_savings(_plan(None, None, 1.0, 1.0), "en").startswith("No prices")
    txt = explain_savings(_plan(18.0, 23.0, 9.0, 10.0, cd=4.0, bcd=1.0), "nb")
    assert txt == "Sparer ca. 5,00 kr, og bruker 1,0 kWh mindre, og rommet er 3,0 gradtimer kaldere"
