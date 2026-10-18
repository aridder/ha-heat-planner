"""Learning heater power and the room model from recorder history."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.heat_planner.const import DOMAIN

from .test_integration import _entry, setup_states  # noqa: F401


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder must start before hass in these tests."""
    return


async def test_learns_heater_power_with_consent(
    hass: HomeAssistant, setup_states, freezer: FrozenDateTimeFactory
) -> None:
    start = dt_util.utcnow() - timedelta(days=3)
    freezer.move_to(start)
    for i in range(60):
        on = i % 3 == 0
        hass.states.async_set("sensor.bad_power", "380" if on else "0", {"unit_of_measurement": "W", "device_class": "power"})
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=20))
    await async_wait_recording_done(hass)
    freezer.move_to(dt_util.utcnow())

    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "number", "set_value")
    base = _entry()
    opts = {**base.options, "use_history": True}
    opts["zones"] = [dict(z, heater_kw=0, power_sensor="sensor.bad_power") if z["zone_id"] == "bad" else z for z in opts["zones"]]
    entry = MockConfigEntry(domain=DOMAIN, title="Hjem", data={}, options=opts)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    await coord.async_learn()
    assert coord.heater_kw("bad") == 0.38
    bad = next(z for z in coord.export()["zones"] if z["zone_id"] == "bad")
    assert bad["heater_kw_source"] == "measured"


async def test_no_learning_without_consent(hass: HomeAssistant, setup_states) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "number", "set_value")
    base = _entry()
    opts = {**base.options, "use_history": False}
    opts["zones"] = [dict(z, heater_kw=0, power_sensor="sensor.bad_power") if z["zone_id"] == "bad" else z for z in opts["zones"]]
    entry = MockConfigEntry(domain=DOMAIN, title="Hjem", data={}, options=opts)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_learn()
    assert entry.runtime_data.zone_state("bad").get("learned_kw") is None
