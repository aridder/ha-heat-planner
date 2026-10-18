"""Find heating devices in Home Assistant and describe the house."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .planner import MODE_PERCENT, MODE_SETPOINT, ThermalModel

# Climate entities that are not room heating
_EXCLUDED_PLATFORMS = {
    "kia_uvo", "tesla", "tesla_custom", "teslemetry", "tessie", "volvooncall", "volvo",
    "bmw_connected_drive", "mercedesme", "renault", "nissan_leaf", "polestar_api",
    "smartcar", "myskoda", "volkswagencarnet", "audiconnect", "toyota", "hyundai_kia_connect",
    "ecobee_vehicle", "heat_planner",
}
_DEMAND_HINTS = ("pi_heating_demand", "heating_demand", "regulator", "fixed_load_demand", "duty")

HOUSE_TYPES = ["apartment", "terraced", "detached", "cabin"]
INSULATION = ["old", "normal", "good", "passive"]
HEATING_SOURCES = [
    "floor_electric", "floor_water", "panel_heaters", "heat_pump_air_air",
    "heat_pump_air_water", "district_heating", "wood_stove",
]

_TAU = {"old": 20.0, "normal": 35.0, "good": 55.0, "passive": 90.0}
_TYPE_FACTOR = {"apartment": 1.3, "terraced": 1.1, "detached": 0.85, "cabin": 0.7}


def thermal_prior(house: dict[str, Any] | None) -> ThermalModel:
    """A starting thermal model from the house description (refined by learning)."""
    house = house or {}
    tau = _TAU.get(house.get("insulation", "normal"), 35.0)
    tau *= _TYPE_FACTOR.get(house.get("house_type", "terraced"), 1.0)
    sources = set(house.get("heating_sources") or [])
    if sources & {"floor_electric", "floor_water"} and not sources & {"panel_heaters", "heat_pump_air_air"}:
        gross = 0.7  # heavy floors heat slowly but store heat well
    elif "panel_heaters" in sources or "heat_pump_air_air" in sources:
        gross = 1.6
    else:
        gross = 1.0
    return ThermalModel(gross_rate=gross, tau_hours=round(tau, 1))


def default_heater_kw(house: dict[str, Any] | None, area_m2: float | None = None) -> float:
    """Rough heater power for a zone when nothing better is known."""
    sources = set((house or {}).get("heating_sources") or [])
    per_m2 = 0.08 if "floor_electric" in sources else 0.06  # kW per m² of room
    return round((area_m2 or 15) * per_m2, 2)


@dataclass
class Candidate:
    zone_id: str
    name: str
    area: str | None
    mode: str
    climate_entity: str
    number_entity: str | None
    temperature_sensor: str | None
    power_sensor: str | None
    comfort: float
    reason: str  # short explanation shown to the user

    def as_zone(self, heater_kw: float) -> dict[str, Any]:
        z = {
            "zone_id": self.zone_id, "name": self.name, "mode": self.mode,
            "climate_entity": self.climate_entity, "comfort": self.comfort, "heater_kw": heater_kw,
        }
        if self.number_entity:
            z["number_entity"] = self.number_entity
        if self.temperature_sensor:
            z["temperature_sensor"] = self.temperature_sensor
        if self.power_sensor:
            z["power_sensor"] = self.power_sensor
        return z


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def discover(hass: HomeAssistant, existing: set[str] | None = None) -> list[Candidate]:
    """Thermostats that look like room heating, with their helpers."""
    existing = existing or set()
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    out: list[Candidate] = []
    used_ids: set[str] = set()

    def area_of(entry: er.RegistryEntry | None) -> str | None:
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        dev = dev_reg.async_get(entry.device_id) if entry.device_id else None
        return dev.area_id if dev else None

    for state in hass.states.async_all("climate"):
        entry = ent_reg.async_get(state.entity_id)
        if state.entity_id in existing:
            continue
        if entry and entry.platform in _EXCLUDED_PLATFORMS:
            continue
        modes = state.attributes.get("hvac_modes") or []
        if modes and "heat" not in modes and "auto" not in modes:
            continue
        area_id = area_of(entry)
        area = area_reg.async_get_area(area_id) if area_id else None
        siblings = (
            [e for e in er.async_entries_for_device(ent_reg, entry.device_id) if not e.disabled_by]
            if entry and entry.device_id else []
        )
        number = next(
            (e.entity_id for e in siblings
             if e.domain == "number" and any(h in e.entity_id for h in _DEMAND_HINTS)),
            None,
        )
        power = next(
            (e.entity_id for e in siblings
             if e.domain == "sensor" and (e.original_device_class or e.device_class) == "power"),
            None,
        )
        current = _float(state.attributes.get("current_temperature"))
        # room sensor in the same area, not on the thermostat itself
        sensor = None
        if area_id:
            for e in er.async_entries_for_area(ent_reg, area_id) + [
                se for d in dr.async_entries_for_area(dev_reg, area_id)
                for se in er.async_entries_for_device(ent_reg, d.id)
            ]:
                if (
                    e.domain == "sensor" and not e.disabled_by
                    and (e.original_device_class or e.device_class) == "temperature"
                    and e.device_id != (entry.device_id if entry else None)
                    and not any(x in e.entity_id for x in ("roykvarsler", "smoke", "device_temperature", "cpu"))
                ):
                    sensor = e.entity_id
                    break
        if (current is None or current <= 1) and number:
            mode, reason = MODE_PERCENT, "no_sensor"
        else:
            mode, reason = MODE_SETPOINT, "thermostat"
        name = (area.name if area else None) or state.name
        zid = slugify(name) or "zone"
        base = zid
        i = 2
        while zid in used_ids:
            zid = f"{base}_{i}"
            i += 1
        used_ids.add(zid)
        comfort = _float(state.attributes.get("temperature")) or 21.0
        out.append(
            Candidate(
                zone_id=zid, name=name, area=area.name if area else None, mode=mode,
                climate_entity=state.entity_id, number_entity=number if mode == MODE_PERCENT else None,
                temperature_sensor=sensor, power_sensor=power,
                comfort=max(15.0, min(26.0, round(comfort * 2) / 2)), reason=reason,
            )
        )
    out.sort(key=lambda c: (c.area is None, c.name))
    return out


def describe(hass: HomeAssistant, cands: list[Candidate], lang: str) -> str:
    """Human readable list for the config flow."""
    nb = lang.startswith(("nb", "no", "nn"))
    if not cands:
        return "Fant ingen termostater." if nb else "No thermostats found."
    lines = []
    for c in cands:
        if c.mode == MODE_PERCENT:
            kind = ("styres i prosent – termostaten har ingen føler" if nb
                    else "controlled in percent – the thermostat has no sensor")
        else:
            kind = (f"termostat, står på {c.comfort:g}°" if nb else f"thermostat, set to {c.comfort:g}°")
        extra = []
        if c.temperature_sensor:
            extra.append("romføler funnet" if nb else "room sensor found")
        if c.power_sensor:
            extra.append("effektmåler funnet" if nb else "power meter found")
        lines.append(f"- **{c.name}**: {kind}" + (f". {', '.join(extra).capitalize()}." if extra else ""))
    return "\n".join(lines)
