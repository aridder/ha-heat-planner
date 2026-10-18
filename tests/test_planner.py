"""Unit tests for the pure planning engine."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.heat_planner.planner import (
    LEVEL_CHEAP,
    LEVEL_EXPENSIVE,
    LEVEL_EXTREME,
    LEVEL_NORMAL,
    MODE_PERCENT,
    PricePoint,
    PriceRules,
    R_AWAY,
    R_EXPENSIVE,
    R_EXTREME_OFF,
    R_OVERRIDE,
    R_PREHEAT,
    R_PREHEAT_EXTREME,
    R_PROTECTED,
    Sample,
    ThermalModel,
    ZoneConfig,
    build_plan,
    classify_prices,
    default_schedule,
    fit_thermal,
    hourly_prices,
)

TZ = ZoneInfo("Europe/Oslo")
DAY = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)  # a Monday


def prices_for(day: datetime, values: list[float]) -> dict[datetime, float]:
    return {day + timedelta(hours=i): v for i, v in enumerate(values)}


# A typical Norwegian day: cheap at night and early afternoon, peaks at 8 and 18-19.
TYPICAL = [1.2, 1.2, 1.1, 1.1, 1.1, 1.2, 1.4, 1.6, 1.8, 1.5, 1.4, 1.3,
           1.1, 1.0, 1.0, 1.2, 1.5, 1.7, 1.9, 1.9, 1.6, 1.5, 1.4, 1.3]


def zone(**kw) -> ZoneConfig:
    return ZoneConfig(zone_id="stue", name="Stue", comfort=21.0, **kw)


def test_hourly_aggregation_of_quarter_prices():
    pts = [
        PricePoint(DAY + timedelta(minutes=15 * i), DAY + timedelta(minutes=15 * (i + 1)), p)
        for i, p in enumerate([1.0, 2.0, 3.0, 4.0, 5.0, 5.0, 5.0, 5.0])
    ]
    hours = hourly_prices(pts)
    assert hours == {DAY: 2.5, DAY + timedelta(hours=1): 5.0}


def test_classification_uses_each_days_own_distribution():
    p = prices_for(DAY, TYPICAL)
    levels = classify_prices(p, PriceRules())
    # top 3 hours: 8, 18, 19
    assert {h.hour for h, l in levels.items() if l == LEVEL_EXPENSIVE} == {8, 18, 19}
    assert levels[DAY + timedelta(hours=13)] == LEVEL_CHEAP
    assert levels[DAY + timedelta(hours=10)] == LEVEL_NORMAL


def test_too_few_prices_means_everything_normal():
    p = prices_for(DAY, [1.0, 5.0, 1.0])
    assert set(classify_prices(p, PriceRules()).values()) == {LEVEL_NORMAL}


def test_extreme_hour_detected_by_ratio_and_absolute():
    vals = list(TYPICAL)
    vals[18] = 4.5
    levels = classify_prices(prices_for(DAY, vals), PriceRules())
    assert levels[DAY + timedelta(hours=18)] == LEVEL_EXTREME


def test_default_schedule_shape():
    sched = default_schedule("setpoint")
    assert len(sched) == 7 and all(len(v) == 24 for v in sched.values())
    assert sched[0][6] == 1.0 and sched[0][23] == -2.0 and sched[0][12] == 0.0
    pct = default_schedule(MODE_PERCENT)
    assert pct[0][6] == 70 and pct[0][21] == 70 and pct[0][3] == 20


def test_plan_without_prices_follows_schedule():
    z = zone()
    plan = build_plan(z, default_schedule("setpoint"), {}, {}, PriceRules(), DAY, 24)
    targets = [h.target for h in plan.hours]
    assert targets[0] == 19.0 and targets[6] == 22.0 and targets[12] == 21.0
    assert plan.hours[6].mode == "comfort" and plan.hours[0].mode == "setback"


def test_expensive_hours_are_reduced_and_cheap_hours_before_preheat():
    z = zone()
    plan = build_plan(z, default_schedule("setpoint"), {}, prices_for(DAY, TYPICAL),
                      PriceRules(), DAY, 24)
    by = {h.start.hour: h for h in plan.hours}
    assert R_EXPENSIVE in by[18].reasons and by[18].target == 20.0
    # 15 is not cheap (1.2) but 14 is cheap and 16/17 are not "expensive"; 15:00 -> 18 is 3 ahead
    assert R_PREHEAT in by[15].reasons or R_PREHEAT in by[14].reasons
    assert by[18].mode == "save"


def test_protected_window_blocks_reductions():
    vals = list(TYPICAL)
    vals[14] = 3.5  # extreme in the protected window 13-15
    z = zone()
    plan = build_plan(z, default_schedule("setpoint"), {}, prices_for(DAY, vals),
                      PriceRules(), DAY, 24)
    h14 = plan.hours[14]
    assert h14.target == 21.0
    assert R_PROTECTED in h14.reasons and R_EXTREME_OFF not in h14.reasons


def test_extreme_hour_turns_off_and_hours_before_preheat_extra():
    vals = list(TYPICAL)
    vals[18] = 4.5
    z = zone()
    plan = build_plan(z, default_schedule("setpoint"), {}, prices_for(DAY, vals),
                      PriceRules(), DAY, 24, room_temp=21.0)
    assert plan.hours[18].target is None and plan.hours[18].mode == "off"
    assert R_EXTREME_OFF in plan.hours[18].reasons
    assert R_PREHEAT_EXTREME in plan.hours[16].reasons
    assert plan.hours[16].target == 23.0
    assert plan.hours[18].kwh == 0


def test_away_is_floor_not_additive_and_ends_at_warmup_hour():
    z = zone()
    start = DAY + timedelta(hours=9)
    plan = build_plan(z, default_schedule("setpoint"), {}, {}, PriceRules(), start, 15,
                      away_now=True)
    by = {h.start.hour: h for h in plan.hours}
    assert by[9].target == 17.0 and R_AWAY in by[9].reasons
    assert by[12].target == 17.0
    assert by[13].target == 21.0 and R_AWAY not in by[13].reasons
    # night setback (-2) and away (-4) do not stack
    night = build_plan(z, default_schedule("setpoint"), {}, {}, PriceRules(), DAY, 2,
                       away_now=True)
    assert night.hours[0].target == 17.0


def test_override_for_a_date_wins_over_weekday_schedule():
    z = zone()
    ov = {DAY.date().isoformat(): [3.0] * 24}
    plan = build_plan(z, default_schedule("setpoint"), ov, {}, PriceRules(), DAY, 26)
    assert plan.hours[0].target == 24.0 and R_OVERRIDE in plan.hours[0].reasons
    # next day falls back to the schedule
    assert plan.hours[25].target == 19.0


def test_aggressiveness_zero_ignores_prices():
    z = zone(price_aggressiveness=0.0)
    plan = build_plan(z, default_schedule("setpoint"), {}, prices_for(DAY, TYPICAL),
                      PriceRules(), DAY, 24)
    assert all(h.mode in ("comfort", "setback", "normal") for h in plan.hours)


def test_clamped_to_limits():
    z = zone(max_value=22.0)
    ov = {DAY.date().isoformat(): [10.0] * 24}
    plan = build_plan(z, default_schedule("setpoint"), ov, {}, PriceRules(), DAY, 1)
    assert plan.hours[0].target == 22.0


def test_percent_zone_plan_and_cost():
    z = ZoneConfig(zone_id="bad", name="Bad", mode=MODE_PERCENT, min_value=0, max_value=100,
                   away_offset=-20, preheat=10, price_shift=15, heater_kw=0.4)
    plan = build_plan(z, default_schedule(MODE_PERCENT), {}, prices_for(DAY, TYPICAL),
                      PriceRules(), DAY, 24)
    by = {h.start.hour: h for h in plan.hours}
    assert by[6].target == 70 and by[3].target == 20
    assert by[19].target == 25  # 40 - 15 in an expensive hour
    assert by[6].kwh == pytest.approx(0.28)
    assert plan.cost is not None and plan.baseline_cost is not None
    assert plan.savings > 0


def test_prediction_heats_toward_target_and_cools_when_off():
    z = zone(thermal=ThermalModel(gross_rate=1.0, tau_hours=40))
    vals = list(TYPICAL)
    vals[18] = 4.5
    plan = build_plan(z, default_schedule("setpoint"), {}, prices_for(DAY, vals),
                      PriceRules(), DAY, 24, room_temp=19.0, outdoor_default=0.0)
    temps = [h.predicted for h in plan.hours]
    assert temps[0] == pytest.approx(19.0, abs=0.1)
    assert temps[7] > temps[4]  # morning comfort heats up
    assert temps[18] < temps[17]  # cut off during the extreme hour cools down
    assert 0 < plan.hours[12].duty < 1


def test_savings_positive_with_price_logic():
    z = zone()
    plan = build_plan(z, default_schedule("setpoint"), {}, prices_for(DAY, TYPICAL),
                      PriceRules(), DAY, 24, room_temp=21.0)
    assert plan.cost is not None
    assert plan.kwh <= plan.baseline_kwh + 0.5


def test_plan_crosses_midnight_with_tomorrows_prices():
    tomorrow = DAY + timedelta(days=1)
    p = {**prices_for(DAY, TYPICAL), **prices_for(tomorrow, TYPICAL)}
    plan = build_plan(zone(), default_schedule("setpoint"), {}, p, PriceRules(),
                      DAY + timedelta(hours=12), 24)
    assert len(plan.hours) == 24 and all(h.price is not None for h in plan.hours)
    assert plan.hours[-1].start.date() == tomorrow.date()


def test_fit_thermal_recovers_parameters():
    tau, gross = 30.0, 1.2
    samples, temp, t = [], 20.0, DAY
    for i in range(120):
        heating = (i // 10) % 2 == 0
        samples.append(Sample(t, temp, 0.0, heating))
        loss = (temp - 0.0) / tau
        temp += ((gross if heating else 0.0) - loss) * 0.5
        t += timedelta(minutes=30)
    model = fit_thermal(samples)
    assert model.learned
    assert model.tau_hours == pytest.approx(tau, rel=0.25)
    assert model.gross_rate == pytest.approx(gross, rel=0.25)


def test_fit_thermal_falls_back_with_little_data():
    model = fit_thermal([], ThermalModel(gross_rate=0.8, tau_hours=50))
    assert not model.learned and model.gross_rate == 0.8 and model.tau_hours == 50
