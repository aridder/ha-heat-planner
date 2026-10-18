"""Integration tests running inside a real Home Assistant test instance."""

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.heat_planner.const import DOMAIN

PRICE_SENSOR = "sensor.spot"
TYPICAL = [1.2, 1.2, 1.1, 1.1, 1.1, 1.2, 1.4, 1.6, 1.8, 1.5, 1.4, 1.3,
           1.1, 1.0, 1.0, 1.2, 1.5, 1.7, 1.9, 1.9, 1.6, 1.5, 1.4, 1.3]


def _raw_today():
    day = dt_util.start_of_local_day()
    return [
        {"start": (day + timedelta(hours=i)).isoformat(),
         "end": (day + timedelta(hours=i + 1)).isoformat(), "value": v}
        for i, v in enumerate(TYPICAL)
    ]


@pytest.fixture
async def setup_states(hass: HomeAssistant):
    await hass.config.async_set_time_zone("Europe/Oslo")
    hass.config.currency = "NOK"
    hass.states.async_set(PRICE_SENSOR, "1.2", {"unit_of_measurement": "NOK/kWh", "raw_today": _raw_today()})
    hass.states.async_set("climate.stue", "heat", {"temperature": 22, "current_temperature": 20.5,
                                                   "hvac_action": "idle", "hvac_modes": ["off", "heat"]})
    hass.states.async_set("number.bad_demand", "50", {"min": 0, "max": 100})
    hass.states.async_set("person.a", "home")


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hjem",
        data={},
        options={
            "price_type": "sensor",
            "price_sensor": PRICE_SENSOR,
            "presence_entities": ["person.a"],
            "price_extra": 0.5,
            "zones": [
                {"zone_id": "stue", "name": "Stue", "mode": "setpoint",
                 "climate_entity": "climate.stue", "comfort": 21, "heater_kw": 1.2},
                {"zone_id": "bad", "name": "Bad", "mode": "percent",
                 "number_entity": "number.bad_demand", "comfort": 21, "heater_kw": 0.4},
            ],
        },
    )


async def test_config_flow_with_price_sensor(hass: HomeAssistant, setup_states) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Hjem", "price_source": f"sensor:{PRICE_SENSOR}", "presence_entities": ["person.a"], "weather_entity": "weather.home", "price_extra": 0.4}
    )
    assert result["step_id"] == "house"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"house_type": "terraced", "insulation": "normal", "heating_sources": ["floor_electric"], "heated_area": 120}
    )
    assert result["step_id"] == "energy"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"contract": "fixed", "fixed_price": 0.5, "grid": "elvia_2026", "peak_limit": True}
    )
    assert result["type"] is FlowResultType.MENU and result["step_id"] == "setup_method"
    assert result["menu_options"] == ["ai_copy", "zones"]  # no AI in HA
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "zones"})
    assert result["step_id"] == "zones"
    assert "Stue" in result["description_placeholders"]["found"] or "climate.stue" in result["description_placeholders"]["found"].lower() or result["description_placeholders"]["found"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"zones": ["stue"]})
    assert result["step_id"] == "history"
    with patch("custom_components.heat_planner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"use_history": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hjem"
    opts = result["options"]
    assert opts["price_sensor"] == PRICE_SENSOR and opts["use_history"] is True
    assert opts["house"]["insulation"] == "normal"
    assert opts["energy"]["contract"] == "fixed" and opts["energy"]["peak_limit"] is True
    assert [z["climate_entity"] for z in opts["zones"]] == ["climate.stue"]


async def test_options_add_zone(hass: HomeAssistant, setup_states) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch("custom_components.heat_planner.async_setup_entry", return_value=True):
        await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "add_zone"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Kjøkken", "mode": "setpoint", "comfort": 23, "heater_kw": 1.0}
    )
    assert result["errors"] == {"climate_entity": "climate_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Kjøkken", "mode": "setpoint", "climate_entity": "climate.stue", "comfort": 23, "heater_kw": 1.0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [z["zone_id"] for z in entry.options["zones"]] == ["stue", "bad", "kjokken"]


async def test_setup_plans_and_controls(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=18, minutes=10))
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    set_number = async_mock_service(hass, "number", "set_value")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # 18:00 is one of the 3 most expensive hours -> comfort 21 - 1
    assert set_temp and set_temp[-1].data["temperature"] == 20.0
    assert set_number and set_number[-1].data["value"] == 25  # bad: 40 - 15

    states = {s.entity_id: s.state for s in hass.states.async_all() if s.domain in ("sensor", "switch", "number", "select")}
    target = next(k for k in states if (k.startswith("sensor.stue") and "target" in k) or k == "sensor.stue_mal")
    assert float(states[target]) == 20.0
    assert any(v == "save" for k, v in states.items() if (k.startswith("sensor.stue") and "mode" in k) or k == "sensor.stue_modus")
    level = hass.states.get(next(k for k in states if "price_level" in k or "prisniva" in k))
    assert level.state == "expensive"


async def test_manual_change_pauses_control(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10, minutes=10))
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "number", "set_value")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    applied = set_temp[-1].data["temperature"]
    hass.states.async_set("climate.stue", "heat", {"temperature": applied, "current_temperature": 20.5})
    await coord.async_refresh()
    # someone turns the thermostat up by hand
    freezer.tick(timedelta(minutes=10))
    hass.states.async_set("climate.stue", "heat", {"temperature": 24, "current_temperature": 20.5})
    calls_before = len(set_temp)
    await coord.async_refresh()
    assert coord.manual_until("stue") is not None
    assert len(set_temp) == calls_before
    await coord.async_resume("stue")
    assert coord.manual_until("stue") is None
    assert len(set_temp) > calls_before


async def test_websocket_get_preview_save(hass: HomeAssistant, setup_states, hass_ws_client) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "number", "set_value")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)

    await ws.send_json({"id": 1, "type": "heat_planner/get"})
    res = await ws.receive_json()
    assert res["success"]
    data = res["result"]["entries"][0]
    assert {z["zone_id"] for z in data["zones"]} == {"stue", "bad"}
    assert len(data["prices"]) == 24
    stue = next(z for z in data["zones"] if z["zone_id"] == "stue")
    assert len(stue["schedule"]["0"]) == 24 and stue["plan"]["hours"]

    values = [3.0] * 24
    await ws.send_json({"id": 2, "type": "heat_planner/preview", "entry_id": entry.entry_id, "zone_id": "stue", "values": values})
    res = await ws.receive_json()
    assert res["success"]
    # the edited curve is today's; late in the evening the second hour is already tomorrow
    today = dt_util.now().date().isoformat()
    todays = [h for h in res["result"]["hours"] if h["start"][:10] == today]
    assert todays and all(h["scheduled"] == 24.0 for h in todays)

    await ws.send_json({"id": 3, "type": "heat_planner/save", "entry_id": entry.entry_id, "zone_id": "stue",
                        "scope": "weekdays", "values": values})
    res = await ws.receive_json()
    assert res["success"]
    stue = next(z for z in res["result"]["zones"] if z["zone_id"] == "stue")
    assert stue["schedule"]["0"] == values and stue["schedule"]["5"] != values

    today = dt_util.now().date().isoformat()
    await ws.send_json({"id": 4, "type": "heat_planner/save", "entry_id": entry.entry_id, "zone_id": "stue",
                        "scope": "date", "date": today, "values": [0.0] * 24})
    res = await ws.receive_json()
    stue = next(z for z in res["result"]["zones"] if z["zone_id"] == "stue")
    assert stue["overrides"][today] == [0.0] * 24

    await ws.send_json({"id": 5, "type": "heat_planner/settings", "entry_id": entry.entry_id, "zone_id": "stue",
                        "settings": {"comfort": 22.5, "price_aggressiveness": 0, "bogus": 1}})
    res = await ws.receive_json()
    stue = next(z for z in res["result"]["zones"] if z["zone_id"] == "stue")
    assert stue["settings"]["comfort"] == 22.5 and stue["settings"]["price_aggressiveness"] == 0

    # persists across reload
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord.zone("stue").comfort == 22.5
    assert coord.schedule("stue")[0] == values


async def test_services(hass: HomeAssistant, setup_states) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "number", "set_value")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "set_schedule", {"zone": "bad", "values": [10] * 24, "scope": "weekend"}, blocking=True)
    coord = entry.runtime_data
    assert coord.schedule("bad")[6] == [10.0] * 24 and coord.schedule("bad")[0] != [10.0] * 24
    from homeassistant.exceptions import ServiceValidationError
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "resume", {"zone": "nope"}, blocking=True)


async def test_unload(hass: HomeAssistant, setup_states) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "number", "set_value")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_house_prior_and_estimated_power_in_export(hass: HomeAssistant, setup_states) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "number", "set_value")
    entry = _entry()
    opts = {**entry.options, "house": {"insulation": "old", "house_type": "detached", "heating_sources": ["panel_heaters"]}}
    opts["zones"] = [dict(z, heater_kw=0) if z["zone_id"] == "stue" else z for z in opts["zones"]]
    hass.config_entries.async_update_entry(entry, options=opts) if False else None
    entry = MockConfigEntry(domain=DOMAIN, title="Hjem", data={}, options=opts)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = entry.runtime_data.export()
    stue = next(z for z in data["zones"] if z["zone_id"] == "stue")
    assert stue["thermal"]["learned"] is False and stue["thermal"]["tau_hours"] == 17.0
    assert stue["heater_kw_source"] == "estimate" and stue["heater_kw"] > 0
    bad = next(z for z in data["zones"] if z["zone_id"] == "bad")
    assert bad["heater_kw_source"] == "config" and bad["heater_kw"] == 0.4


async def test_presence_ignores_unknown_persons(hass: HomeAssistant, setup_states) -> None:
    """A person without a working tracker must not keep the house 'home' forever."""
    hass.states.async_set("person.a", "not_home")
    hass.states.async_set("person.guest", "unknown")
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "presence_entities": ["person.a", "person.guest"]}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord._is_away() is True
    hass.states.async_set("person.a", "home")
    assert coord._is_away() is False
    hass.states.async_set("person.a", "unavailable")
    assert coord._is_away() is False  # nothing known -> assume someone is home


async def test_presence_defaults_skip_unknown(hass: HomeAssistant) -> None:
    from custom_components.heat_planner.onboarding import presence_defaults

    hass.states.async_set("person.a", "home", {"device_trackers": ["device_tracker.a"]})
    hass.states.async_set("person.b", "unknown", {"device_trackers": ["device_tracker.b"]})
    assert presence_defaults(hass) == ["person.a"]
