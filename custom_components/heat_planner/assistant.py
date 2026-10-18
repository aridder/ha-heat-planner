"""AI-assisted setup: let Claude, ChatGPT or Gemini propose zones, day curves and settings.

Two ways in:
  * Home Assistant's AI Task integration (ai_task.generate_data), if an AI is set up in HA.
  * Copy a prompt into any chat assistant and paste the JSON answer back.
Both produce the same JSON, which is validated strictly before anything is used.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import TUNABLE_ZONE
from .discovery import HEATING_SOURCES, HOUSE_TYPES, INSULATION, Candidate
from .planner import MODE_PERCENT, MODE_SETPOINT

_LOGGER = logging.getLogger(__name__)

LIMITS: dict[str, tuple[float, float]] = {
    "comfort": (12, 26), "away_offset": (-8, 0), "away_until_hour": (0, 23),
    "protected_from_hour": (0, 24), "protected_to_hour": (0, 24), "price_shift": (0, 3),
    "preheat": (0, 3), "safety_min_temp": (10, 22), "price_aggressiveness": (0, 2),
    "max_off_hours": (0, 4),
}
PERCENT_LIMITS: dict[str, tuple[float, float]] = {
    "away_offset": (-60, 0), "price_shift": (0, 40), "preheat": (0, 30),
}


def ai_task_entities(hass: HomeAssistant) -> list[str]:
    return [s.entity_id for s in hass.states.async_all("ai_task")]


def build_prompt(
    hass: HomeAssistant, house: dict[str, Any], cands: list[Candidate], routine: str
) -> str:
    """Everything an assistant needs, and nothing personal beyond the room list."""
    rooms = []
    for c in cands:
        st = hass.states.get(c.climate_entity)
        rooms.append({
            "climate_entity": c.climate_entity,
            "room": c.name,
            "suggested_mode": c.mode,
            "number_entity": c.number_entity,
            "temperature_sensor": c.temperature_sensor,
            "has_power_meter": bool(c.power_sensor),
            "current_setpoint": st.attributes.get("temperature") if st else None,
            "current_room_temperature": st.attributes.get("current_temperature") if st else None,
        })
    lang = "Norwegian (bokmål)" if (hass.config.language or "").startswith(("nb", "no", "nn")) else "English"
    return f"""You are configuring "Heat Planner", a Home Assistant integration that plans heating hour by hour around electricity spot prices and whether anyone is home.

HOME
{json.dumps(house, ensure_ascii=False)}
(house_type one of {HOUSE_TYPES}; insulation one of {INSULATION}; heating_sources from {HEATING_SOURCES})

DAILY ROUTINE (in the household's own words)
{routine.strip() or "(not given – assume an ordinary working family: up 06:30, away 08–15 on weekdays, children in bed 19:30, adults 23:00)"}

THERMOSTATS FOUND
{json.dumps(rooms, ensure_ascii=False, indent=1)}

HOW HEAT PLANNER WORKS
- Each zone has a day curve of 24 hourly values for weekdays and one for weekends.
  - mode "setpoint": values are OFFSETS in °C from the zone's "comfort" temperature (e.g. -2 at night, +1 in the morning).
  - mode "percent" (thermostats without a sensor): values are ABSOLUTE heating demand 0–100 %.
- On top of the curve it lowers the heat in the day's most expensive hours ("price_shift"), raises it in cheap hours just before ("preheat"),
  lowers it while nobody is home until "away_until_hour", and never saves money between "protected_from_hour" and "protected_to_hour"
  so it is warm when people come home. Floor heating is slow: start raising 1–2 hours before it should feel warm.
- Bathrooms: warm in the morning and at bedtime. Bedrooms: cooler at night. Living rooms: warm in the evening.

TASK
Return ONLY JSON (no markdown) in exactly this shape:
{{
  "house": {{"house_type": "...", "insulation": "...", "heating_sources": ["..."], "heated_area": 100}},
  "zones": [
    {{
      "climate_entity": "climate.xxx",
      "name": "Room name",
      "mode": "setpoint" | "percent",
      "number_entity": "number.xxx or null",
      "temperature_sensor": "sensor.xxx or null",
      "comfort": 21,
      "weekdays": [24 numbers],
      "weekend": [24 numbers],
      "settings": {{"away_offset": -3, "away_until_hour": 13, "protected_from_hour": 13, "protected_to_hour": 15,
                    "price_shift": 1, "preheat": 1, "price_aggressiveness": 1, "safety_min_temp": 18}}
    }}
  ],
  "explanation": "3–5 short sentences in {lang} explaining the choices to the household"
}}
Only use climate entities from the list. Keep comfort between 18 and 24 °C unless the routine says otherwise.
Percent zones: away_offset, price_shift and preheat are in percentage points."""


def _extract_json(text: Any) -> dict[str, Any] | None:
    if isinstance(text, dict):
        return text
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _curve(values: Any, mode: str) -> list[float] | None:
    if not isinstance(values, list) or len(values) != 24:
        return None
    try:
        vals = [float(v) for v in values]
    except (TypeError, ValueError):
        return None
    lo, hi = (0, 100) if mode == MODE_PERCENT else (-8, 6)
    return [round(max(lo, min(hi, v)) * 2) / 2 if mode != MODE_PERCENT else round(max(lo, min(hi, v)))
            for v in vals]


def validate_proposal(
    hass: HomeAssistant, raw: Any, cands: list[Candidate]
) -> tuple[dict[str, Any] | None, str | None]:
    """Strictly validate an AI answer. Returns (proposal, error_key)."""
    data = _extract_json(raw) if not isinstance(raw, dict) else raw
    if not data or not isinstance(data.get("zones"), list):
        return None, "ai_invalid"
    known = {c.climate_entity: c for c in cands}
    house_in = data.get("house") or {}
    house = {
        "house_type": house_in.get("house_type") if house_in.get("house_type") in HOUSE_TYPES else "terraced",
        "insulation": house_in.get("insulation") if house_in.get("insulation") in INSULATION else "normal",
        "heating_sources": [s for s in house_in.get("heating_sources") or [] if s in HEATING_SOURCES] or ["floor_electric"],
        "heated_area": max(10, min(1000, float(house_in.get("heated_area") or 100))),
    }
    zones, seeds, used = [], {}, set()
    for z in data["zones"]:
        if not isinstance(z, dict):
            continue
        ent = str(z.get("climate_entity"))
        cand = known.get(ent)
        if cand is None or ent in used:
            continue
        used.add(ent)
        mode: str = z["mode"] if z.get("mode") in (MODE_SETPOINT, MODE_PERCENT) else cand.mode
        number = z.get("number_entity") or cand.number_entity
        if mode == MODE_PERCENT and not (number and hass.states.get(number)):
            mode, number = MODE_SETPOINT, None
        sensor = z.get("temperature_sensor") or cand.temperature_sensor
        if sensor and not hass.states.get(sensor):
            sensor = cand.temperature_sensor
        name = str(z.get("name") or cand.name)[:40]
        zid = slugify(name) or cand.zone_id
        while zid in seeds:
            zid += "_2"
        comfort = max(12.0, min(26.0, float(z.get("comfort") or cand.comfort)))
        weekdays = _curve(z.get("weekdays"), mode)
        weekend = _curve(z.get("weekend"), mode) or weekdays
        settings: dict[str, Any] = {}
        for k, v in (z.get("settings") or {}).items():
            if k not in TUNABLE_ZONE or k in ("comfort", "heater_kw", "min_value", "max_value"):
                continue
            if isinstance(v, bool):
                settings[k] = v
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            lo, hi = (PERCENT_LIMITS.get(k) if mode == MODE_PERCENT else None) or LIMITS.get(k, (f, f))
            f = max(lo, min(hi, f))
            settings[k] = int(f) if k.endswith("_hour") or k == "max_off_hours" else f
        zone = cand.as_zone(0.0)
        zone.update({"zone_id": zid, "name": name, "mode": mode, "comfort": comfort})
        zone.pop("number_entity", None)
        zone.pop("temperature_sensor", None)
        if number and mode == MODE_PERCENT:
            zone["number_entity"] = number
        if sensor:
            zone["temperature_sensor"] = sensor
        zones.append(zone)
        seed: dict[str, Any] = {"settings": settings}
        if weekdays:
            seed["schedule"] = {**{str(d): weekdays for d in range(5)}, **{str(d): weekend for d in (5, 6)}}
        seeds[zid] = seed
    if not zones:
        return None, "ai_no_zones"
    explanation = str(data.get("explanation") or "").strip()[:1200]
    return {"house": house, "zones": zones, "seeds": seeds, "explanation": explanation}, None


def describe_proposal(hass: HomeAssistant, proposal: dict[str, Any]) -> str:
    nb = (hass.config.language or "").startswith(("nb", "no", "nn"))
    lines = []
    for z in proposal["zones"]:
        seed = proposal["seeds"].get(z["zone_id"], {})
        wd = (seed.get("schedule") or {}).get("0")
        if z["mode"] == MODE_PERCENT:
            span = f"{int(min(wd))}–{int(max(wd))} %" if wd else ""
            lines.append(f"- **{z['name']}**: " + (f"prosent, {span}" if nb else f"percent, {span}"))
        else:
            c = z["comfort"]
            span = f"{c + min(wd):g}–{c + max(wd):g}°" if wd else f"{c:g}°"
            lines.append(f"- **{z['name']}**: " + (f"komfort {c:g}°, døgnet {span}" if nb else f"comfort {c:g}°, day {span}"))
    out = "\n".join(lines)
    if proposal.get("explanation"):
        out += "\n\n" + proposal["explanation"]
    return out


async def async_ask_ai(hass: HomeAssistant, entity_id: str, prompt: str) -> Any:
    """Ask Home Assistant's AI Task integration for the JSON proposal."""
    resp = await hass.services.async_call(
        "ai_task",
        "generate_data",
        {"task_name": "Heat Planner setup", "entity_id": entity_id, "instructions": prompt},
        blocking=True,
        return_response=True,
    )
    return (resp or {}).get("data")
