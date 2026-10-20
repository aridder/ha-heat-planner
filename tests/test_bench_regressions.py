"""Regressions for the engine changes made after the strategy benchmark (tools/bench).

* Percent zones (regulators): price shifting moves heat instead of removing it (the
  bathroom ended twice as far below its comfort as with a plain schedule).
* The benchmark itself runs and is reproducible.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.heat_planner.control import live_shed, plan_peaks
from custom_components.heat_planner.optimizer import R_OPTIMIZED
from custom_components.heat_planner.peak import PeakConfig, choose_limit
from custom_components.heat_planner.planner import (
    MODE_PERCENT,
    R_EXPENSIVE,
    R_PEAK,
    PriceRules,
    ThermalModel,
    ZoneConfig,
    build_plan,
    default_schedule,
)

TZ = ZoneInfo("Europe/Oslo")
DAY = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)
TYPICAL = [1.2, 1.2, 1.1, 1.1, 1.1, 1.2, 1.4, 1.6, 1.8, 1.5, 1.4, 1.3,
           1.1, 1.0, 1.0, 1.2, 1.5, 1.7, 1.9, 1.9, 1.6, 1.5, 1.4, 1.3]


def test_percent_zone_price_shifting_keeps_the_days_heat():
    z = ZoneConfig(zone_id="bad", name="Bad", mode=MODE_PERCENT, comfort=0.0, min_value=0.0, max_value=100.0,
                   away_offset=-20.0, price_shift=15.0, preheat=10.0)
    sched = default_schedule(MODE_PERCENT)
    prices = {DAY + timedelta(hours=i): v for i, v in enumerate(TYPICAL)}
    p = build_plan(z, sched, {}, prices, PriceRules(), DAY, 24)
    assert any(R_EXPENSIVE in h.reasons for h in p.hours)  # still shifts away from the peaks
    planned = sum(h.target or 0.0 for h in p.hours)
    scheduled = sum(h.scheduled for h in p.hours)
    assert planned >= scheduled - 0.5
    # and puts it in cheaper hours: the price-weighted heat goes down
    assert sum((h.target or 0.0) * h.price for h in p.hours) < sum(h.scheduled * h.price for h in p.hours)


def test_benchmark_smoke():
    from tools.bench.ours import Ours
    from tools.bench.sim import Case, run
    from tools.bench.strategies import Emhass, Fixed, PriceAnalyzer, Sparegris

    case = Case("oct", 1, "spot_support", days=1)
    out = {s.name: run(s, case) for s in (Fixed(), PriceAnalyzer(), Sparegris(), Emhass(mode="power"), Ours("rules"))}
    for r in out.values():
        b = r["bills"]["spot_support"]
        assert b["cost"] > 0 and r["heat_kwh"] > 0 and r["deficit"] >= r["deficit_band"] >= 0
    # price-blind strategies are billed under every contract from the same run
    assert set(out["fixed_schedule"]["bills"]) == {"spot", "spot_support", "norgespris"}
    # deterministic
    again = run(Fixed(), case)
    assert again["bills"] == out["fixed_schedule"]["bills"]


# --------------------------------------------------------------------------- round 2

BATH = ZoneConfig(zone_id="bad", name="Bad", mode=MODE_PERCENT, comfort=0.0, min_value=0.0, max_value=100.0,
                  away_offset=-20.0, price_shift=15.0, preheat=10.0)


def _evening_peak(day=DAY, days=2, peak=3.0):
    return {day + timedelta(hours=i): (peak if i % 24 in (17, 18, 19) else 1.0) for i in range(24 * days)}


def test_percent_zone_preheats_just_before_a_cut_and_keeps_it_across_replans():
    sched = {wd: [40.0] * 24 for wd in range(7)}
    prices = _evening_peak(peak=3.5)  # 17–19 extreme (≥ 3.0)
    early = build_plan(BATH, sched, {}, prices, PriceRules(), DAY + timedelta(hours=12), 24)
    by = {h.start.hour: h for h in early.hours if h.start.date() == DAY.date()}
    taken = sum(h.scheduled - (h.target or 0.0) for h in by.values() if h.target is None or h.target < h.scheduled)
    added = sum(h.target - h.scheduled for h in by.values() if h.target is not None and h.target > h.scheduled)
    assert taken > 0 and added == pytest.approx(taken, abs=1.0)
    # all of it in the three hours before the cuts (before: the day's cheapest hours, often after
    # the cut – and forgotten once the cut hours had left the plan)
    assert all(by[h].target == 40.0 for h in (12, 13, 20, 21, 22, 23))
    assert all(by[h].target > 40.0 for h in (14, 15, 16))
    # a re-plan in the middle of the cut keeps the same hours (deterministic over the window)
    later = build_plan(BATH, sched, {}, prices, PriceRules(), DAY + timedelta(hours=18), 6)
    assert [h.target for h in later.hours] == [by[h].target for h in range(18, 24)]
    # what cannot be put back before is not taken: with less room before the peak (no
    # pre-heat allowed above the schedule) the OFF hour becomes a smaller cut
    tight = replace(BATH, price_shift=10.0, preheat=0.0)
    p = build_plan(tight, sched, {}, prices, PriceRules(), DAY + timedelta(hours=12), 24)
    first = {h.start.hour: h for h in p.hours if h.start.date() == DAY.date()}
    assert first[17].target is not None and first[17].target > 0
    lowered = sum(h.scheduled - h.target for h in first.values() if h.target < h.scheduled)
    raised = sum(h.target - h.scheduled for h in first.values() if h.target > h.scheduled)
    assert lowered == pytest.approx(raised, abs=1.0)


def test_choose_limit_aims_for_the_lower_step_only_while_reachable():
    cfg = PeakConfig()
    today = DAY.date()
    # last month 5.6 kW (10 kW step) but this house can stay under 4 kW: aim for 4.7
    assert choose_limit(cfg, [], 0.0, today=today, days_left=13, reference_kw=5.6, floor_kw=4.0) == pytest.approx(4.7)
    # a cold spell: the unavoidable peak is above the lower step → defend the current one
    assert choose_limit(cfg, [], 0.0, today=today, days_left=13, reference_kw=5.6, floor_kw=5.1) == pytest.approx(9.7)
    # one bad day does not end the attempt …
    bad = [(today - timedelta(days=1), 5.8)]
    assert choose_limit(cfg, bad, 0.0, today=today, days_left=13, reference_kw=5.6, floor_kw=4.0) == pytest.approx(4.7)
    # … three do
    three = [(today - timedelta(days=k), 5.8) for k in (1, 2, 3)]
    assert choose_limit(cfg, three, 0.0, today=today, days_left=13, reference_kw=5.6, floor_kw=4.0) == pytest.approx(9.7)
    # without a floor estimate: the old behaviour (hold the current step)
    assert choose_limit(cfg, [], 0.0, today=today, days_left=13, reference_kw=5.6) == pytest.approx(9.7)


def test_plan_peaks_stores_heat_before_the_peak_instead_of_setting_back():
    slow = ZoneConfig(zone_id="stue", name="Stue", heater_kw=2.0, warmup=True,
                      thermal=ThermalModel(gross_rate=1.3, tau_hours=40.0, learned=True, lag_hours=1.0),
                      protected_from_hour=0, protected_to_hour=0)
    flat = {wd: [0.0] * 24 for wd in range(7)}
    start = DAY + timedelta(hours=10)
    prices = {DAY + timedelta(hours=i): 1.0 + 0.001 * i for i in range(48)}

    def plan(kw_cap=None):
        return build_plan(slow, flat, {}, prices, PriceRules(), start, 24, room_temp=21.0, outdoor_default=-2.0,
                          method="optimize", kw_cap=kw_cap)

    before = plan()
    caps = {h.start: 3.0 for h in before.hours}
    base = {h: (2.8 if h in (17, 18) else 0.5) for h in range(24)}  # cooking: 0.2 kW left for heating
    out = plan_peaks({"stue": slow}, {"stue": before}, caps, base, {}, lambda z, cap: plan(cap))["stue"]

    def kwh(p, hours):
        return sum(h.kwh for h in p.hours if h.start.hour in hours)

    assert kwh(out, (17, 18)) < kwh(before, (17, 18)) - 0.3
    assert kwh(out, (13, 14, 15, 16)) > kwh(before, (13, 14, 15, 16))
    # comfort first: the room coasts on stored heat, it is not held below its schedule
    assert all(h.predicted >= h.scheduled - 0.1 for h in out.hours)


def test_capacity_budget_under_norgespris_is_not_price_shifting():
    z = ZoneConfig(zone_id="stue", name="Stue", heater_kw=2.0,
                   thermal=ThermalModel(gross_rate=1.3, tau_hours=40.0, learned=True))
    flat = {wd: [0.0] * 24 for wd in range(7)}
    start = DAY + timedelta(hours=10)
    prices = {DAY + timedelta(hours=i): 0.5 + (0.1 if 6 <= i % 24 < 22 else 0.0) for i in range(48)}
    cap = {start + timedelta(hours=k): 0.1 for k in (7, 8)}
    p = build_plan(z, flat, {}, prices, PriceRules(), start, 24, room_temp=21.0, outdoor_default=-2.0,
                   method="optimize", kw_cap=cap)
    reasons = {r for h in p.hours for r in h.reasons}
    assert R_OPTIMIZED not in reasons and R_PEAK in reasons


def test_live_guard_does_not_set_back_a_percent_zone():
    plan = build_plan(BATH, default_schedule(MODE_PERCENT), {}, {}, PriceRules(), DAY + timedelta(hours=18), 2)
    now = DAY + timedelta(hours=18, minutes=20)
    assert live_shed(9.0, 5.0, {"bad": plan}, {"bad": BATH}, {}, {}, now) == {}
