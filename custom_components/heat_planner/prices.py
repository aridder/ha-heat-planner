"""Price sources: core Nord Pool integration or any sensor exposing hourly/15-min prices."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_NORDPOOL_AREA,
    CONF_NORDPOOL_ENTRY,
    CONF_PRICE_SENSOR,
    CONF_PRICE_TYPE,
    PRICE_NORDPOOL,
    PRICE_TIBBER,
)
from .planner import PricePoint

_LOGGER = logging.getLogger(__name__)

# Attribute names used by popular price integrations
#  - nordpool (HACS):           raw_today / raw_tomorrow  [{start, end, value}]
#  - energidataservice:         raw_today / raw_tomorrow  [{hour, price}]
#  - entsoe:                    prices_today / prices_tomorrow / prices [{time, price}]
#  - tibber (template sensors): today / tomorrow [{startsAt, total}]
_LIST_ATTRS = (
    "raw_today",
    "raw_tomorrow",
    "prices_today",
    "prices_tomorrow",
    "prices",
    "today",
    "tomorrow",
    "data",
    "forecast",
)
_TIME_KEYS = ("start", "startsAt", "start_time", "time", "hour", "datetime", "from")
_END_KEYS = ("end", "endsAt", "end_time", "till", "to")
_VALUE_KEYS = ("value", "price", "total", "price_per_kwh", "energy")


def _to_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = dt_util.parse_datetime(value)
    else:
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_local(dt)


def parse_price_list(items: list[Any], scale: float = 1.0) -> list[PricePoint]:
    """Parse a list of price dicts from any of the supported shapes."""
    out: list[PricePoint] = []
    parsed: list[tuple[datetime, datetime | None, float]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        start = next((_to_dt(item[k]) for k in _TIME_KEYS if k in item), None)
        end = next((_to_dt(item[k]) for k in _END_KEYS if k in item), None)
        val = next((item[k] for k in _VALUE_KEYS if k in item and item[k] is not None), None)
        if start is None or val is None:
            continue
        try:
            parsed.append((start, end, float(val) * scale))
        except (TypeError, ValueError):
            continue
    parsed.sort(key=lambda p: p[0])
    for i, (start, end, val) in enumerate(parsed):
        if end is None:
            nxt = parsed[i + 1][0] if i + 1 < len(parsed) else None
            step = (nxt - start) if nxt and nxt > start else timedelta(hours=1)
            end = start + min(step, timedelta(hours=1))
        out.append(PricePoint(start, end, val))
    return out


class PriceSource:
    """Fetches and caches prices for today and tomorrow."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self.hass = hass
        self.config = config
        self._cache: dict[date, list[PricePoint]] = {}
        self._last_try: dict[date, datetime] = {}
        self.unit: str | None = None

    @property
    def description(self) -> str:
        if self.config.get(CONF_PRICE_TYPE) == PRICE_NORDPOOL:
            return f"Nord Pool {self.config.get(CONF_NORDPOOL_AREA, '')}".strip()
        if self.config.get(CONF_PRICE_TYPE) == PRICE_TIBBER:
            return "Tibber"
        return str(self.config.get(CONF_PRICE_SENSOR))

    async def async_get(self) -> list[PricePoint]:
        today = dt_util.now().date()
        tomorrow = today + timedelta(days=1)
        for old in [d for d in self._cache if d < today - timedelta(days=1)]:
            self._cache.pop(old, None)
        if self.config.get(CONF_PRICE_TYPE) == PRICE_NORDPOOL:
            for day in (today, tomorrow):
                await self._nordpool_day(day)
        elif self.config.get(CONF_PRICE_TYPE) == PRICE_TIBBER:
            await self._tibber()
        else:
            self._from_sensor()
        points = [p for d in sorted(self._cache) for p in self._cache[d]]
        return points

    async def _nordpool_day(self, day: date) -> None:
        if self._cache.get(day):
            return
        now = dt_util.now()
        last = self._last_try.get(day)
        # tomorrow's prices are published ~13:00 CET; retry every 15 minutes until then
        if last and now - last < timedelta(minutes=15):
            return
        self._last_try[day] = now
        area = self.config.get(CONF_NORDPOOL_AREA)
        try:
            resp = await self.hass.services.async_call(
                "nordpool",
                "get_prices_for_date",
                {
                    "config_entry": self.config[CONF_NORDPOOL_ENTRY],
                    "date": day.isoformat(),
                    "areas": area,
                },
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001 - not published yet, or integration down
            _LOGGER.debug("Nord Pool prices for %s not available: %s", day, err)
            return
        items = (resp or {}).get(area) or next(iter((resp or {}).values()), [])
        # The core integration reports currency per MWh
        points = parse_price_list(items, scale=1 / 1000)
        if points:
            self._cache[day] = points
            self.unit = f"{self.hass.config.currency}/kWh"

    async def _tibber(self) -> None:
        """Tibber's own service returns total prices (incl. taxes) per home."""
        today = dt_util.now().date()
        have_tomorrow = bool(self._cache.get(today + timedelta(days=1)))
        if self._cache.get(today) and have_tomorrow:
            return
        now = dt_util.now()
        last = self._last_try.get(today)
        if self._cache.get(today) and last and now - last < timedelta(minutes=15):
            return
        self._last_try[today] = now
        start = dt_util.start_of_local_day()
        try:
            resp = await self.hass.services.async_call(
                "tibber", "get_prices",
                {"start": start.isoformat(), "end": (start + timedelta(days=2)).isoformat()},
                blocking=True, return_response=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Tibber prices not available: %s", err)
            return
        homes = (resp or {}).get("prices") or {}
        items = next(iter(homes.values()), []) if homes else []
        points = parse_price_list(items)
        if points:
            self._cache.clear()
            for p in points:
                self._cache.setdefault(p.start.date(), []).append(p)
            self.unit = f"{self.hass.config.currency}/kWh"

    def _from_sensor(self) -> None:
        entity_id = self.config.get(CONF_PRICE_SENSOR)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return
        self.unit = state.attributes.get("unit_of_measurement")
        scale = 1 / 1000 if self.unit and "mwh" in self.unit.lower() else 1.0
        if self.unit and ("öre" in self.unit.lower() or "øre" in self.unit.lower() or "ct" in self.unit.lower()):
            scale = 1 / 100
        points: list[PricePoint] = []
        for attr in _LIST_ATTRS:
            val = state.attributes.get(attr)
            if isinstance(val, list):
                points.extend(parse_price_list(val, scale))
        dedup: dict[datetime, PricePoint] = {p.start: p for p in points}
        self._cache.clear()
        for p in sorted(dedup.values(), key=lambda x: x.start):
            self._cache.setdefault(p.start.date(), []).append(p)
