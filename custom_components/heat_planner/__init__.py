"""Heat Planner – price- and presence-aware heating schedules for Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
    ATTR_ENTRY_ID,
    ATTR_ZONE,
    CARD_URL,
    CONF_ZONE_ID,
    CONF_ZONES,
    DOMAIN,
    HELP_URL,
    PLATFORMS,
    SCOPE_ALL,
    SCOPE_DATE,
    SCOPE_DAYS,
    SCOPE_WEEKDAYS,
    SCOPE_WEEKEND,
    SIGNAL_ENTRIES_CHANGED,
    VERSION,
)
from .coordinator import HeatPlannerCoordinator
from .entity import async_setup_devices
from .repairs import async_clear_issues
from .websocket import async_register_websocket

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type HeatPlannerConfigEntry = ConfigEntry[HeatPlannerCoordinator]

SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_ZONE): cv.string,
        vol.Required("values"): vol.All(
            cv.ensure_list, [vol.Coerce(float)], vol.Length(min=24, max=24)
        ),
        vol.Optional("scope", default=SCOPE_ALL): vol.In(
            [SCOPE_ALL, SCOPE_WEEKDAYS, SCOPE_WEEKEND, SCOPE_DAYS, SCOPE_DATE]
        ),
        vol.Optional("days"): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(0, 6))]),
        vol.Optional("date"): cv.date,
    }
)
CLEAR_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_ZONE): cv.string,
        vol.Optional("date"): cv.date,
    }
)
RESUME_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_ENTRY_ID): cv.string, vol.Required(ATTR_ZONE): cv.string}
)


def scope_weekdays(scope: str, days: list[int] | None) -> list[int]:
    """The weekdays (0 = Monday) a schedule scope applies to."""
    if scope == SCOPE_WEEKDAYS:
        return [0, 1, 2, 3, 4]
    if scope == SCOPE_WEEKEND:
        return [5, 6]
    if scope == SCOPE_DAYS:
        return sorted(set(days or []))
    return list(range(7))


def find_coordinator(
    hass: HomeAssistant, entry_id: str | None, zone_id: str
) -> HeatPlannerCoordinator:
    """The coordinator of the (loaded) home that has this zone."""
    if entry_id and not any(e.entry_id == entry_id for e in hass.config_entries.async_loaded_entries(DOMAIN)):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"entry_id": entry_id},
        )
    entry: HeatPlannerConfigEntry
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if entry_id and entry.entry_id != entry_id:
            continue
        coord = entry.runtime_data
        if any(z["zone_id"] == zone_id for z in coord.zone_configs):
            return coord
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_zone",
        translation_placeholders={"zone": zone_id},
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the card, websocket API and services once."""
    frontend = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(CARD_URL, str(frontend / "heat-planner-card.js"), cache_headers=False),
            StaticPathConfig(HELP_URL, str(frontend / "heat-planner-help.js"), cache_headers=False),
        ]
    )
    add_extra_js_url(hass, f"{HELP_URL}?v={VERSION}")
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    async_register_websocket(hass)

    async def set_schedule(call: ServiceCall) -> None:
        zone = call.data[ATTR_ZONE]
        coord = find_coordinator(hass, call.data.get(ATTR_ENTRY_ID), zone)
        if call.data["scope"] == SCOPE_DATE:
            day = call.data.get("date")
            if day is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="date_required"
                )
            await coord.async_set_override(zone, day.isoformat(), call.data["values"])
        else:
            await coord.async_set_schedule(
                zone, scope_weekdays(call.data["scope"], call.data.get("days")),
                call.data["values"],
            )

    async def clear_override(call: ServiceCall) -> None:
        zone = call.data[ATTR_ZONE]
        coord = find_coordinator(hass, call.data.get(ATTR_ENTRY_ID), zone)
        day = call.data.get("date") or dt_util.now().date()
        await coord.async_set_override(zone, day.isoformat(), None)

    async def resume(call: ServiceCall) -> None:
        zone = call.data[ATTR_ZONE]
        coord = find_coordinator(hass, call.data.get(ATTR_ENTRY_ID), zone)
        await coord.async_resume(zone)

    hass.services.async_register(DOMAIN, "set_schedule", set_schedule, SCHEDULE_SCHEMA)
    hass.services.async_register(DOMAIN, "clear_override", clear_override, CLEAR_SCHEMA)
    hass.services.async_register(DOMAIN, "resume", resume, RESUME_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HeatPlannerConfigEntry) -> bool:
    """Set up one home: devices, coordinator, platforms."""
    async_setup_devices(hass, entry)
    coordinator = HeatPlannerCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    async_dispatcher_send(hass, SIGNAL_ENTRIES_CHANGED)
    return True


async def _async_reload(hass: HomeAssistant, entry: HeatPlannerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HeatPlannerConfigEntry) -> bool:
    """Unload a home and give the thermostats back."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_shutdown()
        async_clear_issues(hass, entry.entry_id)
        async_dispatcher_send(hass, SIGNAL_ENTRIES_CHANGED)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the stored schedules and learned models."""
    async_clear_issues(hass, entry.entry_id)
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: HeatPlannerConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a zone device whose zone no longer exists."""
    zones = {f"{entry.entry_id}_{z[CONF_ZONE_ID]}" for z in entry.options.get(CONF_ZONES, [])}
    return not any(
        domain == DOMAIN and (ident == entry.entry_id or ident in zones)
        for domain, ident in device.identifiers
    )
