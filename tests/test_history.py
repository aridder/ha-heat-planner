"""The look-back view: rolling hourly log, persistence, DST and the websocket command."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.heat_planner.const import DOMAIN
from custom_components.heat_planner.history import (
    FLAG_MANUAL,
    FLAG_PAUSED,
    LOG_HOURS,
    HistoryLog,
    hour_id,
    room_value,
    rows_from_history,
    window,
)
from custom_components.heat_planner.planner import hour_key

from .test_integration import _entry, setup_states  # noqa: F401

OSLO = ZoneInfo("Europe/Oslo")


def _entry_with_bathroom_thermostat() -> MockConfigEntry:
    base = _entry()
    opts = {**base.options}
    # the bathroom thermostat has no floor sensor and reports 0 °C
    opts["zones"] = [dict(z, climate_entity="climate.bad") if z["zone_id"] == "bad" else z for z in opts["zones"]]
    return MockConfigEntry(domain=DOMAIN, title="Hjem", data={}, options=opts)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry):
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    async_mock_service(hass, "number", "set_value")
    hass.states.async_set("climate.bad", "heat", {"current_temperature": 0, "hvac_action": "heating"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_rolling_log_records_what_happened(hass: HomeAssistant, setup_states) -> None:
    coord = await _setup(hass, _entry_with_bathroom_thermostat())
    now = dt_util.now()
    stue = coord.history.zones["stue"][hour_id(now)]
    plan_h0 = coord.plans["stue"].hours[0]
    assert stue["src"] == "live"
    assert stue["temp"] == 20.5
    assert stue["mode"] == plan_h0.mode and stue["reasons"] == plan_h0.reasons
    assert stue["price"] == round(plan_h0.price, 4)
    assert stue["heating"] == 0.0  # hvac_action idle
    bad = coord.history.zones["bad"][hour_id(now)]
    assert "temp" not in bad  # 0 °C from a thermostat without a sensor is ignored
    assert bad["heating"] == 1.0

    # a second sample in the same hour: temperatures are averaged, the last target wins
    hass.states.async_set("climate.stue", "heat", {"temperature": 22, "current_temperature": 21.5,
                                                   "hvac_action": "heating", "hvac_modes": ["off", "heat"]})
    coord.history.sample(coord)
    stue = coord.history.zones["stue"][hour_id(now)]
    assert stue["temp"] == 21.0 and stue["temp_n"] == 2
    assert stue["heating"] == 0.5

    # a manual change: paused from now on, "manual" marks the hour it happened
    coord.zone_state("stue")["manual_until"] = (now + timedelta(hours=2)).isoformat()
    coord.history.sample(coord)
    flags = coord.history.zones["stue"][hour_id(now)]["flags"]
    assert FLAG_PAUSED in flags and FLAG_MANUAL in flags
    assert coord.history.zones["stue"][hour_id(now)]["mode"] == "manual"

    # house energy from the meter history fills completed hours
    coord.history.fill_house({hour_key(now - timedelta(hours=3)): 2.5}, now)
    assert coord.history.home[hour_id(now - timedelta(hours=3))]["house_kwh"] == 2.5


async def test_log_survives_a_restart_and_is_pruned(hass: HomeAssistant, setup_states) -> None:
    entry = _entry_with_bathroom_thermostat()
    coord = await _setup(hass, entry)
    old = dt_util.now() - timedelta(hours=LOG_HOURS + 5)
    coord.history.record_zone("stue", old, target=21, mode="normal", temp=20)
    coord.history.record_zone("stue", dt_util.now() - timedelta(hours=30), target=19, mode="setback", temp=19.4)
    coord.history.prune(dt_util.now())
    assert hour_id(old) not in coord.history.zones["stue"]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    rec = coord.history.zones["stue"][hour_id(dt_util.now() - timedelta(hours=30))]
    assert rec["target"] == 19 and rec["temp"] == 19.4

    # and straight from the Store
    fresh = HistoryLog(hass, entry.entry_id)
    await fresh.async_load()
    assert hour_id(dt_util.now() - timedelta(hours=30)) in fresh.zones["stue"]


async def test_websocket_history_shape_and_limits(hass: HomeAssistant, setup_states, hass_ws_client) -> None:
    entry = _entry_with_bathroom_thermostat()
    coord = await _setup(hass, entry)
    coord.history.record_zone("stue", dt_util.now() - timedelta(hours=2), target=21, mode="preheat",
                              reasons=["preheat"], price=1.5, level="cheap", temp=20.6, kwh=1.2)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/history", "entry_id": entry.entry_id})
    res = await ws.receive_json()
    assert res["success"]
    data = res["result"]
    assert data["hours"] == 24
    assert {z["zone_id"] for z in data["zones"]} == {"stue", "bad"}
    stue = next(z for z in data["zones"] if z["zone_id"] == "stue")
    assert len(stue["hours"]) == 25 and len(data["home"]) == 25
    assert stue["hours"][-1]["partial"] is True and stue["hours"][-1]["start"] == hour_id(dt_util.now())
    row = stue["hours"][-3]
    assert row["start"] == hour_id(dt_util.now() - timedelta(hours=2))
    assert row["mode"] == "preheat" and row["temp"] == 20.6 and row["cost"] == round(1.2 * 1.5, 4)
    assert set(row) >= {"target", "mode", "reasons", "price", "level", "predicted", "temp", "heating", "flags", "kwh", "cost"}
    assert stue["has_temperature"] is True
    bad = next(z for z in data["zones"] if z["zone_id"] == "bad")
    assert bad["has_temperature"] is False  # percent zone, no sensor: the card shows heating demand

    await ws.send_json({"id": 2, "type": f"{DOMAIN}/history", "entry_id": entry.entry_id, "hours": 168})
    res = await ws.receive_json()
    assert res["success"] and len(res["result"]["zones"][0]["hours"]) == 169
    await ws.send_json({"id": 3, "type": f"{DOMAIN}/history", "entry_id": entry.entry_id, "hours": 169})
    res = await ws.receive_json()
    assert not res["success"] and res["error"]["code"] == "invalid_format"
    await ws.send_json({"id": 4, "type": f"{DOMAIN}/history", "entry_id": "nope", "hours": 12})
    res = await ws.receive_json()
    assert not res["success"] and res["error"]["code"] == "not_found"
    # the regular export stays small and only advertises the feature
    assert "history_log" in coord.export()["features"]


def test_dst_autumn_two_hours_at_two() -> None:
    log = HistoryLog.__new__(HistoryLog)
    log.zones, log.home, log._paused = {}, {}, {}
    first = datetime(2026, 10, 25, 0, 30, tzinfo=UTC).astimezone(OSLO)  # 02:30 CEST
    second = datetime(2026, 10, 25, 1, 30, tzinfo=UTC).astimezone(OSLO)  # 02:30 CET
    assert first.hour == second.hour == 2
    log.record_zone("stue", first, target=21, mode="normal", temp=20.0)
    log.record_zone("stue", second, target=19, mode="setback", temp=19.0)
    assert len(log.zones["stue"]) == 2
    assert log.zones["stue"][hour_id(first)]["temp"] == 20.0
    assert log.zones["stue"][hour_id(second)]["temp"] == 19.0

    span = window(datetime(2026, 10, 25, 4, 10, tzinfo=OSLO), 4)
    assert [h.hour for h in span] == [1, 2, 2, 3, 4]  # five real hours, two of them at 02
    assert len({h.isoformat() for h in span}) == 5
    assert hour_id(first) in {h.isoformat() for h in span} and hour_id(second) in {h.isoformat() for h in span}


def test_room_value_ignores_missing_sensors() -> None:
    assert room_value(0) is None and room_value("1") is None and room_value("unknown") is None
    assert room_value("20.4") == 20.4


class _S:
    def __init__(self, when, state, **attrs):
        self.state, self.attributes, self.last_updated, self.last_changed = state, attrs, when, when


def test_rows_from_history_weights_by_time() -> None:
    h = datetime(2026, 10, 20, 7, tzinfo=OSLO)
    climate = [
        _S(h - timedelta(minutes=5), "heat", temperature=21, current_temperature=20.0, hvac_action="heating"),
        _S(h + timedelta(minutes=30), "heat", temperature=21, current_temperature=21.0, hvac_action="idle"),
        _S(h + timedelta(hours=1, minutes=10), "off", temperature=None, current_temperature=21.0, hvac_action="off"),
    ]
    mode = [_S(h - timedelta(hours=1), "preheat", reasons=["preheat"]),
            _S(h + timedelta(hours=1, minutes=20), "manual", reasons=[])]
    rows = rows_from_history({"climate": climate, "mode": mode}, {}, [h, h + timedelta(hours=1)],
                             percent=False, heater_kw=2.0, zone_id="stue")
    r0 = rows[h.isoformat()]
    assert r0["temp"] == 20.5 and r0["heating"] == 0.5 and r0["kwh"] == 1.0
    assert r0["target"] == 21 and r0["mode"] == "preheat" and r0["reasons"] == ["preheat"]
    r1 = rows[(h + timedelta(hours=1)).isoformat()]
    assert r1["target"] is None and "off" in r1["flags"]
    assert "manual" in r1["flags"] and "paused" in r1["flags"]
