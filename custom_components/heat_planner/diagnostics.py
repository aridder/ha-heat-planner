"""Diagnostics: the config entry, what the card sees and the learned models."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import HeatPlannerConfigEntry
from .const import CONF_PRESENCE, CONF_ZONE_ID
from .insights import CONF_NOTIFY_SERVICE
from .planner import model_quality

# People, devices that reveal who lives there, free text about their everyday
# life and where alerts are sent.
TO_REDACT = {
    CONF_PRESENCE,
    CONF_NOTIFY_SERVICE,
    "routine",
    "title",
    "unique_id",
    "latitude",
    "longitude",
    "manual_until",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HeatPlannerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    models: dict[str, Any] = {}
    for cfg in coordinator.zone_configs:
        zid = cfg[CONF_ZONE_ID]
        zone = coordinator.zone(zid)
        models[zid] = {
            "thermal": asdict(zone.thermal),
            "quality": model_quality(zone.thermal),
            "heater_kw": zone.heater_kw,
            "learned_kw": coordinator.zone_state(zid).get("learned_kw"),
        }
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "models": models,
        "export": async_redact_data(coordinator.export(), TO_REDACT),
    }
