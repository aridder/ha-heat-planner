"""Config, reconfigure and options flows: every step and error."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heat_planner.config_flow import _current_choice, energy_defaults
from custom_components.heat_planner.const import DOMAIN
from custom_components.heat_planner.onboarding import OTHER_SENSOR, SETUP_NORDPOOL

from .test_integration import PRICE_SENSOR, _entry, setup_states  # noqa: F401

NO_SETUP = "custom_components.heat_planner.async_setup_entry"


async def _loaded(hass: HomeAssistant) -> MockConfigEntry:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(NO_SETUP, return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def _options(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> dict[str, Any]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert step in result["menu_options"]
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})


async def test_user_flow_other_sensor_and_no_thermostats(hass: HomeAssistant) -> None:
    hass.states.async_set(PRICE_SENSOR, "1", {"unit_of_measurement": "NOK/kWh"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Hytta", "price_source": OTHER_SENSOR, "weather_entity": "weather.x"}
    )
    assert result["step_id"] == "price_sensor"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"price_sensor": PRICE_SENSOR})
    assert result["step_id"] == "house"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    # no thermostats at all: the AI/menu step is skipped
    assert result["step_id"] == "history"
    with patch(NO_SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"use_history": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hytta"
    assert result["options"]["price_type"] == "sensor" and result["options"]["zones"] == []


async def test_reconfigure_changes_name_and_price(hass: HomeAssistant, setup_states) -> None:
    entry = await _loaded(hass)
    hass.states.async_set("sensor.other", "1", {"unit_of_measurement": "NOK/kWh", "raw_today": [{"start": "x", "value": 1}]})
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    with patch(NO_SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"name": "Huset", "price_source": "sensor:sensor.other", "presence_entities": ["person.a"],
             "weather_entity": "weather.home", "price_extra": 0.3},
        )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.title == "Huset"
    assert entry.options["price_sensor"] == "sensor.other" and entry.options["price_extra"] == 0.3
    assert len(entry.options["zones"]) == 2  # rooms untouched


async def test_reconfigure_other_sensor_and_nordpool_error(hass: HomeAssistant, setup_states) -> None:
    entry = await _loaded(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(
        "custom_components.heat_planner.config_flow.async_setup_nordpool",
        return_value=(None, "NO1", "nordpool_failed"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": "Hjem", "price_source": SETUP_NORDPOOL, "weather_entity": "weather.home"}
        )
    assert result["errors"] == {"price_source": "nordpool_failed"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Hjem", "price_source": OTHER_SENSOR, "weather_entity": "weather.home"}
    )
    assert result["step_id"] == "price_sensor"
    with patch(NO_SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"price_sensor": "sensor.x"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.options["price_type"] == "sensor" and entry.options["price_sensor"] == "sensor.x"


async def test_ai_review_declined_goes_to_zones(hass: HomeAssistant, setup_states) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Hjem", "price_source": f"sensor:{PRICE_SENSOR}", "weather_entity": "weather.x"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "ai_copy"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"routine": "Hjemme hele dagen"})
    answer = '{"zones": [{"climate_entity": "climate.stue", "name": "Stue", "comfort": 21}]}'
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"prompt": "x", "answer": answer})
    assert result["step_id"] == "ai_review"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"accept": False})
    assert result["step_id"] == "zones"


async def test_options_edit_and_remove_zone(hass: HomeAssistant, setup_states) -> None:
    entry = await _loaded(hass)
    result = await _options(hass, entry, "edit_zone")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"zone_id": "bad"})
    assert result["step_id"] == "zone"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Bad", "mode": "percent", "comfort": 22, "heater_kw": 0.4}
    )
    assert result["errors"] == {"number_entity": "number_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Bad", "mode": "percent", "number_entity": "number.bad_demand", "comfort": 22, "heater_kw": 0.5},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    bad = next(z for z in entry.options["zones"] if z["zone_id"] == "bad")
    assert bad["heater_kw"] == 0.5

    result = await _options(hass, entry, "remove_zone")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"zone_id": "bad"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [z["zone_id"] for z in entry.options["zones"]] == ["stue"]


async def test_options_add_zone_duplicate_name(hass: HomeAssistant, setup_states) -> None:
    entry = await _loaded(hass)
    result = await _options(hass, entry, "add_zone")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Stue", "mode": "setpoint", "climate_entity": "climate.stue", "comfort": 21, "heater_kw": 1}
    )
    assert result["errors"] == {"name": "duplicate"}


async def test_options_discover(hass: HomeAssistant, setup_states) -> None:
    entry = await _loaded(hass)
    # climate.stue is already a zone: nothing new
    result = await _options(hass, entry, "discover")
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "nothing_new"

    hass.states.async_set("climate.kontor", "heat", {"temperature": 21, "current_temperature": 20, "hvac_modes": ["off", "heat"]})
    result = await _options(hass, entry, "discover")
    assert result["step_id"] == "discover"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"zones": ["kontor"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "kontor" in [z["zone_id"] for z in entry.options["zones"]]


async def test_options_house_history_energy(hass: HomeAssistant, setup_states) -> None:
    entry = await _loaded(hass)
    result = await _options(hass, entry, "house")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"house_type": "detached", "insulation": "old", "heating_sources": ["panel_heaters"], "heated_area": 200}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["house"]["house_type"] == "detached"

    result = await _options(hass, entry, "history")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"use_history": False})
    assert entry.options["use_history"] is False

    hass.states.async_set("sensor.tibber_pulse_power", "1500", {"device_class": "power", "unit_of_measurement": "W"})
    result = await _options(hass, entry, "energy")
    assert result["step_id"] == "energy"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"contract": "spot", "grid": "none", "peak_limit": True, "price_includes_vat": True,
         "house_power_sensor": "sensor.tibber_pulse_power"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["energy"]["house_power_sensor"] == "sensor.tibber_pulse_power"


async def test_options_general_and_price_sensor(hass: HomeAssistant, setup_states) -> None:
    entry = await _loaded(hass)
    result = await _options(hass, entry, "general")
    assert result["step_id"] == "general"
    with patch(
        "custom_components.heat_planner.config_flow.async_setup_nordpool",
        return_value=(None, "NO1", "nordpool_failed"),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"price_source": SETUP_NORDPOOL, "weather_entity": "weather.home"}
        )
    assert result["errors"] == {"price_source": "nordpool_failed"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"price_source": OTHER_SENSOR, "weather_entity": "weather.home"}
    )
    assert result["step_id"] == "price_sensor"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"price_sensor": "sensor.y"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["price_sensor"] == "sensor.y"

    result = await _options(hass, entry, "general")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"price_source": f"sensor:{PRICE_SENSOR}"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["price_sensor"] == PRICE_SENSOR and "weather_entity" not in entry.options


def test_current_choice() -> None:
    assert _current_choice({"price_type": "nordpool", "nordpool_entry": "abc"}) == "nordpool:abc"
    assert _current_choice({"price_type": "tibber"}) == "tibber"
    assert _current_choice({"price_type": "sensor", "price_sensor": "sensor.p"}) == "sensor:sensor.p"
    assert _current_choice({}) is None


async def test_energy_defaults_find_house_meter(hass: HomeAssistant) -> None:
    hass.config.country = "NO"
    hass.states.async_set("sensor.ams_power", "unavailable", {"device_class": "power"})
    hass.states.async_set("sensor.tibber_pulse_power", "900", {"device_class": "power"})
    hass.states.async_set("sensor.tibber_pulse_max_power", "900", {"device_class": "power"})
    hass.states.async_set("sensor.kitchen_power", "900", {"device_class": "power"})
    d = energy_defaults(hass, {"price_type": "tibber"})
    assert d["house_power_sensor"] == "sensor.tibber_pulse_power"
    assert d["peak_limit"] is True and d["price_includes_vat"] is True
