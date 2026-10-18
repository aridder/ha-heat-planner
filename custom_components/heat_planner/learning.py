"""Turn recorder history into samples for :func:`planner.fit_thermal` (pure).

Takes already-fetched state lists (``{entity_id: [State, ...]}`` as returned
by ``recorder.history.get_significant_states``), so it can run in an executor
and be unit tested without Home Assistant. Any object with ``state``,
``attributes`` and ``last_updated`` (or ``last_changed``) works as a state.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
import math
from typing import Any, Callable, Sequence

from .planner import Sample
from .solar import CONDITION_CLOUD, UNKNOWN_SKY, clear_sky_factor, solar_factor

DEFAULT_OUTDOOR = 5.0
STEP_MINUTES = 30


def _float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _when(st: Any) -> datetime | None:
    return getattr(st, "last_updated", None) or getattr(st, "last_changed", None)


def _attrs(st: Any) -> dict[str, Any]:
    return getattr(st, "attributes", None) or {}


class _Step:
    """A step function from time-sorted (time, value) points."""

    def __init__(self, data: list[tuple[datetime, Any]]) -> None:
        data = sorted(data, key=lambda x: x[0])
        self.t = [d[0] for d in data]
        self.v = [d[1] for d in data]

    def at(self, t: datetime) -> Any:
        i = bisect_right(self.t, t) - 1
        return self.v[i] if i >= 0 else None

    def share_true(self, a: datetime, b: datetime) -> float | None:
        """Time-weighted share of [a, b) where the value is truthy."""
        if self.at(a) is None:
            return None
        total = (b - a).total_seconds()
        on = 0.0
        cur, cur_v = a, bool(self.at(a))
        i = bisect_right(self.t, a)
        while i < len(self.t) and self.t[i] < b:
            if cur_v:
                on += (self.t[i] - cur).total_seconds()
            cur, cur_v = self.t[i], bool(self.v[i])
            i += 1
        if cur_v:
            on += (b - cur).total_seconds()
        return on / total if total > 0 else None


def _series(states: Sequence[Any] | None, getter: Callable[[Any], Any]) -> _Step:
    out = []
    for st in states or []:
        when = _when(st)
        if when is None:
            continue
        val = getter(st)
        if val is not None:
            out.append((when, val))
    return _Step(out)


def _outdoor_temp(st: Any) -> float | None:
    """Weather entity (``temperature`` attribute) or a plain temperature sensor."""
    attrs = _attrs(st)
    if "temperature" in attrs:
        return _float(attrs.get("temperature"))
    return _float(getattr(st, "state", None))


def _cloud(st: Any) -> float | None:
    attrs = _attrs(st)
    cc = _float(attrs.get("cloud_coverage"))
    if cc is not None:
        return cc
    state = getattr(st, "state", None)
    return CONDITION_CLOUD.get(state) if isinstance(state, str) else None


def samples_from_history(
    hist: dict[str, list],
    climate: str,
    sensor: str | None,
    outdoor: str | Sequence[Any] | None,
    lat: float | None,
    lon: float | None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    weather: str | Sequence[Any] | None = None,
    step_minutes: int = STEP_MINUTES,
) -> list[Sample]:
    """Resample history to a regular grid of :class:`Sample` (room, outdoor, duty, sun).

    ``outdoor`` is the entity id of a weather entity or outdoor temperature
    sensor in ``hist``, or its state list directly. Cloud cover is read from
    ``weather`` (defaults to ``outdoor``): its ``cloud_coverage`` attribute, else
    its condition; with no cloud information the solar factor is clear sky
    × 0.6. ``duty`` is the share of each interval the thermostat reported
    ``hvac_action: heating``. ``start``/``end`` default to the span of the
    room temperature history. Without ``lat``/``lon`` the solar factor is 0.
    """

    def states(ref: str | Sequence[Any] | None) -> Sequence[Any]:
        if ref is None:
            return []
        if isinstance(ref, str):
            return hist.get(ref, [])
        return ref

    room = _series(states(sensor), lambda s: _float(s.state)) if sensor else _Step([])
    if not room.t:
        room = _series(
            states(climate),
            lambda s: (lambda v: v if v is not None and v > 1 else None)(
                _float(_attrs(s).get("current_temperature"))
            ),
        )
    heat = _series(
        states(climate),
        lambda s: None if "hvac_action" not in _attrs(s) else _attrs(s).get("hvac_action") == "heating",
    )
    out = _series(states(outdoor), _outdoor_temp)
    cloud = _series(states(weather if weather is not None else outdoor), _cloud)

    if not room.t or not heat.t:
        return []
    start = start or room.t[0]
    end = end or room.t[-1]
    has_sun = lat is not None and lon is not None
    step = timedelta(minutes=step_minutes)

    samples: list[Sample] = []
    t = start
    while t <= end:
        r, h = room.at(t), heat.at(t)
        if r is not None and h is not None:
            o = out.at(t)
            duty = heat.share_true(t, t + step)
            if has_sun:
                cc = cloud.at(t)
                sol = (
                    solar_factor(t, lat, lon, cc)  # type: ignore[arg-type]
                    if cc is not None
                    else clear_sky_factor(t, lat, lon) * UNKNOWN_SKY  # type: ignore[arg-type]
                )
            else:
                sol = 0.0
            samples.append(
                Sample(
                    when=t,
                    room=r,
                    outdoor=o if o is not None else DEFAULT_OUTDOOR,
                    heating=bool(h),
                    solar=round(sol, 3),
                    duty=None if duty is None else round(duty, 3),
                )
            )
        t += step
    return samples
