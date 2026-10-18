"""Quality-scale features: devices, entities, services, diagnostics and repairs."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.heat_planner import async_remove_config_entry_device, scope_weekdays
from custom_components.heat_planner.const import DOMAIN
from custom_components.heat_planner.diagnostics import async_get_config_entry_diagnostics
from custom_components.heat_planner.repairs import issue_id

from .test_integration import PRICE_SENSOR, _entry, setup_states  # noqa: F401


@pytest.fixture
def mock_controls(hass: HomeAssistant) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    async_mock_service(hass, "number", "set_value")


async def _setup(hass: HomeAssistant, **options: object) -> MockConfigEntry:
    entry = _entry()
    if options:
        entry = MockConfigEntry(domain=DOMAIN, title=entry.title, data={}, options={**entry.options, **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _eid(hass: HomeAssistant, entry: MockConfigEntry, platform: str, unique_suffix: str) -> str:
    eid = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{unique_suffix}")
    assert eid is not None
    return eid


async def test_devices_linked_with_via_device_id(hass: HomeAssistant, setup_states, mock_controls) -> None:
    entry = await _setup(hass)
    devices = dr.async_get(hass)
    home = devices.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    stue = devices.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_stue")})
    assert home is not None and stue is not None
    assert home.name == "Hjem" and home.model == "Home" and home.entry_type is dr.DeviceEntryType.SERVICE
    assert stue.name == "Stue" and stue.via_device_id == home.id
    # every entity has a unique id, a translated name and sits on a device
    ents = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert ents and all(e.unique_id.startswith(entry.entry_id) and e.translation_key and e.device_id for e in ents)
    comfort = er.async_get(hass).async_get(_eid(hass, entry, "number", "stue_comfort"))
    assert comfort is not None and comfort.entity_category is EntityCategory.CONFIG

    # a removed zone takes its device with it
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "zones": [z for z in entry.options["zones"] if z["zone_id"] != "bad"]}
    )
    await hass.async_block_till_done()
    assert devices.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_bad")}) is None
    assert devices.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_stue")}) is not None

    assert not await async_remove_config_entry_device(hass, entry, stue)
    stale = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{entry.entry_id}_old")})
    assert await async_remove_config_entry_device(hass, entry, stale)


async def test_entities_write_through_coordinator(hass: HomeAssistant, setup_states, mock_controls) -> None:
    entry = await _setup(hass)
    coord = entry.runtime_data
    switch = _eid(hass, entry, "switch", "stue_auto")
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert coord.zone_state("stue")["auto"] is False
    assert hass.states.get(_eid(hass, entry, "sensor", "stue_mode")).state == "disabled"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert coord.zone_state("stue")["auto"] is True

    select = _eid(hass, entry, "select", "stue_strategy")
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "aggressive"}, blocking=True)
    assert coord.zone("stue").price_aggressiveness == 2.0
    await coord.async_refresh()
    assert hass.states.get(select).state == "aggressive"

    number = _eid(hass, entry, "number", "stue_comfort")
    await hass.services.async_call("number", "set_value", {"entity_id": number, "value": 22.5}, blocking=True)
    assert coord.zone("stue").comfort == 22.5


async def test_services_validate_and_raise_translated_errors(hass: HomeAssistant, setup_states, mock_controls) -> None:
    entry = await _setup(hass)
    coord = entry.runtime_data
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "set_schedule", {"zone": "stue", "values": [0] * 24, "scope": "date"}, blocking=True)
    assert err.value.translation_key == "date_required"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "resume", {"zone": "stue", "entry_id": "nope"}, blocking=True)
    assert err.value.translation_key == "entry_not_loaded"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "resume", {"zone": "kjeller"}, blocking=True)
    assert err.value.translation_key == "unknown_zone"

    day = dt_util.now().date() + timedelta(days=1)
    await hass.services.async_call(
        DOMAIN, "set_schedule",
        {"zone": "stue", "values": [1] * 24, "scope": "date", "date": day.isoformat(), "entry_id": entry.entry_id},
        blocking=True,
    )
    assert coord.overrides("stue")[day.isoformat()] == [1.0] * 24
    await hass.services.async_call(DOMAIN, "clear_override", {"zone": "stue", "date": day.isoformat()}, blocking=True)
    assert day.isoformat() not in coord.overrides("stue")
    await hass.services.async_call(DOMAIN, "clear_override", {"zone": "stue"}, blocking=True)
    await hass.services.async_call(
        DOMAIN, "set_schedule", {"zone": "stue", "values": [2] * 24, "scope": "days", "days": [2, 2, 4]}, blocking=True
    )
    assert coord.schedule("stue")[2] == [2.0] * 24 and coord.schedule("stue")[4] == [2.0] * 24
    await hass.services.async_call(DOMAIN, "resume", {"zone": "stue"}, blocking=True)


def test_scope_weekdays() -> None:
    assert scope_weekdays("weekdays", None) == [0, 1, 2, 3, 4]
    assert scope_weekdays("weekend", None) == [5, 6]
    assert scope_weekdays("days", [3, 1, 3]) == [1, 3]
    assert scope_weekdays("days", None) == []
    assert scope_weekdays("all", None) == list(range(7))


async def test_diagnostics_are_redacted(hass: HomeAssistant, setup_states, mock_controls) -> None:
    entry = await _setup(hass, alerts={"notify_service": "notify.mobile_app_secret", "kinds": []}, routine="We leave at 7")
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["options"]["presence_entities"] == "**REDACTED**"
    assert diag["entry"]["options"]["alerts"]["notify_service"] == "**REDACTED**"
    assert diag["entry"]["options"]["routine"] == "**REDACTED**"
    assert diag["entry"]["title"] == "**REDACTED**"
    assert set(diag["models"]) == {"stue", "bad"}
    assert "tau_hours" in diag["models"]["stue"]["thermal"] and diag["models"]["stue"]["quality"]
    assert diag["export"]["title"] == "**REDACTED**"
    assert [z["zone_id"] for z in diag["export"]["zones"]] == ["stue", "bad"]
    assert diag["last_update_success"] is True
    assert "person.a" not in str(diag) and "mobile_app_secret" not in str(diag)


async def test_price_issue_after_grace_period(
    hass: HomeAssistant, setup_states, mock_controls, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10, minutes=5))
    attrs = dict(hass.states.get(PRICE_SENSOR).attributes)
    hass.states.async_set(PRICE_SENSOR, "unavailable", {})
    entry = await _setup(hass)
    coord = entry.runtime_data
    issues = ir.async_get(hass)
    iid = issue_id("price_unavailable", entry.entry_id)
    assert issues.async_get_issue(DOMAIN, iid) is None  # grace period

    freezer.tick(timedelta(hours=3, minutes=1))
    await coord.async_refresh()
    issue = issues.async_get_issue(DOMAIN, iid)
    assert issue is not None and issue.translation_key == "price_unavailable"
    assert issue.translation_placeholders == {"title": "Hjem", "source": PRICE_SENSOR}

    hass.states.async_set(PRICE_SENSOR, "1.2", attrs)
    await coord.async_refresh()
    assert issues.async_get_issue(DOMAIN, iid) is None


async def test_fixed_price_needs_no_spot_price(hass: HomeAssistant, setup_states, mock_controls) -> None:
    hass.states.async_set(PRICE_SENSOR, "unavailable", {})
    entry = await _setup(hass, energy={"contract": "fixed", "fixed_price": 0.5, "grid": "none"})
    coord = entry.runtime_data
    from custom_components.heat_planner.repairs import price_missing

    assert not price_missing(coord, dt_util.now())


async def test_house_meter_and_recorder_issues(
    hass: HomeAssistant, setup_states, mock_controls, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10, minutes=5))
    hass.states.async_set("sensor.house_power", "unavailable", {"unit_of_measurement": "W", "device_class": "power"})
    entry = await _setup(
        hass,
        use_history=True,
        energy={"contract": "spot", "grid": "none", "peak_limit": True, "house_power_sensor": "sensor.house_power"},
    )
    coord = entry.runtime_data
    issues = ir.async_get(hass)
    recorder = issues.async_get_issue(DOMAIN, issue_id("recorder_missing", entry.entry_id))
    assert recorder is not None and recorder.severity is ir.IssueSeverity.WARNING
    meter_id = issue_id("house_meter_unavailable", entry.entry_id)
    assert issues.async_get_issue(DOMAIN, meter_id) is None

    freezer.tick(timedelta(hours=1, minutes=1))
    await coord.async_refresh()
    issue = issues.async_get_issue(DOMAIN, meter_id)
    assert issue is not None and issue.translation_placeholders["entity"] == "sensor.house_power"

    hass.states.async_set("sensor.house_power", "1200", {"unit_of_measurement": "W", "device_class": "power"})
    await coord.async_refresh()
    assert issues.async_get_issue(DOMAIN, meter_id) is None

    # unloading clears what is left
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert issues.async_get_issue(DOMAIN, issue_id("recorder_missing", entry.entry_id)) is None


async def test_remove_entry_deletes_storage(hass: HomeAssistant, setup_states, mock_controls) -> None:
    entry = await _setup(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
