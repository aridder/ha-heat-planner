"""The look-back view is rebuilt from the recorder on a first start (and after gaps)."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done
import pytest

from custom_components.heat_planner.history import hour_id
from custom_components.heat_planner.planner import hour_key

from .test_history import _entry_with_bathroom_thermostat, _setup
from .test_integration import setup_states  # noqa: F401


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder must start before hass in these tests."""
    return


async def test_backfill_from_recorder(hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory) -> None:
    real_now = dt_util.utcnow()
    start = hour_key(real_now) - timedelta(hours=30)
    freezer.move_to(start)
    for i in range(30 * 4):  # every 15 minutes for 30 hours
        heating = i % 2 == 0
        hass.states.async_set("climate.stue", "heat", {
            "temperature": 21 if (i // 4) % 2 else 19, "current_temperature": 20.0 if heating else 21.0,
            "hvac_action": "heating" if heating else "idle", "hvac_modes": ["off", "heat"],
        })
        hass.states.async_set("climate.bad", "heat", {"current_temperature": 0, "hvac_action": "heating"})
        hass.states.async_set("number.bad_demand", "40", {"min": 0, "max": 100})
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=15))
    await async_wait_recording_done(hass)
    freezer.move_to(real_now)

    coord = await _setup(hass, _entry_with_bathroom_thermostat())
    await hass.async_block_till_done()  # the first update starts the backfill in the background
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()

    rows = coord.history.zones["stue"]
    h = hour_key(dt_util.now()) - timedelta(hours=10)
    rec = rows[hour_id(h)]
    assert rec["src"] == "recorder"
    assert rec["temp"] == pytest.approx(20.5, abs=0.01)  # time-weighted mean of 20 and 21
    assert rec["heating"] == pytest.approx(0.5, abs=0.01)
    assert rec["kwh"] == pytest.approx(0.6, abs=0.02)  # half the hour × 1.2 kW
    assert rec["target"] in (19, 21)
    # 30 recorded hours of the last 48 are filled, older ones stay empty
    filled = [k for k, r in rows.items() if r.get("src") == "recorder"]
    assert 28 <= len(filled) <= 31
    assert hour_id(hour_key(dt_util.now()) - timedelta(hours=40)) not in rows

    bad = coord.history.zones["bad"][hour_id(h)]
    assert "temp" not in bad  # the bathroom thermostat's 0 °C is not a temperature
    assert bad["target"] == 40 and bad["heating"] == 1.0

    # the live current hour is never overwritten, and a second run finds nothing to do
    assert coord.history.zones["stue"][hour_id(dt_util.now())]["src"] == "live"
    assert await coord.history.async_backfill(coord) == 0
