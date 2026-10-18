"""Comfort temperature per setpoint zone."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatPlannerConfigEntry
from .coordinator import HeatPlannerCoordinator
from .entity import HeatPlannerZoneEntity
from .planner import MODE_PERCENT

# Writes go through the coordinator, which serialises them itself.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a comfort temperature for every thermostat (setpoint) zone."""
    coord = entry.runtime_data
    async_add_entities(
        ComfortNumber(coord, z["zone_id"])
        for z in coord.zone_configs
        if z.get("mode") != MODE_PERCENT
    )


class ComfortNumber(HeatPlannerZoneEntity, NumberEntity):
    """The temperature the zone is planned around when someone is home."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 10
    _attr_native_max_value = 28
    _attr_native_step = 0.5
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coord: HeatPlannerCoordinator, zone_id: str) -> None:
        super().__init__(coord, zone_id, "comfort")

    @property
    def native_value(self) -> float:
        return self.coordinator.zone(self.zone_id).comfort

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_settings(self.zone_id, {"comfort": value})
