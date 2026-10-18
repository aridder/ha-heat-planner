"""Constants for Heat Planner."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "heat_planner"
VERSION: Final = "0.1.1"

PLATFORMS: Final = ["sensor", "switch", "number", "select"]

UPDATE_INTERVAL: Final = timedelta(minutes=5)
PLAN_HOURS: Final = 36
STORAGE_VERSION: Final = 1

CARD_URL: Final = f"/{DOMAIN}_static/heat-planner-card.js"

# Config entry data
CONF_PRICE_TYPE: Final = "price_type"  # "nordpool" | "sensor"
CONF_NORDPOOL_ENTRY: Final = "nordpool_entry"
CONF_NORDPOOL_AREA: Final = "nordpool_area"
CONF_PRICE_SENSOR: Final = "price_sensor"
CONF_PRICE_EXTRA: Final = "price_extra"  # fixed cost per kWh added to spot (grid tariff, VAT approx)
CONF_PRESENCE: Final = "presence_entities"
CONF_WEATHER: Final = "weather_entity"

PRICE_NORDPOOL: Final = "nordpool"
PRICE_SENSOR: Final = "sensor"
PRICE_TIBBER: Final = "tibber"

# Zones (stored in entry.options["zones"])
CONF_ZONES: Final = "zones"
CONF_ZONE_ID: Final = "zone_id"
CONF_NAME: Final = "name"
CONF_MODE: Final = "mode"
CONF_CLIMATE: Final = "climate_entity"
CONF_NUMBER: Final = "number_entity"
CONF_TEMP_SENSOR: Final = "temperature_sensor"
CONF_HEATER_KW: Final = "heater_kw"
CONF_COMFORT: Final = "comfort"
CONF_POWER_SENSOR: Final = "power_sensor"
CONF_HOUSE: Final = "house"
CONF_USE_HISTORY: Final = "use_history"
HISTORY_DAYS: Final = 30

# Tunable zone settings (stored in .storage, editable from the card)
TUNABLE_ZONE: Final = (
    "comfort",
    "min_value",
    "max_value",
    "away_offset",
    "away_until_hour",
    "protected_from_hour",
    "protected_to_hour",
    "price_shift",
    "preheat",
    "extreme_off",
    "safety_min_temp",
    "heater_kw",
    "price_aggressiveness",
    "max_off_hours",
)
TUNABLE_RULES: Final = (
    "enabled",
    "expensive_hours",
    "extreme_ratio",
    "extreme_min_price",
    "extreme_abs_price",
    "lookahead_hours",
)

SCOPE_DATE: Final = "date"
SCOPE_ALL: Final = "all"
SCOPE_WEEKDAYS: Final = "weekdays"
SCOPE_WEEKEND: Final = "weekend"
SCOPE_DAYS: Final = "days"

ATTR_ENTRY_ID: Final = "entry_id"
ATTR_ZONE: Final = "zone"

SIGNAL_ENTRIES_CHANGED: Final = f"{DOMAIN}_entries_changed"
