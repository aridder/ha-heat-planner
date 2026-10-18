"""Month peaks come from the meter's full energy history (e.g. Tibber), not just recent power."""

from datetime import timedelta

import pytest

from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.heat_planner.energy import async_house_hourly_kwh


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    yield


async def test_tibber_energy_statistic_gives_the_month_peaks(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.pulse_power", "800", {"unit_of_measurement": "W", "device_class": "power"})
    start = dt_util.start_of_local_day() - timedelta(days=3)
    meta = StatisticMetaData(
        mean_type=StatisticMeanType.NONE, has_sum=True, name="Consumption", source="tibber", unit_class="energy",
        statistic_id="tibber:energy_consumption_abc", unit_of_measurement="kWh",
    )
    rows, total = [], 0.0
    for i in range(48):
        kwh = 5.5 if i == 30 else (-900.0 if i == 40 else 1.0)  # one real peak, one import glitch
        total += kwh
        rows.append(StatisticData(start=dt_util.as_utc(start + timedelta(hours=i)), sum=total, state=total))
    async_add_external_statistics(hass, meta, rows)
    await async_wait_recording_done(hass)

    hourly = await async_house_hourly_kwh(hass, "sensor.pulse_power")
    assert max(hourly.values()) == pytest.approx(5.5)
    assert min(hourly.values()) >= 0  # the glitch is ignored
