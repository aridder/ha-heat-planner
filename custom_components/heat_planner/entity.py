"""Base entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION
from .coordinator import HeatPlannerCoordinator


class HeatPlannerEntity(CoordinatorEntity[HeatPlannerCoordinator]):
    """An entity belonging to one zone (or the home when zone_id is None)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HeatPlannerCoordinator, zone_id: str | None, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self.zone_id = zone_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{zone_id or 'home'}_{key}"
        if zone_id is None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry_id)},
                name=coordinator.config_entry.title,
                manufacturer="Heat Planner",
                model="Home",
                sw_version=VERSION,
                entry_type=DeviceEntryType.SERVICE,
            )
        else:
            name = next(
                (z["name"] for z in coordinator.zone_configs if z["zone_id"] == zone_id), zone_id
            )
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{entry_id}_{zone_id}")},
                name=name,
                manufacturer="Heat Planner",
                model="Zone",
                sw_version=VERSION,
                via_device=(DOMAIN, entry_id),
                entry_type=DeviceEntryType.SERVICE,
            )

    @property
    def plan(self):
        return self.coordinator.plans.get(self.zone_id) if self.zone_id else None
