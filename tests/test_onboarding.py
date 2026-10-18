"""Effortless setup: guessing, checklist, automatic Nord Pool, Tibber prices."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heat_planner.const import DOMAIN
from custom_components.heat_planner.onboarding import SETUP_NORDPOOL, guess_nordpool_area, price_options, summary
from custom_components.heat_planner.prices import PriceSource


@pytest.mark.parametrize(
    ("lat", "lon", "area"),
    [(59.91, 10.75, "NO1"), (60.39, 5.32, "NO5"), (58.97, 5.73, "NO2"), (58.15, 8.0, "NO2"),
     (63.43, 10.39, "NO3"), (69.65, 18.96, "NO4"), (60.79, 11.07, "NO1")],
)
async def test_guess_area_norway(hass: HomeAssistant, lat, lon, area) -> None:
    hass.config.country = "NO"
    hass.config.latitude, hass.config.longitude = lat, lon
    assert guess_nordpool_area(hass) == (area, "NOK")


async def test_checklist_when_nothing_exists(hass: HomeAssistant) -> None:
    hass.config.country, hass.config.language = "NO", "nb"
    hass.config.latitude, hass.config.longitude = 59.9, 10.7
    text = summary(hass, 0)
    assert "Sett opp Nord Pool for meg" in text and "NO1" in text
    assert "Home Assistant-appen" in text and "Met.no" in text and "Ingen termostater" in text
    assert price_options(hass)[0].value == SETUP_NORDPOOL


async def test_flow_sets_up_nordpool_for_the_user(hass: HomeAssistant) -> None:
    hass.config.country, hass.config.language = "NO", "nb"
    hass.states.async_set("person.a", "home", {"device_trackers": ["device_tracker.phone"]})
    hass.states.async_set("weather.forecast_home", "sunny", {"temperature": 3})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert "✅ Hvem som er hjemme" in result["description_placeholders"]["summary"]
    with patch(
        "custom_components.heat_planner.config_flow.async_setup_nordpool",
        return_value=("np_entry", "NO1", None),
    ) as setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": "Hjem", "price_source": SETUP_NORDPOOL}
        )
    assert setup.called and result["step_id"] == "house"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "energy"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    # no thermostats -> zones skipped, straight to history
    assert result["step_id"] == "history"
    with patch("custom_components.heat_planner.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    o = result["options"]
    assert o["price_type"] == "nordpool" and o["nordpool_entry"] == "np_entry" and o["nordpool_area"] == "NO1"
    assert o["presence_entities"] == ["person.a"] and o["use_history"] is True
    assert o["house"]["house_type"] == "terraced"
    # Norway, NO1: spot with support and Elvia grid tariff by default
    assert o["energy"]["contract"] == "spot_support" and o["energy"]["grid"] == "elvia_2026" and o["energy"]["peak_limit"] is False  # no house meter


async def test_nordpool_failure_is_explained(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch("custom_components.heat_planner.config_flow.async_setup_nordpool", return_value=(None, "NO1", "nordpool_failed")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": "Hjem", "price_source": SETUP_NORDPOOL, "weather_entity": "weather.x"}
        )
    assert result["errors"] == {"price_source": "nordpool_failed"}


async def test_existing_sources_are_offered(hass: HomeAssistant) -> None:
    MockConfigEntry(domain="nordpool", data={"areas": ["NO1"], "currency": "NOK"}).add_to_hass(hass)
    MockConfigEntry(domain="tibber", data={}).add_to_hass(hass)
    hass.states.async_set("sensor.nordpool_kwh_no1", "1.2", {"unit_of_measurement": "NOK/kWh", "raw_today": [{"start": "x", "value": 1}]})
    values = [o.value for o in price_options(hass)]
    assert values[0].startswith("nordpool:") and "tibber" in values and "sensor:sensor.nordpool_kwh_no1" in values
    assert SETUP_NORDPOOL not in values


async def test_tibber_prices(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    day = dt_util.start_of_local_day()

    async def get_prices(call):
        return {"prices": {"Hjem": [
            {"start_time": (day + timedelta(hours=i)).isoformat(), "price": 1.0 + i / 10} for i in range(48)
        ]}}

    hass.services.async_register("tibber", "get_prices", get_prices, supports_response=SupportsResponse.ONLY)
    src = PriceSource(hass, {"price_type": "tibber"})
    points = await src.async_get()
    assert len(points) == 48 and points[3].price == pytest.approx(1.3)
    assert src.description == "Tibber"


async def test_real_nordpool_flow_is_driven(hass: HomeAssistant) -> None:
    """Our automatic setup speaks the core Nord Pool flow's language."""
    pytest.importorskip("pynordpool")
    import homeassistant.components.nordpool.config_flow  # noqa: F401

    from custom_components.heat_planner.onboarding import async_setup_nordpool

    hass.config.country = "NO"
    hass.config.latitude, hass.config.longitude = 60.39, 5.32  # Bergen
    with patch("homeassistant.components.nordpool.config_flow.test_api", return_value={}), patch(
        "homeassistant.components.nordpool.async_setup_entry", return_value=True
    ):
        entry_id, area, err = await async_setup_nordpool(hass)
    assert err is None and area == "NO5"
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry.domain == "nordpool" and entry.data == {"areas": ["NO5"], "currency": "NOK"}
