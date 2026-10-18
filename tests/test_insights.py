"""Home-level sensors, realised savings, alerts and the house load for the card."""

from datetime import datetime, timedelta
import json
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events, async_mock_service

from custom_components.heat_planner.coordinator import HeatPlannerCoordinator
from custom_components.heat_planner.insights import (
    EVENT_ALERT,
    alert_message,
    load_series,
    roll_savings,
    savings_value,
)
from custom_components.heat_planner.planner import ThermalModel

from .test_energy_integration import _entry, _setup
from .test_integration import setup_states  # noqa: F401

FIXED = {"contract": "fixed", "fixed_price": 0.5, "grid": "none", "peak_limit": False}
PEAK = {"contract": "fixed", "fixed_price": 0.5, "grid": "none", "peak_limit": True,
        "house_power_sensor": "sensor.house_power"}


def _hour(y, mo, d, h) -> datetime:
    return datetime(y, mo, d, h, tzinfo=dt_util.get_default_time_zone())


# --------------------------------------------------------------------------- pure


def test_roll_savings_commits_completed_hours_only() -> None:
    sv: dict = {}
    vals = iter([1.0, 2.0, -0.5])
    calls = []

    def cur():
        calls.append(1)
        return next(vals)

    assert roll_savings(sv, _hour(2026, 1, 10, 10), cur)
    assert sv["pending"]["amount"] == 1.0 and sv["month_total"] == 0.0
    # same hour again: nothing recomputed, nothing committed
    assert not roll_savings(sv, _hour(2026, 1, 10, 10), cur)
    assert len(calls) == 1
    roll_savings(sv, _hour(2026, 1, 10, 11), cur)
    assert sv["month_total"] == 1.0 and sv["day_total"] == 1.0 and sv["pending"]["amount"] == 2.0
    # survives a JSON round trip (the Store) and continues
    sv = json.loads(json.dumps(sv))
    roll_savings(sv, _hour(2026, 1, 10, 12), cur)
    assert sv["month_total"] == 3.0 and sv["hours"] == 2
    assert savings_value(sv, _hour(2026, 1, 10, 12), "month") == 3.0


def test_roll_savings_month_and_day_change() -> None:
    sv: dict = {}
    roll_savings(sv, _hour(2026, 1, 31, 23), lambda: 4.0)
    assert sv["month"] == "2026-01"
    roll_savings(sv, _hour(2026, 2, 1, 0), lambda: 0.25)
    # the last hour of January counts for January, then February starts from zero
    assert sv["month"] == "2026-02" and sv["month_total"] == 0.0 and sv["day_total"] == 0.0
    assert sv["pending"]["amount"] == 0.25
    roll_savings(sv, _hour(2026, 2, 1, 1), lambda: None)
    assert sv["month_total"] == 0.25 and sv["pending"] is None
    # a sensor read in a later month without a refresh shows zero, not stale totals
    assert savings_value(sv, _hour(2026, 3, 1, 5), "month") == 0.0
    assert savings_value(sv, _hour(2026, 2, 1, 5), "day") == 0.25


def test_load_series_measured_live_and_planned() -> None:
    now = _hour(2026, 1, 10, 10) + timedelta(minutes=20)
    hist = {_hour(2026, 1, 10, 10) - timedelta(hours=i): 1.0 + i for i in range(1, 20)}
    planned = {_hour(2026, 1, 10, 10) + timedelta(hours=i): 2.0 for i in range(3)}
    out = load_series(hist, planned, now, live_kwh=3.3)
    measured = [x for x in out if x.get("measured")]
    assert len(measured) == 12 and measured[-1]["kw"] == 2.0
    assert out[12] == {"start": _hour(2026, 1, 10, 10).isoformat(), "kw": 3.3, "live": True}
    assert [x["kw"] for x in out[13:]] == [2.0, 2.0]


def test_alert_messages_are_translated() -> None:
    en = alert_message("peak_risk", "en", projected_kwh=5.24, cap_kw=5.0)
    nb = alert_message("peak_risk", "nb", projected_kwh=5.24, cap_kw=5.0)
    assert "5.2 kWh" in en and "kWh" in nb and en != nb
    assert "17:00–20:00" in alert_message("extreme_price_tomorrow", "nb", hours=["17:00", "20:00"])
    assert "Stue" in alert_message("model_poor", "en", zone_name="Stue")


# --------------------------------------------------------------------------- in HA


def _hourly(now: datetime) -> dict[datetime, float]:
    out = {}
    t = now.replace(day=1, hour=0, minute=0)
    while t < now.replace(minute=0):
        out[t] = 4.5 if t.hour == 18 else 1.0
        t += timedelta(hours=1)
    return out


async def test_home_sensors_without_capacity_limiting(hass: HomeAssistant, setup_states) -> None:
    coord = await _setup(hass, _entry(FIXED))
    price = hass.states.get("sensor.hjem_effective_electricity_price")
    assert price is not None and float(price.state) == 0.5
    assert price.attributes["unit_of_measurement"] == "NOK/kWh"
    assert price.attributes["tariff"] == coord.export()["tariff"]["label"]
    month = hass.states.get("sensor.hjem_savings_this_month")
    assert month is not None and month.attributes["device_class"] == "monetary"
    assert month.attributes["state_class"] == "total"
    assert hass.states.get("sensor.hjem_savings_today") is not None
    # capacity sensors only exist when capacity limiting is configured
    assert hass.states.get("sensor.hjem_capacity_tariff_step") is None
    assert coord.export()["savings"]["month"] == 0.0


async def test_capacity_sensors_headroom_and_load(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    now = dt_util.start_of_local_day() + timedelta(hours=10, minutes=30)
    freezer.move_to(now)
    hass.states.async_set("sensor.house_power", "2000", {"unit_of_measurement": "W", "device_class": "power"})
    with patch("custom_components.heat_planner.coordinator.async_house_hourly_kwh", return_value=_hourly(now)):
        coord = await _setup(hass, _entry(PEAK))
    step = hass.states.get("sensor.hjem_capacity_tariff_step")
    assert step.state == coord.peak_state.step_label and "–" in step.state
    assert step.attributes["cap_kw_today"] == coord.peak_state.cap_kw
    assert step.attributes["top3_kw"] == coord.peak_state.top3_kw
    avg = hass.states.get("sensor.hjem_monthly_peak_average")
    assert float(avg.state) == coord.peak_state.month_avg_kw
    assert avg.attributes["unit_of_measurement"] == "kW"

    # the meter has been seen once (at setup): 2 kW to the end of the hour ≈ 1 kWh
    projected = coord.projected_hour_kwh()
    assert projected is not None and abs(projected - 1.0) < 0.05
    head = hass.states.get("sensor.hjem_peak_headroom_this_hour")
    assert abs(float(head.state) - round(coord.peak_state.cap_kw - projected, 2)) < 0.02

    # the house load for the card: last 12 measured hours + the plan horizon, current hour live
    load = coord.export()["peak"]["load"]
    assert sum(1 for x in load if x.get("measured")) == 12
    live = [x for x in load if x.get("live")]
    assert len(live) == 1 and live[0]["start"] == now.replace(minute=0).isoformat()
    planned = [x for x in load if not x.get("measured") and not x.get("live")]
    assert len(planned) >= 24 and all(x["kw"] >= 0 for x in planned)

    # without data: unavailable
    coord.peak_state = None
    coord.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.hjem_capacity_tariff_step").state == "unavailable"


async def test_realised_savings_across_hours_restart_and_month(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    start = dt_util.start_of_local_day().replace(day=1) + timedelta(days=27, hours=22, minutes=5)
    freezer.move_to(start)
    values = iter([1.25, 0.5, -0.25, 2.0, 3.0, 4.0])
    with patch.object(HeatPlannerCoordinator, "_hour_savings", side_effect=lambda hour: next(values)):
        entry = _entry(FIXED)
        coord = await _setup(hass, entry)
        assert coord.state["savings"]["pending"]["amount"] == 1.25
        # later in the same hour: nothing new
        freezer.move_to(start + timedelta(minutes=20))
        await coord.async_refresh()
        assert coord.state["savings"]["pending"]["amount"] == 1.25
        freezer.move_to(start + timedelta(hours=1))
        await coord.async_refresh()
        await hass.async_block_till_done()
        assert float(hass.states.get("sensor.hjem_savings_this_month").state) == 1.25
        assert float(hass.states.get("sensor.hjem_savings_today").state) == 1.25

        # restart: the hour in progress and the totals come back from the Store
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        coord = entry.runtime_data
        assert coord.state["savings"]["month_total"] == 1.25
        assert coord.state["savings"]["pending"]["amount"] == 0.5
        assert float(hass.states.get("sensor.hjem_savings_this_month").state) == 1.25

        # past midnight: today restarts, the month keeps counting (incl. the hour before midnight)
        freezer.move_to(start + timedelta(hours=2))
        await coord.async_refresh()
        await hass.async_block_till_done()
        assert float(hass.states.get("sensor.hjem_savings_this_month").state) == 1.75
        assert float(hass.states.get("sensor.hjem_savings_today").state) == 0.0

        # into the next month: starts from zero
        nxt = (start.replace(day=28) + timedelta(days=5)).replace(day=1, hour=3, minute=5)
        freezer.move_to(nxt)
        await coord.async_refresh()
        await hass.async_block_till_done()
        assert float(hass.states.get("sensor.hjem_savings_this_month").state) == 0.0
        assert coord.state["savings"]["month"] == f"{nxt.year:04d}-{nxt.month:02d}"


async def test_hour_savings_is_baseline_minus_plan(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=8, minutes=5))  # pricey morning
    coord = await _setup(hass, _entry({"contract": "spot", "grid": "none", "peak_limit": False}))
    hour = dt_util.now().replace(minute=0, second=0, microsecond=0)
    value = coord._hour_savings(hour)
    assert value is not None
    base = coord.plan_zone("stue", hours=1, method="rules", start=hour)
    assert abs(value - (base.baseline_cost - coord.plans["stue"].hours[0].cost)) < 1e-9
    # a zone under manual control does not count
    coord.zone_state("stue")["auto"] = False
    assert coord._hour_savings(hour) is None


async def test_peak_risk_alert_rate_limited_and_notified(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    now = dt_util.start_of_local_day() + timedelta(hours=10, minutes=30)
    freezer.move_to(now)
    hass.states.async_set("sensor.house_power", "1000", {"unit_of_measurement": "W", "device_class": "power"})
    notify = async_mock_service(hass, "notify", "mobile_app_x")
    events = async_capture_events(hass, EVENT_ALERT)
    entry = _entry(PEAK)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={
        **entry.options, "alerts": {"notify_service": "notify.mobile_app_x", "kinds": ["peak_risk"]},
    })
    with patch("custom_components.heat_planner.coordinator.async_house_hourly_kwh", return_value=_hourly(now)):
        async_mock_service(hass, "climate", "set_temperature")
        async_mock_service(hass, "climate", "set_hvac_mode")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coord = entry.runtime_data
        assert not [e for e in events if e.data["kind"] == "peak_risk"]
        cap = coord.peak_state.cap_kw
        # a big load comes on: the hour heads over the cap
        for i in range(3):
            freezer.tick(timedelta(seconds=90))
            hass.states.async_set("sensor.house_power", str(int((cap * 2.5 + i) * 1000)), {"unit_of_measurement": "W"})
            await hass.async_block_till_done()
        risk = [e for e in events if e.data["kind"] == "peak_risk"]
        assert len(risk) == 1  # once per hour, however often the meter reports
        assert risk[0].data["entry_id"] == entry.entry_id and risk[0].data["cap_kw"] == cap
        assert risk[0].data["projected_kwh"] > cap - 0.3 and risk[0].data["message"]
        assert len(notify) == 1 and notify[0].data["message"] == risk[0].data["message"]
        # the next hour may alert again
        freezer.move_to(now.replace(minute=0) + timedelta(hours=1, minutes=40))
        hass.states.async_set("sensor.house_power", str(int(cap * 3000)), {"unit_of_measurement": "W"})
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=90))
        hass.states.async_set("sensor.house_power", str(int(cap * 3000) + 1), {"unit_of_measurement": "W"})
        await hass.async_block_till_done()
        assert len([e for e in events if e.data["kind"] == "peak_risk"]) == 2


async def test_extreme_tomorrow_once_per_day_and_kind_filter(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=13, minutes=10))
    notify = async_mock_service(hass, "notify", "mobile_app_x")
    events = async_capture_events(hass, EVENT_ALERT)
    entry = _entry(FIXED)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={
        **entry.options, "alerts": {"notify_service": "notify.mobile_app_x", "kinds": ["model_poor"]},
    })
    coord = await _setup(hass, entry)
    assert not events
    tomorrow = dt_util.start_of_local_day() + timedelta(days=1)
    prices = {tomorrow + timedelta(hours=i): 1.0 for i in range(24)}
    prices[tomorrow + timedelta(hours=17)] = 6.0
    prices[tomorrow + timedelta(hours=18)] = 6.5
    coord.price_hours = {**coord.price_hours, **prices}
    coord._check_extreme_tomorrow()
    coord._check_extreme_tomorrow()
    await hass.async_block_till_done()
    ext = [e for e in events if e.data["kind"] == "extreme_price_tomorrow"]
    assert len(ext) == 1
    assert ext[0].data["hours"] == ["17:00", "19:00"] and ext[0].data["max_price"] == 6.5
    assert ext[0].data["date"] == tomorrow.date().isoformat()
    assert not notify  # this kind is not selected for notifications
    # survives a restart (persisted), so no second alert for the same day
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    coord.price_hours = {**coord.price_hours, **prices}
    coord._check_extreme_tomorrow()
    assert len([e for e in events if e.data["kind"] == "extreme_price_tomorrow"]) == 1


async def test_model_poor_alert_once(hass: HomeAssistant, setup_states) -> None:
    hass.config.language = "nb"
    notify = async_mock_service(hass, "notify", "mobile_app_x")
    events = async_capture_events(hass, EVENT_ALERT)
    entry = _entry(FIXED)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "alerts": {"notify_service": "notify.mobile_app_x"}})
    coord = await _setup(hass, entry)
    poor = ThermalModel(learned=True, rmse=5.0)
    good = ThermalModel(learned=True, rmse=0.05)
    coord._check_model("stue", "Stue", poor)
    coord._check_model("stue", "Stue", poor)
    await hass.async_block_till_done()
    got = [e for e in events if e.data["kind"] == "model_poor"]
    assert len(got) == 1 and got[0].data["zone"] == "stue" and "Stue" in got[0].data["message"]
    assert "Varmemodellen" in got[0].data["message"]  # Norwegian from hass.config.language
    assert len(notify) == 1  # all kinds by default
    # recovers, then turns poor again: one more
    coord._check_model("stue", "Stue", good)
    coord._check_model("stue", "Stue", poor)
    assert len([e for e in events if e.data["kind"] == "model_poor"]) == 2


async def test_alerts_options_step(hass: HomeAssistant, setup_states) -> None:
    async_mock_service(hass, "notify", "mobile_app_x")
    entry = _entry(FIXED)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "alerts" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "alerts"})
    assert result["step_id"] == "alerts"
    bad = await hass.config_entries.options.async_configure(result["flow_id"], {"notify_service": "light.kitchen", "kinds": ["peak_risk"]})
    assert bad["errors"] == {"notify_service": "invalid_notify"}
    done = await hass.config_entries.options.async_configure(bad["flow_id"], {"notify_service": "notify.mobile_app_x", "kinds": ["peak_risk", "model_poor"]})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["alerts"] == {"notify_service": "notify.mobile_app_x", "kinds": ["peak_risk", "model_poor"]}
