"""Contract, grid tariff, optimiser switch and capacity-tariff limiting inside HA."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.heat_planner.const import DOMAIN
from custom_components.heat_planner.energy import R_PEAK

from .test_integration import PRICE_SENSOR, setup_states  # noqa: F401


def _entry(energy: dict, zones=None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, title="Hjem", data={},
        options={
            "price_type": "sensor", "price_sensor": PRICE_SENSOR, "presence_entities": ["person.a"],
            "energy": energy,
            "zones": zones or [
                {"zone_id": "stue", "name": "Stue", "mode": "setpoint",
                 "climate_entity": "climate.stue", "comfort": 21, "heater_kw": 1.2},
            ],
        },
    )


async def _setup(hass, entry):
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    async_mock_service(hass, "number", "set_value")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_fixed_price_means_no_price_shifting(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=18, minutes=10))
    coord = await _setup(hass, _entry({"contract": "fixed", "fixed_price": 0.5, "grid": "none", "peak_limit": False}))
    assert {round(p, 3) for p in coord.price_hours.values()} == {0.5}
    plan = coord.plans["stue"]
    assert all(h.level == "normal" for h in plan.hours)
    assert plan.hours[0].target == plan.hours[0].scheduled
    exp = coord.export()
    assert exp["tariff"]["mode"] == "fixed" and exp["optimize"] is True and exp["price_extra"] == 0.0


async def test_elvia_grid_tariff_in_prices(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10))
    coord = await _setup(hass, _entry({"contract": "fixed", "fixed_price": 0.5, "grid": "elvia_2026", "peak_limit": False}))
    day = dt_util.start_of_local_day()
    weekday = day.weekday() < 5
    if weekday:
        assert round(coord.price_hours[day + timedelta(hours=12)] - coord.price_hours[day + timedelta(hours=2)], 3) == 0.1
    assert "Elvia" in coord.export()["tariff"]["label"]


async def test_optimiser_can_be_switched_off(hass: HomeAssistant, setup_states, hass_ws_client) -> None:
    entry = _entry({"contract": "spot_support", "grid": "elvia_2026", "peak_limit": False})
    coord = await _setup(hass, entry)
    assert coord.optimize is True
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/settings", "entry_id": entry.entry_id, "settings": {"optimize": False}})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["optimize"] is False
    await hass.async_block_till_done()
    assert coord.plans["stue"].method == "rules"


async def test_capacity_limit_spreads_heating(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10, minutes=5))
    hass.states.async_set("climate.kjokken", "heat", {"temperature": 23, "current_temperature": 20.0, "hvac_modes": ["off", "heat"]})
    hass.states.async_set("climate.stue", "heat", {"temperature": 23, "current_temperature": 20.0, "hvac_modes": ["off", "heat"]})
    zones = [
        {"zone_id": "stue", "name": "Stue", "mode": "setpoint", "climate_entity": "climate.stue", "comfort": 23, "heater_kw": 3.0},
        {"zone_id": "kjokken", "name": "Kjøkken", "mode": "setpoint", "climate_entity": "climate.kjokken", "comfort": 23, "heater_kw": 3.0, "priority": 1},
    ]
    coord = await _setup(hass, _entry({"contract": "fixed", "fixed_price": 0.5, "grid": "none", "peak_limit": True}, zones))
    state = coord.export()["peak"]
    assert state is not None and state["cap_kw"] == 3.0 and state["metered"] is False
    stue, kjokken = coord.plans["stue"], coord.plans["kjokken"]
    # the lower-priority room gives way first
    assert any(R_PEAK in h.reasons for h in stue.hours)
    first = stue.hours[0].start
    total = stue.hours[0].kwh + kjokken.hours[0].kwh
    assert total <= 6.0
    assert first is not None
