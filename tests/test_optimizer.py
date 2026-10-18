"""Tests for the DP optimiser and the honest-savings fields of the plan."""

import time
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.heat_planner.optimizer import (
    R_OPTIMIZED,
    OptimizerWeights,
    optimize_zone,
)
from custom_components.heat_planner.planner import (
    LEVEL_EXTREME,
    R_PREHEAT,
    R_PREHEAT_EXTREME,
    Plan,
    PriceRules,
    ThermalModel,
    ZoneConfig,
    build_plan,
    default_schedule,
    _cap_off_runs,
    _simulate,
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


def _deltas(z, out):
    return [h.target - h.scheduled for h in out if h.target is not None]


def test_price_shift_and_preheat_bound_the_actions():
    p = prices(spike(1.0, 6.0))
    cheap_comfort = OptimizerWeights(comfort=0.01)
    for shift, pre, agg in ((1.0, 1.0, 1.0), (0.5, 0.5, 1.0), (1.0, 1.5, 2.0), (2.0, 0.25, 0.5)):
        z = zone(price_shift=shift, preheat=pre, price_aggressiveness=agg, extreme_off=False,
                 safety_min_temp=5.0)
        out = plan(z, p, optimizer_weights=cheap_comfort).hours
        d = _deltas(z, out)
        assert min(d) >= -shift * agg - 1e-9, (shift, pre, agg, d)
        assert max(d) <= 2 * pre * min(agg, 1.0) + 1e-9, (shift, pre, agg, d)
        assert all(abs(h.target * 2 - round(h.target * 2)) < 1e-9 for h in out)  # 0.5 °C grid
    # the bound itself is reachable when it pays (comfort almost free)
    z = zone(price_shift=1.5, extreme_off=False, safety_min_temp=5.0)
    assert min(_deltas(z, plan(z, p, optimizer_weights=cheap_comfort).hours)) == -1.5


def test_zero_price_shift_never_lowers_zero_preheat_never_raises():
    p = prices(spike(1.0, 6.0))
    w = OptimizerWeights(comfort=0.01)
    z = zone(price_shift=0.0, extreme_off=False)
    assert min(_deltas(z, plan(z, p, optimizer_weights=w).hours)) == 0.0
    z = zone(preheat=0.0)
    out = plan(z, p).hours
    assert max(_deltas(z, out)) == 0.0
    assert not any(R_PREHEAT in h.reasons or R_PREHEAT_EXTREME in h.reasons for h in out)


def test_percent_zone_bounds_in_points():
    z = zone(mode="percent", comfort=0.0, min_value=0.0, max_value=100.0, away_offset=-20.0,
             price_shift=15.0, preheat=10.0, extreme_off=False)
    out = plan(z, prices(spike(1.0, 6.0)), schedule=default_schedule("percent"),
               optimizer_weights=OptimizerWeights(comfort=0.01)).hours
    d = _deltas(z, out)
    assert min(d) >= -15.0 and max(d) <= 20.0
    assert min(d) < 0  # it does lower
    assert all(h.target % 5 == 0 for h in out)


def test_lag_moves_preheat_earlier_and_keeps_expensive_hours_warm():
    # evening comfort (21 °C) starts at 16:00, the price spike is 17–19
    sched = {wd: [-2.0] * 16 + [0.0] * 7 + [-2.0] for wd in range(7)}
    p = prices(spike(1.0, 6.0))

    def first_raise(pl):
        return next(h.start.hour for h in pl.hours if h.target is not None
                    and h.target > h.scheduled)

    z0 = zone(thermal=ThermalModel(gross_rate=3.0, tau_hours=30.0))
    z2 = zone(thermal=ThermalModel(gross_rate=3.0, tau_hours=30.0, lag_hours=2.0))
    p0 = plan(z0, p, room=19.0, schedule=sched)
    p2 = plan(z2, p, room=19.0, schedule=sched)
    assert first_raise(p2) < first_raise(p0)
    # the room entering and during the expensive hours (predicted = end of hour)
    for h in p2.hours[16:20]:
        assert h.predicted >= h.scheduled - 0.25, h
    # ignoring the lag (lag-0 targets on the slow floor) would arrive too late
    naive = [replace(h) for h in p0.hours]
    _simulate(z2, naive, 19.0, {}, -5.0)
    assert min(h.predicted - h.scheduled for h in naive[16:20]) < -1.0


def test_runtime_with_lag():
    z = zone(thermal=ThermalModel(gross_rate=2.0, tau_hours=30.0, lag_hours=3.0),
             price_shift=2.0, preheat=1.5)
    p = prices(TYPICAL)
    rule_slots = plan(z, p, hours=36, method="rules", schedule=default_schedule("setpoint")).hours
    t = time.perf_counter()
    for _ in range(5):
        optimize_zone(z, rule_slots, p, 19.0, {}, -5.0)
    assert (time.perf_counter() - t) / 5 < 0.25


def test_off_only_in_extreme_hours():
    # expensive but not extreme (below extreme_min_price): no OFF even if comfort is free
    z = zone(max_off_hours=3, thermal=ThermalModel(gross_rate=4.0, tau_hours=10.0),
             safety_min_temp=5.0)
    p = prices([0.5] * 6 + [1.9] * 12 + [0.5] * 6)
    out = plan(z, p, optimizer_weights=OptimizerWeights(comfort=0.001, terminal=0.0)).hours
    assert all(h.level != LEVEL_EXTREME for h in out)
    assert all(h.target is not None for h in out)
    # explicitly allowed by the weights
    w = OptimizerWeights(comfort=0.001, terminal=0.0, off_levels=("expensive", "extreme"))
    assert any(h.target is None for h in plan(z, p, optimizer_weights=w).hours)


def test_initial_off_run_counts_toward_max_off():
    z = zone(max_off_hours=2, thermal=ThermalModel(gross_rate=4.0, tau_hours=10.0),
             safety_min_temp=5.0)
    p = prices([9.0] * 2 + [1.0] * 22)
    w = OptimizerWeights(comfort=0.001, terminal=0.0)
    fresh = plan(z, p, optimizer_weights=w).hours
    assert fresh[0].target is None and fresh[1].target is None
    one = plan(z, p, optimizer_weights=w, initial_off_run=1).hours
    assert not (one[0].target is None and one[1].target is None)
    two = plan(z, p, optimizer_weights=w, initial_off_run=2).hours
    assert two[0].target is not None
    # rules path: off, off at the start; already off for 2 h -> first hour heats
    assert [h.target is None for h in plan(z, p, method="rules").hours[:3]] == [
        True, True, False]
    one_r = plan(z, p, method="rules", initial_off_run=1).hours
    assert [h.target is None for h in one_r[:2]] == [True, False]
    rules = plan(z, p, method="rules", initial_off_run=2).hours
    assert [h.mode for h in rules[:2]] == ["save", "save"]


def test_lowered_setpoints_never_below_safety_floor():
    z = zone(safety_min_temp=20.5, price_shift=3.0, price_aggressiveness=2.0,
             thermal=ThermalModel(gross_rate=4.0, tau_hours=10.0))
    p = prices([1.0] * 6 + [8.0] * 12 + [1.0] * 6)
    out = plan(z, p, optimizer_weights=OptimizerWeights(comfort=0.001)).hours
    lowered = [h for h in out if h.target is not None and h.target < h.scheduled]
    assert lowered
    assert all(h.target >= 20.5 for h in lowered)
    # a schedule already below the floor is kept, never lowered further
    sched = {wd: [-3.0] * 24 for wd in range(7)}  # 18 °C
    out = plan(z, p, schedule=sched, room=18.0,
               optimizer_weights=OptimizerWeights(comfort=0.001)).hours
    assert all(h.target is None or h.target >= h.scheduled for h in out)


def test_cap_off_runs_respects_safety_floor():
    z = zone(max_off_hours=1, safety_min_temp=20.5, price_shift=3.0)
    slots = plan(z, prices([9.0] * 4 + [1.0] * 20), method="rules").hours
    for s in slots[:4]:
        s.target = None
    _cap_off_runs(z, slots, initial_off_run=1)
    assert [s.target for s in slots[:4]] == [20.5] * 4


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
