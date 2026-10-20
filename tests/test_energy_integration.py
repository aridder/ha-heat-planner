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
    from unittest.mock import patch

    now = dt_util.start_of_local_day() + timedelta(hours=10, minutes=5)
    freezer.move_to(now)
    hass.states.async_set("sensor.house_power", "2500", {"unit_of_measurement": "W", "device_class": "power"})
    # rooms at comfort, big heaters: just holding them would exceed the cap. Cuts may take a
    # room at most PEAK_SLACK below its schedule (a recovering room is not cut: at full power
    # until the setpoint, a 0.5 °C cut saves nothing and a deeper one costs comfort)
    hass.states.async_set("climate.kjokken", "heat", {"temperature": 23, "current_temperature": 23.0, "hvac_modes": ["off", "heat"]})
    hass.states.async_set("climate.stue", "heat", {"temperature": 23, "current_temperature": 23.0, "hvac_modes": ["off", "heat"]})
    zones = [
        {"zone_id": "stue", "name": "Stue", "mode": "setpoint", "climate_entity": "climate.stue", "comfort": 23, "heater_kw": 6.0},
        {"zone_id": "kjokken", "name": "Kjøkken", "mode": "setpoint", "climate_entity": "climate.kjokken", "comfort": 23, "heater_kw": 6.0, "priority": 1},
    ]
    # this month: three days at ~4.5 kWh/h -> step 2–5 kW, cap ≈ 4.7 kW; base load ~1 kW
    hourly = {}
    start = now.replace(day=1, hour=0, minute=0)
    t = start
    while t < now.replace(minute=0):
        hourly[t] = 4.5 if t.hour == 18 else 1.0
        t += timedelta(hours=1)
    energy = {"contract": "fixed", "fixed_price": 0.5, "grid": "none", "peak_limit": True, "house_power_sensor": "sensor.house_power"}
    with patch("custom_components.heat_planner.coordinator.async_house_hourly_kwh", return_value=hourly):
        coord = await _setup(hass, _entry(energy, zones))
        state = coord.export()["peak"]
        assert state is not None and state["metered"] is True and state["cap_kw"] < 6
        # the protected warm-up window (13–15) runs both rooms flat out today, so cuts
        # elsewhere today cannot lower today's peak and are dropped again
        today = now.date()
        assert not any(R_PEAK in h.reasons for h in coord.plans["stue"].hours if h.start.date() == today)
        # without a protected window: the optimiser plans the budget with stored heat only –
        # an occupied room is never planned below its schedule for the capacity step
        await coord.async_set_settings("stue", {"protected_from_hour": 0, "protected_to_hour": 0})
        await coord.async_set_settings("kjokken", {"protected_from_hour": 0, "protected_to_hour": 0})
        await hass.async_block_till_done()
        for p in coord.plans.values():
            assert all(h.target is not None and h.target >= h.scheduled for h in p.hours
                       if h.start.hour >= 6 and R_PEAK in h.reasons)
        # the rules give way in the lowest-priority room (at most PEAK_SLACK)
        await coord.async_set_settings(None, {"optimize": False})
        await coord.async_refresh()
        await hass.async_block_till_done()
        assert coord.plans["stue"].method == "rules"
    stue, kjokken = coord.plans["stue"], coord.plans["kjokken"]
    assert any(R_PEAK in h.reasons for h in stue.hours)
    # never below the safety floor, whatever was cut – nor more than PEAK_SLACK below the schedule
    assert all(h.target is None or h.target >= 18 for p in (stue, kjokken) for h in p.hours)
    assert all(h.target >= h.scheduled - 0.5 for h in stue.hours if R_PEAK in h.reasons)


async def test_no_meter_no_peak_limiting(hass: HomeAssistant, setup_states) -> None:
    coord = await _setup(hass, _entry({"contract": "fixed", "grid": "none", "peak_limit": True}))
    assert coord.export()["peak"] is None


async def test_feedback_and_auto_arrival(hass: HomeAssistant, setup_states, hass_ws_client, freezer: FrozenDateTimeFactory) -> None:
    from custom_components.heat_planner.occupancy import ArrivalModel

    client = await hass_ws_client(hass)
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=11))
    entry = _entry({"contract": "fixed", "fixed_price": 0.5, "grid": "none", "peak_limit": False})
    entry = MockConfigEntry(domain=DOMAIN, title="Hjem", data={}, options={**entry.options, "use_history": True})
    coord = await _setup(hass, entry)
    before = coord.zone("stue").comfort
    await client.send_json({"id": 1, "type": f"{DOMAIN}/feedback", "entry_id": entry.entry_id, "zone": "stue", "kind": "too_cold"})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["message"]
    await hass.async_block_till_done()
    assert coord.zone("stue").comfort == before + 0.5
    assert coord.export()["zones"][0]["feedback"][-1]["kind"] == "too_cold"

    # "auto" warm-up hour follows the learned arrival
    today = dt_util.now().date()
    obs = {wd: [] for wd in range(7)}
    for i in range(1, 6):
        d = today - timedelta(days=7 * i)
        obs[d.weekday()].append((d.isoformat(), 16 * 60 + 30))
    coord.state["arrivals"] = ArrivalModel(observations=obs, updated=dt_util.now().isoformat()).as_dict()
    await coord.async_set_settings("stue", {"away_until_hour": -1})
    zone = coord.zone("stue")
    assert 10 <= zone.away_until_hour <= 16  # arrival minus the time a slow floor needs
    exp = coord.export()
    assert exp["arrival"]["expected"] == "16:30"
    assert exp["zones"][0]["settings"]["away_until_hour"] == -1


async def test_feedback_is_bounded(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=11))
    coord = await _setup(hass, _entry({"contract": "fixed", "grid": "none", "peak_limit": False}))
    start = coord.zone("stue").comfort
    for _ in range(12):
        freezer.tick(timedelta(minutes=11))
        await coord.async_feedback("stue", "too_warm")
    comfort = coord.zone("stue").comfort
    assert comfort >= start - 2.0 and comfort >= coord.zone("stue").safety_min_temp + 0.5
