"""Price strategy per zone."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatPlannerConfigEntry
from .coordinator import HeatPlannerCoordinator
from .entity import HeatPlannerZoneEntity

# Writes go through the coordinator, which serialises them itself.
PARALLEL_UPDATES = 0

STRATEGIES = {"off": 0.0, "gentle": 0.5, "balanced": 1.0, "aggressive": 2.0}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one price strategy select per zone."""
    coord = entry.runtime_data
    async_add_entities(StrategySelect(coord, z["zone_id"]) for z in coord.zone_configs)


class StrategySelect(HeatPlannerZoneEntity, SelectEntity):
    """How hard the zone chases cheap hours."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(STRATEGIES)

    def __init__(self, coord: HeatPlannerCoordinator, zone_id: str) -> None:
        super().__init__(coord, zone_id, "strategy")

    @property
    def current_option(self) -> str:
        agg = self.coordinator.zone(self.zone_id).price_aggressiveness
        return min(STRATEGIES, key=lambda k: abs(STRATEGIES[k] - agg))

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_settings(
            self.zone_id, {"price_aggressiveness": STRATEGIES[option]}
        )
