"""Optimal start ("warm-up"): reach the next schedule step on time.

Found by the strategy benchmark (tools/bench): a thermostat that only switches to
the new target at the step left a slow concrete floor 1–2 °C under the schedule for
hours after every morning and afternoon step (≈ 3–6 °C·h a day); a model-based
optimal start (RoomMind-style) beat Heat Planner on comfort in every season.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.heat_planner.optimizer import R_OPTIMIZED
from custom_components.heat_planner.planner import (
    R_EXPENSIVE,
    R_PREHEAT,
    R_WARMUP,
    PriceRules,
    ThermalModel,
    ZoneConfig,
    baseline_hour,
    build_plan,
)

TZ = ZoneInfo("Europe/Oslo")
DAY = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)  # a Monday
SLOW = ThermalModel(1.25, 44.0, learned=True, lag_hours=2.0)
# 19 °C until 06, 21 °C 06–23 (offsets from comfort 21)
SCHEDULE = {wd: [-2.0] * 6 + [0.0] * 17 + [-2.0] for wd in range(7)}
PRICE_REASONS = {R_PREHEAT, R_EXPENSIVE, R_OPTIMIZED}


def zone(**kw) -> ZoneConfig:
    return ZoneConfig(zone_id="stue", name="Stue", comfort=21.0, heater_kw=2.0, thermal=SLOW, **kw)


def plan(method: str = "rules", prices=None, **kw):
    prices = {DAY + timedelta(hours=i): 1.0 for i in range(24)} if prices is None else prices
    return build_plan(zone(**kw), SCHEDULE, {}, prices, PriceRules(), DAY, 24, room_temp=19.0,
                      outdoor_default=-5.0, method=method)


def at_step(p) -> float:
    return p.hours[5].predicted  # end of 05:00 = when the 21 °C step begins


def test_off_by_default_in_the_pure_api():
    p = plan()
    assert not any(R_WARMUP in h.reasons for h in p.hours)
    assert at_step(p) < 19.5


def test_rules_mode_warms_up_before_the_step_and_reaches_it():
    p = plan(warmup=True)
    warm = [h for h in p.hours if R_WARMUP in h.reasons]
    assert warm and all(h.start.hour < 6 for h in warm)
    assert all(h.target == 21.0 and h.warmup == 21.0 for h in warm)
    # a slow floor needs its dead time and then some, but not the whole night
    assert 2 <= len(warm) <= 6
    assert at_step(p) >= 20.8


def test_flat_prices_warm_up_without_any_price_reason():
    # Norgespris: nothing to shift, the optimiser is skipped – the warm-up still happens
    p = plan("optimize", warmup=True)
    assert p.method == "rules"
    assert any(R_WARMUP in h.reasons for h in p.hours)
    assert not any(PRICE_REASONS & set(h.reasons) for h in p.hours)
    assert at_step(p) >= 20.8


def test_optimiser_reaches_the_step_on_time():
    prices = {DAY + timedelta(hours=i): 0.5 + (1.5 if 6 <= i <= 9 else 0.0) for i in range(24)}
    p = plan("optimize", prices, warmup=True)
    assert p.method == "optimize"
    assert at_step(p) >= 20.8
    # a warm-up hour is never planned below the coming target
    assert all(h.target is not None and h.target >= 21.0 for h in p.hours if h.warmup)


def test_optimiser_without_prices_still_warms_up():
    p = plan("optimize", prices={}, warmup=True)
    assert any(R_WARMUP in h.reasons for h in p.hours)


def test_baseline_includes_the_warm_up():
    # savings compare price control with "the schedule, warmed up on time": the warm-up's
    # energy is comfort the schedule asks for, not a cost of price control
    p = plan(warmup=True)
    assert abs(p.baseline_kwh - p.kwh) < 0.05
    h = next(h for h in p.hours if h.warmup)
    with_warm, _ = baseline_hour(zone(warmup=True), SCHEDULE, {}, {}, h.start, room_temp=19.0,
                                 outdoor_default=-5.0, warmup=h.warmup)
    plain, _ = baseline_hour(zone(warmup=True), SCHEDULE, {}, {}, h.start, room_temp=19.0, outdoor_default=-5.0)
    assert with_warm.target == 21.0 and plain.target == 19.0


def test_unknown_room_temperature_assumes_the_schedule_is_held():
    z = zone(warmup=True)
    prices = {DAY + timedelta(hours=i): 1.0 for i in range(24)}
    p = build_plan(z, SCHEDULE, {}, prices, PriceRules(), DAY, 24, room_temp=None, outdoor_default=-5.0)
    assert any(R_WARMUP in h.reasons for h in p.hours)
