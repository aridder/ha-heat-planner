"""Discovery of heating devices and the house description."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heat_planner.discovery import (
    default_heater_kw,
    describe,
    discover,
    thermal_prior,
)


def _device(hass, entry, name, area_id=None):
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", name)}, name=name, suggested_area=None
    ) if area_id is None else dr.async_get(hass).async_update_device(
        dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("test", name)}, name=name
        ).id, area_id=area_id,
    )


async def test_discover_setpoint_percent_and_vehicle(hass: HomeAssistant) -> None:
    src = MockConfigEntry(domain="mqtt")
    src.add_to_hass(hass)
    car = MockConfigEntry(domain="kia_uvo")
    car.add_to_hass(hass)
    areas = ar.async_get(hass)
    stue = areas.async_create("Stue")
    bad = areas.async_create("Bad")
    ent = er.async_get(hass)

    d1 = _device(hass, src, "stue_thermo", stue.id)
    ent.async_get_or_create("climate", "mqtt", "s1", device_id=d1.id, suggested_object_id="gulv_stue")
    hass.states.async_set("climate.gulv_stue", "heat", {"hvac_modes": ["off", "heat"], "current_temperature": 20.3, "temperature": 20})
    ent.async_get_or_create("sensor", "mqtt", "s1p", device_id=d1.id, suggested_object_id="gulv_stue_power", original_device_class="power")

    d2 = _device(hass, src, "bad_thermo", bad.id)
    ent.async_get_or_create("climate", "mqtt", "b1", device_id=d2.id, suggested_object_id="bad")
    hass.states.async_set("climate.bad", "heat", {"hvac_modes": ["off", "heat"], "current_temperature": 0, "temperature": 20})
    ent.async_get_or_create("number", "mqtt", "b1n", device_id=d2.id, suggested_object_id="bad_pi_heating_demand")
    d3 = _device(hass, src, "bad_sensor", bad.id)
    ent.async_get_or_create("sensor", "mqtt", "b2", device_id=d3.id, suggested_object_id="bad_temp", original_device_class="temperature")

    d4 = _device(hass, car, "car")
    ent.async_get_or_create("climate", "kia_uvo", "c1", device_id=d4.id, suggested_object_id="ioniq_5")
    hass.states.async_set("climate.ioniq_5", "off", {"hvac_modes": ["off", "heat_cool"]})

    cands = discover(hass)
    by = {c.climate_entity: c for c in cands}
    assert set(by) == {"climate.gulv_stue", "climate.bad"}
    assert by["climate.gulv_stue"].mode == "setpoint" and by["climate.gulv_stue"].power_sensor == "sensor.gulv_stue_power"
    assert by["climate.gulv_stue"].name == "Stue" and by["climate.gulv_stue"].comfort == 20
    b = by["climate.bad"]
    assert b.mode == "percent" and b.number_entity == "number.bad_pi_heating_demand"
    assert b.temperature_sensor == "sensor.bad_temp"
    text = describe(hass, cands, "nb")
    assert "**Bad**: prosent" in text and "effektmåler" in text
    # already configured thermostats are skipped
    assert [c.climate_entity for c in discover(hass, {"climate.bad"})] == ["climate.gulv_stue"]


def test_house_prior() -> None:
    old = thermal_prior({"insulation": "old", "house_type": "detached", "heating_sources": ["panel_heaters"]})
    good = thermal_prior({"insulation": "good", "house_type": "apartment", "heating_sources": ["floor_electric"]})
    assert old.tau_hours < good.tau_hours
    assert good.gross_rate < old.gross_rate  # heavy floors heat slower than panel heaters
    assert default_heater_kw({"heating_sources": ["floor_electric"]}, 10) == 0.8
