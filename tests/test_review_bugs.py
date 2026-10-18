"""Regression tests proving bugs found in review (expected to FAIL until fixed)."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.heat_planner.const import DOMAIN
from custom_components.heat_planner.planner import (
    PriceRules,
    ZoneConfig,
    build_plan,
    default_schedule,
)

from .test_integration import _entry, setup_states  # noqa: F401


async def _setup(hass):
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    set_mode = async_mock_service(hass, "climate", "set_hvac_mode")
    async_mock_service(hass, "number", "set_value")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, set_temp, set_mode


async def test_manual_pause_never_ends(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10, minutes=10))
    entry, set_temp, _ = await _setup(hass)
    coord = entry.runtime_data
    applied = set_temp[-1].data["temperature"]
    hass.states.async_set("climate.stue", "heat", {"temperature": applied, "current_temperature": 20.5})
    freezer.tick(timedelta(minutes=10))
    hass.states.async_set("climate.stue", "heat", {"temperature": 24, "current_temperature": 20.5})
    await coord.async_refresh()
    until = coord.manual_until("stue")
    assert until is not None
    # time passes beyond the pause
    freezer.move_to(until + timedelta(minutes=5))
    await coord.async_refresh()
    # BUG: pause is re-armed because _applied still holds the pre-manual value
    assert coord.manual_until("stue") is None, "manual pause re-armed forever"


async def test_user_turning_thermostat_off_is_overridden(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10, minutes=10))
    entry, _, set_mode = await _setup(hass)
    coord = entry.runtime_data
    freezer.tick(timedelta(minutes=10))
    hass.states.async_set("climate.stue", "off", {"temperature": None, "current_temperature": 20.5})
    await coord.async_refresh()
    # BUG: no manual detection when current is None (off) -> integration turns heat back on
    assert not [c for c in set_mode if c.data["hvac_mode"] == "heat"], "fought the user's OFF"


async def test_service_days_from_ui_selector(hass: HomeAssistant, setup_states) -> None:
    entry, _, _ = await _setup(hass)
    # services.yaml select selector sends strings "0".."6"
    await hass.services.async_call(
        DOMAIN, "set_schedule",
        {"zone": "bad", "values": [10] * 24, "scope": "days", "days": ["0"]}, blocking=True,
    )


async def test_subscription_survives_reload(hass: HomeAssistant, setup_states, hass_ws_client) -> None:
    entry, _, _ = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "heat_planner/subscribe"})
    assert (await ws.receive_json())["success"]
    await ws.receive_json()  # initial push
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    msg = await ws.receive_json(timeout=1)  # BUG: times out, listener is on the old coordinator
    assert msg["type"] == "event"


async def test_mode_change_resets_schedule(hass: HomeAssistant, setup_states) -> None:
    entry, set_temp, _ = await _setup(hass)
    coord = entry.runtime_data
    assert coord.schedule("bad")[0][6] == 70.0
    zones = [dict(z) for z in entry.options["zones"]]
    for z in zones:
        if z["zone_id"] == "bad":
            z["mode"] = "setpoint"
            z["climate_entity"] = "climate.stue"
    hass.config_entries.async_update_entry(entry, options={**entry.options, "zones": zones})
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord.schedule("bad")[0][6] == 1.0  # setpoint default offsets, not 70
    assert coord.plans["bad"].hours[0].target < 25


async def test_disabling_auto_restores_heat_after_extreme_off(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    from custom_components.heat_planner.prices import parse_price_list  # noqa: F401
    vals = [1.2] * 24
    vals[18] = 4.5
    day = dt_util.start_of_local_day()
    hass.states.async_set("sensor.spot", "1.2", {"unit_of_measurement": "NOK/kWh", "raw_today": [
        {"start": (day + timedelta(hours=i)).isoformat(), "value": v} for i, v in enumerate(vals)]})
    freezer.move_to(day + timedelta(hours=18, minutes=10))
    entry, set_temp, set_mode = await _setup(hass)
    coord = entry.runtime_data
    assert set_mode and set_mode[-1].data["hvac_mode"] == "off"
    hass.states.async_set("climate.stue", "off", {"current_temperature": 20.5})
    await coord.async_set_settings("stue", {"auto": False})
    assert set_mode[-1].data["hvac_mode"] == "heat"
    assert set_temp[-1].data["temperature"] == 21.0


def test_extreme_off_is_capped_at_max_off_hours() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Oslo")
    day = datetime(2026, 10, 19, tzinfo=tz)
    prices = {day + timedelta(hours=i): (5.0 if 15 <= i < 21 else 1.0) for i in range(24)}
    zone = ZoneConfig(zone_id="z", name="z", comfort=21.0, protected_from_hour=13, protected_to_hour=14)
    plan = build_plan(zone, default_schedule("setpoint"), {}, prices, PriceRules(), day, 24)
    offs = [h.start.hour for h in plan.hours if h.target is None]
    assert offs == [15, 16]
    assert plan.hours[17].target == 20.0 and plan.hours[17].mode == "save"


def test_protected_window_wraps_midnight() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Oslo")
    day = datetime(2026, 10, 19, tzinfo=tz)
    prices = {day + timedelta(hours=i): (5.0 if i in (23, 1) else 1.0) for i in range(24)}
    zone = ZoneConfig(zone_id="z", name="z", comfort=21.0, protected_from_hour=22, protected_to_hour=6)
    plan = build_plan(zone, default_schedule("setpoint"), {}, prices, PriceRules(), day, 24)
    assert plan.hours[1].target is not None and plan.hours[23].target is not None
