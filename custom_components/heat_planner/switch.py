"""Switch to enable or disable automatic control per zone."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatPlannerConfigEntry
from .entity import HeatPlannerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coord = entry.runtime_data
    async_add_entities(AutoSwitch(coord, z["zone_id"]) for z in coord.zone_configs)


class AutoSwitch(HeatPlannerEntity, SwitchEntity):
    def __init__(self, coord, zone_id: str) -> None:
        super().__init__(coord, zone_id, "auto")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.zone_state(self.zone_id).get("auto", True))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_settings(self.zone_id, {"auto": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_settings(self.zone_id, {"auto": False})
