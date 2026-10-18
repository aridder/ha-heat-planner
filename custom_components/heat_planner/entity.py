"""Base entities and device info.

Every config entry is one *home* device; every zone is a device of its own,
linked to the home with ``via_device_id`` (see :func:`async_link_zone_devices`).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, CONF_ZONE_ID, CONF_ZONES, DOMAIN, MANUFACTURER, VERSION
from .coordinator import HeatPlannerCoordinator
from .planner import Plan


def home_device_info(entry: ConfigEntry) -> DeviceInfo:
    """The device that represents one Heat Planner home."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model="Home",
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
    )


def zone_device_info(entry: ConfigEntry, zone_id: str, name: str) -> DeviceInfo:
    """The device for one zone.

    ``via_device`` is deliberately not set here (it is deprecated in the device
    registry); the link to the home is made with ``via_device_id``.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
        name=name,
        manufacturer=MANUFACTURER,
        model="Zone",
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
    )


@callback
def async_setup_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create the home and zone devices, link zones to the home, drop removed zones."""
    registry = dr.async_get(hass)
    home = registry.async_get_or_create(config_entry_id=entry.entry_id, **home_device_info(entry))
    wanted = {(DOMAIN, entry.entry_id)}
    for zone in {**entry.data, **entry.options}.get(CONF_ZONES, []):
        info = zone_device_info(entry, zone[CONF_ZONE_ID], zone.get(CONF_NAME) or zone[CONF_ZONE_ID])
        device = registry.async_get_or_create(config_entry_id=entry.entry_id, **info)
        wanted |= info["identifiers"]
        if device.via_device_id != home.id:
            registry.async_update_device(device.id, via_device_id=home.id)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & wanted:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


class HeatPlannerEntity(CoordinatorEntity[HeatPlannerCoordinator]):
    """An entity belonging to the home (one per config entry)."""

    _attr_has_entity_name = True
    zone_id: str | None = None

    def __init__(self, coordinator: HeatPlannerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_home_{key}"
        self._attr_device_info = home_device_info(entry)


class HeatPlannerZoneEntity(CoordinatorEntity[HeatPlannerCoordinator]):
    """An entity belonging to one zone."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HeatPlannerCoordinator, zone_id: str, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self.zone_id: str = zone_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{zone_id}_{key}"
        name = next((z[CONF_NAME] for z in coordinator.zone_configs if z[CONF_ZONE_ID] == zone_id), zone_id)
        self._attr_device_info = zone_device_info(entry, zone_id, name)

    @property
    def plan(self) -> Plan | None:
        """The current plan of this zone, if any."""
        return self.coordinator.plans.get(self.zone_id)
