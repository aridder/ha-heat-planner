"""Websocket API used by the Heat Planner card."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import DOMAIN, SCOPE_ALL, SCOPE_DATE, SCOPE_DAYS, SCOPE_WEEKDAYS, SCOPE_WEEKEND, SIGNAL_ENTRIES_CHANGED

if TYPE_CHECKING:
    from .coordinator import HeatPlannerCoordinator


def _coordinators(hass: HomeAssistant) -> list[HeatPlannerCoordinator]:
    return [e.runtime_data for e in hass.config_entries.async_loaded_entries(DOMAIN)]


def _get(hass: HomeAssistant, entry_id: str) -> HeatPlannerCoordinator | None:
    for coord in _coordinators(hass):
        if coord.config_entry.entry_id == entry_id:
            return coord
    return None


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_preview)
    websocket_api.async_register_command(hass, ws_save)
    websocket_api.async_register_command(hass, ws_settings)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_feedback)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get"})
@callback
def ws_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Return the full state of every Heat Planner entry."""
    connection.send_result(msg["id"], {"entries": [c.export() for c in _coordinators(hass)]})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Push a fresh export whenever any coordinator updates (survives reloads)."""
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    listeners: list = []

    @callback
    def push() -> None:
        connection.send_message(
            websocket_api.event_message(
                msg["id"], {"entries": [c.export() for c in _coordinators(hass)]}
            )
        )

    @callback
    def attach() -> None:
        for u in listeners:
            u()
        listeners.clear()
        listeners.extend(coord.async_add_listener(push) for coord in _coordinators(hass))

    @callback
    def entries_changed() -> None:
        attach()
        push()

    unsub_signal = async_dispatcher_connect(hass, SIGNAL_ENTRIES_CHANGED, entries_changed)
    attach()

    @callback
    def unsub_all() -> None:
        unsub_signal()
        for u in listeners:
            u()

    connection.subscriptions[msg["id"]] = unsub_all
    connection.send_result(msg["id"])
    push()


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/preview",
        vol.Required("entry_id"): str,
        vol.Required("zone_id"): str,
        vol.Optional("date"): str,
        vol.Optional("values"): vol.All([vol.Coerce(float)], vol.Length(min=24, max=24)),
        vol.Optional("settings"): dict,
    }
)
@callback
def ws_preview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Plan a zone with unsaved edits (used while dragging)."""
    from dataclasses import replace

    from .const import TUNABLE_ZONE

    coord = _get(hass, msg["entry_id"])
    if coord is None:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    zone_id = msg["zone_id"]
    zone = coord.zone(zone_id)
    if msg.get("settings"):
        zone = replace(zone, **{k: v for k, v in msg["settings"].items() if k in TUNABLE_ZONE})
    overrides = dict(coord.overrides(zone_id))
    if msg.get("values") is not None:
        overrides[msg.get("date") or dt_util.now().date().isoformat()] = msg["values"]
    plan = coord.plan_zone(zone_id, zone=zone, overrides=overrides)
    connection.send_result(msg["id"], plan.as_dict())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save",
        vol.Required("entry_id"): str,
        vol.Required("zone_id"): str,
        vol.Required("scope"): vol.In(
            [SCOPE_DATE, SCOPE_ALL, SCOPE_WEEKDAYS, SCOPE_WEEKEND, SCOPE_DAYS]
        ),
        vol.Optional("date"): str,
        vol.Optional("days"): [vol.All(int, vol.Range(0, 6))],
        vol.Optional("values"): vol.Any(
            None, vol.All([vol.Coerce(float)], vol.Length(min=24, max=24))
        ),
    }
)
@websocket_api.async_response
async def ws_save(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Save a day profile for a date (override) or as the standing schedule."""
    from . import scope_weekdays

    coord = _get(hass, msg["entry_id"])
    if coord is None:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    zone_id = msg["zone_id"]
    if msg["scope"] == SCOPE_DATE:
        day = msg.get("date") or dt_util.now().date().isoformat()
        if dt_util.parse_date(day) is None:
            connection.send_error(msg["id"], "invalid", "date must be YYYY-MM-DD")
            return
        await coord.async_set_override(zone_id, day, msg.get("values"))
    else:
        if msg.get("values") is None:
            connection.send_error(msg["id"], "invalid", "values required")
            return
        await coord.async_set_schedule(
            zone_id, scope_weekdays(msg["scope"], msg.get("days")), msg["values"]
        )
    connection.send_result(msg["id"], coord.export())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings",
        vol.Required("entry_id"): str,
        vol.Optional("zone_id"): vol.Any(None, str),
        vol.Optional("resume"): bool,
        vol.Required("settings"): dict,
    }
)
@websocket_api.async_response
async def ws_settings(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Update zone tunables (zone_id) or price rules (no zone_id)."""
    coord = _get(hass, msg["entry_id"])
    if coord is None:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    zone_id: str | None = msg.get("zone_id")
    if msg.get("resume") and zone_id:
        await coord.async_resume(zone_id)
    clean: dict[str, Any] = {}
    for k, v in msg["settings"].items():
        if isinstance(v, bool) or v is None:
            clean[k] = v
        else:
            try:
                clean[k] = float(v)
            except (TypeError, ValueError):
                continue
    limits = {
        "away_until_hour": (-1, 24), "protected_from_hour": (0, 24), "protected_to_hour": (0, 24),
        "expensive_hours": (0, 12), "lookahead_hours": (1, 12), "max_off_hours": (0, 24),
        "comfort": (5, 30), "min_value": (0, 100), "max_value": (0, 100),
        "away_offset": (-80, 0), "price_shift": (0, 60), "preheat": (0, 40),
        "safety_min_temp": (5, 25), "heater_kw": (0.01, 50), "price_aggressiveness": (0, 2),
        "extreme_ratio": (1, 10), "extreme_min_price": (0, 100), "extreme_abs_price": (0, 100),
    }
    for k, (lo, hi) in limits.items():
        if isinstance(clean.get(k), (int, float)) and not isinstance(clean.get(k), bool):
            clean[k] = max(lo, min(hi, clean[k]))
    for k in ("away_until_hour", "protected_from_hour", "protected_to_hour",
              "expensive_hours", "lookahead_hours", "max_off_hours"):
        if k in clean and clean[k] is not None:
            clean[k] = int(clean[k])
    await coord.async_set_settings(zone_id, clean)
    connection.send_result(msg["id"], coord.export())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/feedback",
        vol.Required("entry_id"): str,
        vol.Required("zone"): str,
        vol.Required("kind"): vol.In(["too_cold", "too_warm"]),
    }
)
@websocket_api.async_response
async def ws_feedback(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """'Too cold' / 'too warm' from anyone in the household (small, bounded nudges)."""
    coord = _get(hass, msg["entry_id"])
    if coord is None or msg["zone"] not in {z["zone_id"] for z in coord.zone_configs}:
        connection.send_error(msg["id"], "not_found", "Unknown entry or zone")
        return
    message = await coord.async_feedback(msg["zone"], msg["kind"])
    connection.send_result(msg["id"], {"message": message})
