"""Comfort temperature per setpoint zone."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatPlannerConfigEntry
from .entity import HeatPlannerEntity
from .planner import MODE_PERCENT


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coord = entry.runtime_data
    async_add_entities(
        ComfortNumber(coord, z["zone_id"])
        for z in coord.zone_configs
        if z.get("mode") != MODE_PERCENT
    )


class ComfortNumber(HeatPlannerEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 10
    _attr_native_max_value = 28
    _attr_native_step = 0.5
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coord, zone_id: str) -> None:
        super().__init__(coord, zone_id, "comfort")

    @property
    def native_value(self) -> float:
        return self.coordinator.zone(self.zone_id).comfort

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_settings(self.zone_id, {"comfort": value})
