"""Closed-loop digital-twin simulation (tools/sim) and the regressions it found.

The simulator runs the real planning/control code against a house whose rooms
behave differently from what the planner believes (see tools/sim/harness.py).
``python -m tools.sim`` runs the full 7–14 day matrix and prints a KPI table;
here the scenarios run shortened, and every invariant must hold.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.heat_planner.const import DOMAIN
from custom_components.heat_planner.control import (
    HourMeter,
    decide,
    live_shed,
    off_run_hours,
    safety_hold,
    settle,
    too_cold,
)
from custom_components.heat_planner.optimizer import R_OPTIMIZED
from custom_components.heat_planner.planner import (
    R_EXPENSIVE,
    R_EXTREME_OFF,
    R_PREHEAT,
    R_PREHEAT_EXTREME,
    PricePoint,
    PriceRules,
    ThermalModel,
    ZoneConfig,
    baseline_hour,
    build_plan,
    default_schedule,
    hour_key,
    hourly_prices,
    hours_from,
)
from custom_components.heat_planner.tariff import TariffConfig, effective_prices
from tools.sim.harness import Scenario, run
from tools.sim.scenarios import matrix
from tools.sim.twin import local_day_hours

TZ = ZoneInfo("Europe/Oslo")
UTC = timezone.utc
DST_DAY = datetime(2026, 10, 25, tzinfo=TZ).date()  # 25 hours
SPRING_DAY = datetime(2027, 3, 28, tzinfo=TZ).date()  # 23 hours
SCHEDULE = default_schedule("setpoint")
PRICE_REASONS = {R_PREHEAT, R_PREHEAT_EXTREME, R_EXPENSIVE, R_EXTREME_OFF, R_OPTIMIZED}


def _zone(**kw) -> ZoneConfig:
    kw.setdefault("thermal", ThermalModel(gross_rate=1.5, tau_hours=35.0))
    return ZoneConfig(zone_id="stue", name="Stue", **kw)


def _day_prices(day, value=lambda t, i: 1.0 + 0.01 * i) -> list[PricePoint]:
    return [PricePoint(t, t + timedelta(hours=1), value(t, i)) for i, t in enumerate(local_day_hours(day))]


# --------------------------------------------------------------------------- DST: hourly keys


def test_hourly_prices_keep_both_autumn_0200_hours() -> None:
    pts = _day_prices(DST_DAY)
    assert len(pts) == 25
    hours = hourly_prices(pts)
    # before: the two 02:00 hours were one dict key (averaged), 24 hours
    assert len(hours) == 25
    twos = sorted((h, p) for h, p in hours.items() if h.hour == 2)
    assert len(twos) == 2 and twos[0][1] != twos[1][1]


def test_plan_steps_real_hours_through_autumn_dst() -> None:
    start = datetime(2026, 10, 24, 12, 10, tzinfo=TZ)
    prices = hourly_prices(_day_prices(start.date()) + _day_prices(DST_DAY))
    plan = build_plan(_zone(), SCHEDULE, {}, prices, PriceRules(), start, 36, room_temp=21.0)
    utc = [h.start.astimezone(UTC) for h in plan.hours]
    # before: wall-clock "+ 1 h" skipped the second 02:00 (23 real hours in 24 slots)
    assert all(b - a == timedelta(hours=1) for a, b in zip(utc, utc[1:]))
    sunday = [h for h in plan.hours if h.start.date() == DST_DAY]
    assert [h.start.hour for h in sunday[:5]] == [0, 1, 2, 2, 3]
    # schedule index on a 25-hour day: both 02:00 hours use hour 2, 03:00 uses hour 3
    sched = SCHEDULE[DST_DAY.weekday()]
    assert sunday[2].scheduled == sunday[3].scheduled == 21.0 + sched[2]
    assert sunday[4].scheduled == 21.0 + sched[3]
    # and each 02:00 gets its own price
    assert sunday[2].price != sunday[3].price


def test_plan_on_23_hour_spring_day() -> None:
    start = datetime(2027, 3, 28, 0, 0, tzinfo=TZ)
    prices = hourly_prices(_day_prices(SPRING_DAY))
    assert len(prices) == 23
    plan = build_plan(_zone(), SCHEDULE, {}, prices, PriceRules(), start, 24, room_temp=21.0)
    hours = [h.start for h in plan.hours]
    assert 2 not in [h.hour for h in hours if h.date() == SPRING_DAY]
    assert hours[-1].date() > SPRING_DAY  # 24 real hours reach into Monday
    assert sum(1 for h in plan.hours if h.price is not None) == 23


def test_hours_from_and_fixed_prices_on_dst_day() -> None:
    hours = hours_from(datetime(2026, 10, 24, 18, tzinfo=TZ), 36)
    assert len({h.astimezone(UTC) for h in hours}) == 36
    # Norgespris without any spot data: one price per real hour
    cfg = TariffConfig(mode="fixed", fixed_price=0.5)
    assert len(effective_prices({h: 0.0 for h in hours}, cfg)) == 36


def test_hour_meter_restarts_in_the_repeated_hour() -> None:
    meter = HourMeter()
    t = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)  # 02:00 CEST
    while t <= datetime(2026, 10, 25, 1, 30, tzinfo=UTC):  # 02:30 CET (second 02:00 hour)
        meter.add(2.0, t.astimezone(TZ))
        t += timedelta(minutes=1)
    # half an hour at 2 kW so far + 2 kW for the remaining half hour
    # before: the first 02:00 hour's energy was carried over (≈ 3 kWh projected)
    assert meter.projected((t - timedelta(minutes=1)).astimezone(TZ)) == pytest.approx(2.0, abs=0.05)


def test_meter_projection_follows_the_average_not_the_last_reading() -> None:
    meter = HourMeter()
    t = datetime(2026, 10, 20, 17, 0, tzinfo=TZ)
    for i in range(20):  # thermostats switching full power on and off
        meter.add(6.0 if i % 2 else 1.0, t + timedelta(minutes=i))
    projected = meter.projected(t + timedelta(minutes=19), smooth=True)
    so_far = meter.kwh
    # before: the last reading (6 kW) was projected over the remaining 41 minutes (≈ 5.3 kWh)
    assert so_far + 2.5 * 41 / 60 < projected < so_far + 4.5 * 41 / 60


def test_live_guard_holds_until_the_real_end_of_the_hour() -> None:
    now = datetime(2026, 10, 25, 0, 30, tzinfo=UTC).astimezone(TZ)  # 02:30 CEST
    plan = build_plan(_zone(heater_kw=3.0, protected_from_hour=0, protected_to_hour=0), SCHEDULE, {}, {},
                      PriceRules(), now, 2, room_temp=18.0)
    zones = {"stue": _zone(heater_kw=3.0, protected_from_hour=0, protected_to_hour=0)}
    shed = live_shed(5.5, 5.0, {"stue": plan}, zones, {}, {}, now)
    # before: "02:00 + 1 h" in wall time was 03:00 CET, two real hours later
    assert shed["stue"].astimezone(UTC) == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
    # an hour no set-back can save is let go (only comfort lost, and a reheat peak after)
    assert live_shed(9.0, 5.0, {"stue": plan}, zones, {}, shed, now) == {}


# --------------------------------------------------------------------------- max off, restarts


def test_off_run_does_not_count_the_current_hour_twice() -> None:
    off = datetime(2026, 10, 20, 10, 0, 5, tzinfo=TZ)
    # the plan starts at the hour containing "now"; that hour is its first slot
    assert off_run_hours(off, off + timedelta(minutes=5)) == 0  # before: 1
    assert off_run_hours(off, off + timedelta(hours=1)) == 1  # before: 2
    # so a planned 2-hour OFF (max_off_hours=2) is not cut to one hour on the re-plan at 11:00
    z = _zone(max_off_hours=2, protected_from_hour=0, protected_to_hour=0, safety_min_temp=5.0)
    day = datetime(2026, 10, 20, tzinfo=TZ)
    prices = {day + timedelta(hours=i): (6.0 if i in (10, 11) else 1.0) for i in range(24)}
    replan = build_plan(z, SCHEDULE, {}, prices, PriceRules(), off + timedelta(hours=1), 3,
                        room_temp=21.0, initial_off_run=off_run_hours(off, off + timedelta(hours=1)))
    assert replan.hours[0].target is None and replan.hours[1].target is not None


async def test_off_run_survives_a_restart(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    freezer.move_to(datetime(2026, 10, 20, 11, 10, tzinfo=TZ))
    hass.states.async_set("climate.stue", "off", {"temperature": None, "current_temperature": 20.0,
                                                  "hvac_modes": ["off", "heat"]})
    hass.states.async_set("sensor.spot", "1.0", {"unit_of_measurement": "NOK/kWh"})
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    entry = MockConfigEntry(domain=DOMAIN, title="Hjem", data={}, options={
        "price_type": "sensor", "price_sensor": "sensor.spot",
        "zones": [{"zone_id": "stue", "name": "Stue", "mode": "setpoint", "climate_entity": "climate.stue",
                   "comfort": 21, "heater_kw": 1.0}],
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    # turned off at 09:00 by us, then HA restarted (in-memory state gone)
    coord.zone_state("stue")["off_since"] = datetime(2026, 10, 20, 9, 0, 5, tzinfo=TZ).isoformat()
    coord._applied.clear()
    # before: 0 – a restart restarted the max_off_hours count
    assert coord._off_run("stue") == 2


# --------------------------------------------------------------------------- the hour's command


def _hour(target, scheduled: float, start=None, reasons=("schedule",)):
    from custom_components.heat_planner.planner import PlanHour

    return PlanHour(start=start or datetime(2026, 10, 20, 18, tzinfo=TZ), price=1.0, level="normal",
                    scheduled=scheduled, target=target, mode="normal", reasons=list(reasons))


def test_no_flapping_within_an_hour() -> None:
    z = _zone()
    h = _hour(21.5, 22.0)
    last = {"value": 22.0, "off": False, "hour": h.start}
    # the 5-minute re-plan nudged the hour by one step: keep what was written
    assert settle(z, last, h.start, False, 21.5) == 22.0
    # a new hour, a bigger change, or the safety hold always go through
    assert settle(z, last, h.start + timedelta(hours=1), False, 21.5) == 21.5
    assert settle(z, last, h.start, False, 20.5) == 20.5
    assert settle(z, last, h.start, False, 21.5, hold=True) == 21.5


def test_no_price_lowering_near_the_safety_floor() -> None:
    z = _zone(comfort=19.0, safety_min_temp=18.0)
    h = _hour(18.0, 19.0, reasons=("schedule", R_EXPENSIVE))
    # room within 0.5 °C of the floor: before, a lowered setpoint was kept until the room
    # was *below* the floor, and the dead time took it 1–3 °C further down at -15 °C
    hold = safety_hold(18.4, z, False)
    assert hold and decide(z, h, 0.5, hold=hold) == (False, 19.0)
    assert not safety_hold(19.0, z, True)
    assert decide(z, h, 0.5, hold=False) == (False, 18.0)


def test_capacity_limiting_is_bounded_by_comfort() -> None:
    from custom_components.heat_planner.energy import PEAK_SLACK, apply_cuts

    z = _zone(heater_kw=3.0, protected_from_hour=0, protected_to_hour=0)
    h = _hour(21.0, 21.0)
    slots = [h]
    apply_cuts(z, slots, {h.start: 0.9}, {h.start: 21.0})
    # before: 2 °C below the schedule (and the live guard 2 °C more, all evening)
    assert slots[0].target == 21.0 - PEAK_SLACK
    plan = build_plan(z, SCHEDULE, {}, {}, PriceRules(), h.start, 2, room_temp=20.0)
    now = h.start + timedelta(minutes=20)
    cold = {"stue"} if too_cold(z, plan.hours[0], 20.0) else set()
    assert cold == {"stue"}
    # a room already 0.25 °C+ under its schedule is not set back, and is released if it was
    shed = live_shed(9.0, 5.0, {"stue": plan}, {"stue": z}, {}, {"stue": now + timedelta(minutes=30)}, now, cold)
    assert shed == {}


# --------------------------------------------------------------------------- savings, Norgespris, rules


def test_baseline_hour_carries_the_floor_dead_time() -> None:
    z = _zone(thermal=ThermalModel(gross_rate=1.2, tau_hours=40.0, lag_hours=1.5))
    flat = {wd: [0.0] * 24 for wd in range(7)}
    start = datetime(2026, 10, 20, 6, tzinfo=TZ)
    temp, queue = 18.0, None
    for i in range(6):
        hour, queue = baseline_hour(z, flat, {}, {}, start + timedelta(hours=i), room_temp=temp,
                                    queue=queue, outdoor_default=0.0)
        temp = hour.predicted
    whole = build_plan(replace(z, price_aggressiveness=0.0), flat, {}, {}, PriceRules(), start, 6,
                       room_temp=18.0, outdoor_default=0.0)
    # hour by hour (the savings "shadow" house) == one 6-hour simulation; before, the
    # queued heat was dropped every hour and the shadow never warmed up
    assert temp == pytest.approx(whole.hours[-1].predicted, abs=0.05)
    assert temp > 20.0


def test_norgespris_with_grid_tariff_is_not_shifted_by_the_optimiser() -> None:
    day = datetime(2026, 10, 20, tzinfo=TZ)  # a Tuesday: Elvia day/night differ by 0.10
    cfg = TariffConfig.from_options({"preset": "elvia_2026", "mode": "fixed"})
    prices = effective_prices({day + timedelta(hours=i): 1.0 for i in range(48)}, cfg)
    assert max(prices.values()) - min(prices.values()) == pytest.approx(0.1)
    plan = build_plan(_zone(), SCHEDULE, {}, prices, PriceRules(), day + timedelta(hours=12), 36,
                      room_temp=20.5, method="optimize", outdoor_default=-5.0)
    # before: the optimiser pre-heated at night for the 0.10 kr grid difference
    assert not [h for h in plan.hours if PRICE_REASONS & set(h.reasons)]
    assert [h.target for h in plan.hours] == [h.scheduled for h in plan.hours]


def test_rules_back_off_when_a_cold_room_cannot_recover() -> None:
    day = datetime(2026, 10, 20, tzinfo=TZ)
    vals = [1.0] * 24
    for h in (6, 7, 8, 17, 18, 19):
        vals[h] = 5.0
    prices = {day + timedelta(hours=i): vals[i % 24] for i in range(48)}
    slow = _zone(thermal=ThermalModel(gross_rate=1.4, tau_hours=28.0, lag_hours=1.0),
                 protected_from_hour=0, protected_to_hour=0, max_off_hours=3)
    plan = build_plan(slow, SCHEDULE, {}, prices, PriceRules(), day, 36, room_temp=21.0,
                      outdoor_default=-15.0, method="rules")
    # never an OFF hour that takes the room below the safety floor (before: 3 h off → 17.1 °C)
    assert any(h.target is None for h in plan.hours)
    assert all(h.predicted >= slow.safety_min_temp - 0.05 for h in plan.hours)
    # lowered hours are backed off until the recovery tail fits the budget
    flat = {wd: [0.0] * 24 for wd in range(7)}
    evening = {day + timedelta(hours=i): (2.5 if i % 24 in (17, 18, 19) else 1.0) for i in range(48)}
    z = replace(slow, thermal=ThermalModel(gross_rate=1.5, tau_hours=28.0, lag_hours=1.0), preheat=0.0,
                price_shift=2.0, extreme_off=False)
    start = day + timedelta(hours=12)
    plan = build_plan(z, flat, {}, evening, PriceRules(), start, 24, room_temp=21.0, outdoor_default=-15.0,
                      method="rules")
    raw = build_plan(z, flat, {}, evening, PriceRules(), start, 24, room_temp=None, method="rules")
    assert [h.target for h in raw.hours[5:8]] == [19.0, 19.0, 19.0]
    assert sum(h.target for h in plan.hours) > sum(h.target for h in raw.hours)
    intended = sum(max(0.0, h.scheduled - h.target) for h in plan.hours)
    assert plan.comfort_deficit - plan.baseline_comfort_deficit <= intended + 1.0 + 0.05


# --------------------------------------------------------------------------- the digital twin

QUICK = {s.name: s for s in matrix(quick=True)}
# (month_boundary and peak_feasible run in test_peak_limiting_never_raises_the_monthly_top3)
SCENARIOS = [
    "spot_support", "spot_rules", "cold_-15_rules", "cold_-15_opt", "norgespris_elvia",
    "15min_spike", "holiday_away", "dst_autumn", "dst_spring", "feedback_manual", "faults",
    "replan_5min",
]


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario_invariants(name: str) -> None:
    sc = QUICK[name]
    if name == "replan_5min":  # re-plans every 5 minutes, like the coordinator
        sc = replace(sc, days=0.4)
    if name == "holiday_away":
        sc = replace(sc, days=2.5)
    result = run(sc)
    assert result.ok(), (result.violations, result.kpi)
    if sc.contract == "fixed":
        assert result.kpi["norgespris_shift"] == 0


@pytest.mark.parametrize("name", ["peak_feasible", "month_boundary"])
def test_peak_limiting_never_raises_the_monthly_top3(name: str) -> None:
    sc = QUICK[name]
    on, off = run(sc), run(replace(sc, peak_limit=False))
    for month, value in on.kpi["top3"].items():
        assert value <= off.kpi["top3"][month] + 0.05
    if name == "peak_feasible":  # heating-driven peaks: limiting lowers them
        assert on.kpi["top3"]["2026-10"] < off.kpi["top3"]["2026-10"] - 0.1
    assert on.ok(), on.violations


def test_savings_claims_follow_the_real_house() -> None:
    # before: every hour was compared with a baseline restarted from the (cold) real
    # room, so the recovery after a setback was never charged: 155 kr claimed for
    # 32 kr realised in a week at -15 °C
    r = run(replace(QUICK["cold_-15_rules"], days=2))
    assert r.kpi["realised"] > 0
    assert r.kpi["claimed"] <= 1.5 * r.kpi["realised"]


def test_dst_scenario_through_the_real_week() -> None:
    # this Sunday: Saturday evening to Tuesday, 5 kr spikes (extreme → OFF allowed) and a
    # crash during the repeated 02:00 hour
    sc = Scenario("dst", datetime(2026, 10, 24, 18, tzinfo=TZ), days=3, contract="spot",
                  price_pattern=("spike", "normal"),
                  restarts=[(datetime(2026, 10, 25, 2, 20, tzinfo=TZ, fold=1), False)])
    r = run(sc)
    assert r.ok(), r.violations


# --------------------------------------------------------------------------- the real coordinator over DST


async def test_coordinator_through_the_autumn_dst_night(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    hass.config.currency = "NOK"
    raw = []
    for day in (datetime(2026, 10, 24).date(), DST_DAY):
        for i, t in enumerate(local_day_hours(day)):
            raw.append({"start": t.isoformat(), "end": (t + timedelta(hours=1)).isoformat(),
                        "value": round(1.0 + 0.05 * i, 3)})
    hass.states.async_set("sensor.spot", "1.0", {"unit_of_measurement": "NOK/kWh", "raw_today": raw})
    hass.states.async_set("climate.stue", "heat", {"temperature": 21, "current_temperature": 20.5,
                                                   "hvac_modes": ["off", "heat"]})
    calls = async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    t = datetime(2026, 10, 24, 22, 0, 10, tzinfo=TZ)
    freezer.move_to(t)
    entry = MockConfigEntry(domain=DOMAIN, title="Hjem", data={}, options={
        "price_type": "sensor", "price_sensor": "sensor.spot",
        "energy": {"contract": "spot", "grid": "none", "peak_limit": False},
        "zones": [{"zone_id": "stue", "name": "Stue", "mode": "setpoint", "climate_entity": "climate.stue",
                   "comfort": 21, "heater_kw": 1.0}],
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert sum(1 for h in coord.price_hours if h.date() == DST_DAY) == 25
    seen = []
    utc = t.astimezone(UTC)
    for _ in range(8):  # 22, 23, 00, 01, 02 CEST, 02 CET, 03, 04
        freezer.move_to(utc)
        if calls:  # the thermostat took what we wrote (else it looks like a manual change)
            hass.states.async_set("climate.stue", "heat", {"temperature": calls[-1].data["temperature"],
                                                           "current_temperature": 20.5, "hvac_modes": ["off", "heat"]})
        await coord.async_refresh()
        now = dt_util.now()
        first = coord.plans["stue"].hours[0]
        assert first.start == hour_key(now)
        assert coord.effective_price_now() == first.price
        starts = [h.start.astimezone(UTC) for h in coord.plans["stue"].hours]
        assert all(b - a == timedelta(hours=1) for a, b in zip(starts, starts[1:]))
        seen.append(first.start.isoformat())
        utc += timedelta(hours=1)
    assert "2026-10-25T02:00:00+02:00" in seen and "2026-10-25T02:00:00+01:00" in seen
    # every real hour was booked once (the hour in progress is still pending)
    assert coord.state["savings"]["hours"] == 7
