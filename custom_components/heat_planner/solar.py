"""Sun position and a simple solar gain factor (pure, no Home Assistant imports).

The factor is a dimensionless 0..1 number: 1 is a clear sky with the sun in
zenith, 0 is night or full overcast. The thermal model multiplies it by a
learned ``solar_gain`` (°C/h), so the exact radiometric scale does not matter,
only that it rises and falls with the real sunshine.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable

# Rough cloud cover (%) for Home Assistant weather conditions, used when a
# forecast has no ``cloud_coverage``.
CONDITION_CLOUD: dict[str, float] = {
    "sunny": 0.0,
    "clear-night": 0.0,
    "windy": 30.0,
    "partlycloudy": 50.0,
    "windy-variant": 60.0,
    "exceptional": 75.0,
    "cloudy": 90.0,
    "rainy": 90.0,
    "snowy": 90.0,
    "lightning": 90.0,
    "snowy-rainy": 95.0,
    "lightning-rainy": 95.0,
    "hail": 95.0,
    "pouring": 100.0,
    "fog": 100.0,
}

# Without any cloud information assume an average Nordic sky.
UNKNOWN_SKY = 0.6


def _utc(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def sun_elevation(when: datetime, lat: float, lon: float) -> float:
    """Solar elevation in degrees (NOAA general solar position approximation).

    Accurate to well within a degree, which is plenty here. Naive datetimes
    are taken as UTC.
    """
    t = _utc(when)
    doy = t.timetuple().tm_yday
    hour = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )
    true_solar_min = hour * 60 + eqtime + 4 * lon
    ha = math.radians(true_solar_min / 4 - 180)
    phi = math.radians(lat)
    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    cos_zen = max(-1.0, min(1.0, cos_zen))
    return 90.0 - math.degrees(math.acos(cos_zen))


def clear_sky_factor(when: datetime, lat: float, lon: float) -> float:
    """0..1: sine of the sun elevation (0 when the sun is below the horizon)."""
    elev = sun_elevation(when, lat, lon)
    return max(0.0, math.sin(math.radians(elev)))


def cloud_attenuation(cloud_coverage_pct: float | None) -> float:
    """Kasten–Czeplak: share of clear-sky radiation that gets through clouds."""
    if cloud_coverage_pct is None:
        return UNKNOWN_SKY
    c = max(0.0, min(100.0, float(cloud_coverage_pct))) / 100.0
    return 1.0 - 0.75 * c**3.4


def solar_factor(
    when: datetime, lat: float, lon: float, cloud_coverage_pct: float | None
) -> float:
    """Clear-sky factor attenuated by cloud cover (unknown cover → × 0.6)."""
    return clear_sky_factor(when, lat, lon) * cloud_attenuation(cloud_coverage_pct)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def cloud_from_item(item: dict[str, Any]) -> float | None:
    """Cloud cover (%) from a weather state/forecast item, or None if unknown."""
    cc = _num(item.get("cloud_coverage"))
    if cc is not None:
        return cc
    cond = item.get("condition")
    if isinstance(cond, str):
        return CONDITION_CLOUD.get(cond)
    return None


def _parse_dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def forecast_solar(
    forecast_items: Iterable[dict[str, Any]] | None, lat: float, lon: float
) -> dict[datetime, float]:
    """Hourly 0..1 solar factor from Home Assistant weather forecast items.

    Items are dicts with ``datetime`` (ISO string or datetime), ``cloud_coverage``
    (%) and/or ``condition``. Keys are the forecast's own hours (same timezone
    as the items). Coarser forecasts (e.g. twice daily) are spread over the
    hours up to the next item, at most 24 h.
    """
    items: list[tuple[datetime, float | None]] = []
    for it in forecast_items or []:
        if not isinstance(it, dict):
            continue
        when = _parse_dt(it.get("datetime"))
        if when is None:
            continue
        items.append((when.replace(minute=0, second=0, microsecond=0), cloud_from_item(it)))
    items.sort(key=lambda x: x[0])
    out: dict[datetime, float] = {}
    for i, (when, cloud) in enumerate(items):
        nxt = items[i + 1][0] if i + 1 < len(items) else when + timedelta(hours=1)
        span = max(1, min(24, int((nxt - when).total_seconds() // 3600)))
        for k in range(span):
            h = when + timedelta(hours=k)
            # sun position in the middle of the hour
            out[h] = round(solar_factor(h + timedelta(minutes=30), lat, lon, cloud), 3)
    return out
