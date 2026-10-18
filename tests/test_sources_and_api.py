"""Price sources, onboarding helpers and the websocket API: edge cases and errors."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.heat_planner.const import DOMAIN
from custom_components.heat_planner.onboarding import (
    async_setup_nordpool,
    async_setup_weather,
    guess_nordpool_area,
    parse_price_choice,
    price_options,
    summary,
)
from custom_components.heat_planner.prices import PriceSource, parse_price_list

from .test_integration import _entry, setup_states  # noqa: F401

# --------------------------------------------------------------------------- prices


async def test_sensor_price_shapes_and_units(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    day = dt_util.start_of_local_day()
    hass.states.async_set(
        "sensor.eds", "100",
        {"unit_of_measurement": "øre/kWh",
         "raw_today": [{"hour": (day + timedelta(hours=i)).isoformat(), "price": 100 + i} for i in range(24)],
         "raw_tomorrow": None},
    )
    src = PriceSource(hass, {"price_type": "sensor", "price_sensor": "sensor.eds"})
    points = await src.async_get()
    assert len(points) == 24 and points[1].price == pytest.approx(1.01)
    assert points[0].end - points[0].start == timedelta(hours=1)
    assert src.description == "sensor.eds"

    hass.states.async_set(
        "sensor.mwh", "100",
        {"unit_of_measurement": "EUR/MWh",
         "prices": [{"time": (day + timedelta(hours=i)).replace(tzinfo=None).isoformat(), "price": 50.0} for i in range(3)]},
    )
    src = PriceSource(hass, {"price_type": "sensor", "price_sensor": "sensor.mwh"})
    points = await src.async_get()
    assert [p.price for p in points] == [0.05] * 3 and points[0].start.tzinfo is not None

    assert await PriceSource(hass, {"price_type": "sensor", "price_sensor": "sensor.missing"}).async_get() == []


def test_parse_price_list_skips_garbage() -> None:
    now = dt_util.now().replace(minute=0, second=0, microsecond=0)
    items: list[Any] = [
        "nope",
        {"value": 1.0},  # no start
        {"start": now.isoformat()},  # no value
        {"start": 12345, "value": 1.0},  # unparseable start
        {"start": "not a date", "value": 1.0},
        {"start": now.isoformat(), "value": "abc"},
        {"start": now, "value": 2.0},
        {"start": (now + timedelta(minutes=15)).isoformat(), "value": 3.0},
    ]
    points = parse_price_list(items)
    assert [p.price for p in points] == [2.0, 3.0]
    assert points[0].end == now + timedelta(minutes=15)  # quarter-hour step from the next item


async def test_nordpool_prices_cached_and_retried(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10))
    calls: list[str] = []

    async def get_prices(call: ServiceCall) -> dict[str, Any]:
        calls.append(call.data["date"])
        day = dt_util.parse_date(call.data["date"])
        if day != dt_util.now().date():
            raise ValueError("not published yet")
        start = dt_util.start_of_local_day(day)
        return {"NO1": [
            {"start": (start + timedelta(hours=i)).isoformat(), "end": (start + timedelta(hours=i + 1)).isoformat(),
             "price": 1000.0 + i} for i in range(24)
        ]}

    hass.services.async_register("nordpool", "get_prices_for_date", get_prices, supports_response=SupportsResponse.ONLY)
    src = PriceSource(hass, {"price_type": "nordpool", "nordpool_entry": "np", "nordpool_area": "NO1"})
    points = await src.async_get()
    assert len(points) == 24 and points[2].price == pytest.approx(1.002)
    assert src.description == "Nord Pool NO1" and src.unit == f"{hass.config.currency}/kWh"
    assert len(calls) == 2
    await src.async_get()  # today cached, tomorrow not retried within 15 minutes
    assert len(calls) == 2
    freezer.tick(timedelta(minutes=16))
    await src.async_get()
    assert len(calls) == 3


async def test_tibber_failure_and_cache(hass: HomeAssistant) -> None:
    src = PriceSource(hass, {"price_type": "tibber"})
    assert await src.async_get() == []  # service not registered


# --------------------------------------------------------------------------- onboarding


@pytest.mark.parametrize(
    ("country", "lat", "lon", "expected"),
    [("SE", 67.0, 20.0, ("SE1", "SEK")), ("SE", 64.0, 18.0, ("SE2", "SEK")), ("SE", 59.3, 18.0, ("SE3", "SEK")),
     ("SE", 55.6, 13.0, ("SE4", "SEK")), ("DK", 56.0, 9.0, ("DK1", "DKK")), ("DK", 55.7, 12.5, ("DK2", "DKK")),
     ("FI", 60.2, 24.9, ("FI", "EUR")), ("DE", 52.5, 13.4, ("DE-LU", "EUR")), ("PL", 52.2, 21.0, ("PL", "PLN")),
     ("US", 40.0, -74.0, ("NO1", "USD"))],
)
async def test_guess_area_other_countries(hass: HomeAssistant, country: str, lat: float, lon: float, expected: tuple[str, str]) -> None:
    hass.config.country, hass.config.currency = country, "USD"
    hass.config.latitude, hass.config.longitude = lat, lon
    assert guess_nordpool_area(hass) == expected


async def test_summary_english_with_everything(hass: HomeAssistant) -> None:
    hass.config.language = "en"
    MockConfigEntry(domain="nordpool", data={"areas": ["NO1"]}).add_to_hass(hass)
    hass.states.async_set("sensor.not_a_price", "1", {"unit_of_measurement": "W"})
    hass.states.async_set("person.a", "home")
    hass.states.async_set("weather.home", "sunny")
    text = summary(hass, 2)
    assert "✅ Electricity price: Nord Pool (NO1)" in text and "✅ Who is home" in text and "✅ Weather forecast" in text
    assert all(o.value != "sensor:sensor.not_a_price" for o in price_options(hass))


async def test_summary_norwegian_with_nothing(hass: HomeAssistant) -> None:
    hass.config.language, hass.config.country = "nb", "NO"
    text = summary(hass, 0)
    assert "Strømpris" in text and "ingen personer" in text


async def test_parse_price_choice(hass: HomeAssistant) -> None:
    hass.config.country = "NO"
    hass.config.latitude, hass.config.longitude = 59.9, 10.7
    np = MockConfigEntry(domain="nordpool", data={"areas": ["NO2"]})
    np.add_to_hass(hass)
    assert parse_price_choice(hass, f"nordpool:{np.entry_id}")["nordpool_area"] == "NO2"
    assert parse_price_choice(hass, "nordpool:gone")["nordpool_area"] == "NO1"
    assert parse_price_choice(hass, "tibber") == {"price_type": "tibber"}
    assert parse_price_choice(hass, "whatever") == {}


async def test_setup_nordpool_failures(hass: HomeAssistant) -> None:
    with patch.object(hass.config_entries.flow, "async_init", side_effect=RuntimeError("boom")):
        assert await async_setup_nordpool(hass) == (None, "NO1", "nordpool_failed")
    with (
        patch.object(hass.config_entries.flow, "async_init", return_value={"flow_id": "f1"}),
        patch.object(hass.config_entries.flow, "async_configure", return_value={"type": "form", "flow_id": "f1"}),
        patch.object(hass.config_entries.flow, "async_abort") as abort,
    ):
        assert (await async_setup_nordpool(hass))[2] == "nordpool_failed"
    abort.assert_called_once_with("f1")


async def test_setup_weather(hass: HomeAssistant) -> None:
    with patch.object(hass.config_entries.flow, "async_init", side_effect=RuntimeError("no location")):
        assert await async_setup_weather(hass) is None
    with patch.object(hass.config_entries.flow, "async_init", return_value={"type": "abort"}):
        assert await async_setup_weather(hass) is None
    with patch.object(hass.config_entries.flow, "async_init", return_value={"type": "create_entry"}):
        hass.states.async_set("weather.forecast_home", "sunny")
        assert await async_setup_weather(hass) == "weather.forecast_home"
    MockConfigEntry(domain="met").add_to_hass(hass)
    assert await async_setup_weather(hass) == "weather.forecast_home"


# --------------------------------------------------------------------------- websocket


async def test_websocket_errors_and_subscribe(hass: HomeAssistant, setup_states, hass_ws_client) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "number", "set_value")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)

    msg_id = 0

    async def send(**msg: Any) -> dict[str, Any]:
        nonlocal msg_id
        msg_id += 1
        await ws.send_json({"id": msg_id, **msg})
        return await ws.receive_json()

    for kind, extra in (
        ("preview", {"zone_id": "stue"}),
        ("save", {"zone_id": "stue", "scope": "all", "values": [0] * 24}),
        ("settings", {"settings": {}}),
        ("feedback", {"zone": "stue", "kind": "too_cold"}),
    ):
        res = await send(type=f"{DOMAIN}/{kind}", entry_id="nope", **extra)
        assert not res["success"] and res["error"]["code"] == "not_found"

    eid = entry.entry_id
    res = await send(type=f"{DOMAIN}/save", entry_id=eid, zone_id="stue", scope="date", date="bad", values=[0] * 24)
    assert res["error"]["code"] == "invalid"
    res = await send(type=f"{DOMAIN}/save", entry_id=eid, zone_id="stue", scope="all")
    assert res["error"]["code"] == "invalid"
    res = await send(type=f"{DOMAIN}/preview", entry_id=eid, zone_id="stue", settings={"comfort": 23, "evil": 1})
    assert res["success"]
    res = await send(
        type=f"{DOMAIN}/settings", entry_id=eid, zone_id="stue", resume=True,
        settings={"comfort": 99, "max_off_hours": 3.7, "preheat": None, "auto": True, "junk": "x"},
    )
    stue = next(z for z in res["result"]["zones"] if z["zone_id"] == "stue")
    assert stue["settings"]["comfort"] == 30 and stue["settings"]["max_off_hours"] == 3
    res = await send(type=f"{DOMAIN}/feedback", entry_id=eid, zone="kjeller", kind="too_warm")
    assert res["error"]["code"] == "not_found"

    res = await send(type=f"{DOMAIN}/subscribe")
    assert res["success"]
    event = await ws.receive_json()
    assert event["event"]["entries"][0]["entry_id"] == eid
    # a reload re-attaches and pushes again
    await hass.config_entries.async_reload(eid)
    await hass.async_block_till_done()
    event = await ws.receive_json()
    assert event["type"] == "event"
    await ws.send_json({"id": 999, "type": "unsubscribe_events", "subscription": msg_id})
    while (res := await ws.receive_json())["id"] != 999:
        pass
    assert res["success"]


# --------------------------------------------------------------------------- AI proposal


async def test_validate_proposal_edge_cases(hass: HomeAssistant, setup_states) -> None:
    from custom_components.heat_planner.assistant import describe_proposal, validate_proposal
    from custom_components.heat_planner.discovery import discover

    cands = discover(hass)
    assert validate_proposal(hass, "no json here", cands) == (None, "ai_invalid")
    assert validate_proposal(hass, "{not: json}", cands) == (None, "ai_invalid")
    assert validate_proposal(hass, "[1, 2]", cands) == (None, "ai_invalid")
    assert validate_proposal(hass, {"zones": ["x"]}, cands) == (None, "ai_no_zones")

    raw = {
        "zones": [
            {"climate_entity": "climate.stue", "name": "Stue", "mode": "percent", "number_entity": "number.gone",
             "temperature_sensor": "sensor.gone", "weekdays": [0] * 23,
             "settings": {"preheat": "lots", "extreme_off": True, "comfort": 30, "max_off_hours": 2.6}},
            {"climate_entity": "climate.stue", "name": "Duplicate"},
        ],
        "explanation": "  Kort.  ",
    }
    proposal, err = validate_proposal(hass, raw, cands)
    assert err is None and proposal is not None
    (zone,) = proposal["zones"]
    assert zone["mode"] == "setpoint" and "number_entity" not in zone  # percent without a number entity
    seed = proposal["seeds"][zone["zone_id"]]
    assert "schedule" not in seed  # 23 values is not a day curve
    assert seed["settings"] == {"extreme_off": True, "max_off_hours": 2}
    assert proposal["explanation"] == "Kort."

    hass.config.language = "nb"
    percent = {
        "zones": [{"zone_id": "bad", "name": "Bad", "mode": "percent", "comfort": 21}],
        "seeds": {"bad": {"schedule": {"0": [20.0] * 12 + [40.0] * 12}}},
        "explanation": "",
    }
    assert describe_proposal(hass, percent) == "- **Bad**: prosent, 20–40 %"
    hass.config.language = "en"
    setpoint = {**percent, "zones": [{**percent["zones"][0], "mode": "setpoint"}], "seeds": {}}
    assert "comfort 21°, day 21°" in describe_proposal(hass, setpoint)
