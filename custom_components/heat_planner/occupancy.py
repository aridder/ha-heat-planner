"""Learn when the household usually gets home.

Pure module (no Home Assistant imports). The coordinator feeds it the recorder
history of the presence entities; the result replaces the fixed
``away_until_hour`` when that zone setting is "auto" (stored as ``-1``).

An *arrival* is the moment the house goes from "nobody home" to "someone
home": one tracked person becomes ``home`` while every person with a known
state was away. Short absences (< 2 h) and night arrivals (00–05) are ignored,
as are ``unknown``/``unavailable`` states (a person without a working tracker
says nothing). Only the first arrival of each day counts, and only the last
six weeks are kept.

The estimate per weekday is deliberately early (25th percentile): warming up a
little too soon costs a few øre, coming home to a cold house is what people
notice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

AUTO_HOUR = -1  # zone setting value meaning "learn away_until_hour"

MIN_ABSENCE = timedelta(hours=2)
KEEP = timedelta(weeks=6)
MIN_OBSERVATIONS = 3
NIGHT_END_HOUR = 5  # arrivals in [00, 05) are ignored
PERCENTILE = 0.25
WARM_MIN_HOUR = 10
WARM_MAX_HOUR = 18

_IGNORED = {"unknown", "unavailable", "none", ""}
_HOME = {"home", "on"}

WEEKDAYS = (0, 1, 2, 3, 4)
WEEKEND = (5, 6)


def _is_home(state: str) -> bool:
    if state in _HOME:
        return True
    try:  # zone.* entities report the number of persons inside
        return float(state) > 0
    except (TypeError, ValueError):
        return False


@dataclass
class ArrivalModel:
    """Observed arrivals per weekday (Mon=0..Sun=6) as minutes after midnight."""

    observations: dict[int, list[tuple[str, int]]] = field(
        default_factory=lambda: {wd: [] for wd in range(7)}
    )
    updated: str | None = None

    def minutes(self, weekdays: tuple[int, ...] | list[int]) -> list[int]:
        return [m for wd in weekdays for _, m in self.observations.get(wd, [])]

    def count(self, weekday: int) -> int:
        return len(self.observations.get(weekday, []))

    def estimate(self, weekday: int) -> int | None:
        """Early estimate (minutes) for one weekday, or None if too few observations."""
        mins = self.minutes((weekday,))
        return _percentile(mins, PERCENTILE) if len(mins) >= MIN_OBSERVATIONS else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "observations": {
                str(wd): [[d, m] for d, m in obs] for wd, obs in self.observations.items()
            },
            "updated": self.updated,
            "weekdays": {
                str(wd): {
                    "expected": _fmt(self.estimate(wd)),
                    "count": self.count(wd),
                }
                for wd in range(7)
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ArrivalModel:
        model = cls()
        if not isinstance(data, dict):
            return model
        for wd, obs in (data.get("observations") or {}).items():
            try:
                key = int(wd)
            except (TypeError, ValueError):
                continue
            if not 0 <= key <= 6:
                continue
            clean: list[tuple[str, int]] = []
            for item in obs or []:
                try:
                    d, m = item
                    clean.append((str(d), int(m)))
                except (TypeError, ValueError):
                    continue
            model.observations[key] = clean
        model.updated = data.get("updated")
        return model


def _percentile(values: list[int], q: float) -> int:
    vals = sorted(values)
    if not vals:
        raise ValueError("no values")
    pos = (len(vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    v = vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)
    return int(round(v / 5.0) * 5)  # whole five minutes reads better


def _fmt(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _to_time(minutes: int) -> time:
    minutes = max(0, min(23 * 60 + 59, minutes))
    return time(minutes // 60, minutes % 60)


def find_arrivals(
    person_histories: Mapping[str, Sequence[tuple[datetime | None, str]]], tz: tzinfo
) -> list[datetime]:
    """All arrivals (local time) after an absence of at least two hours."""
    events: list[tuple[datetime, str, str]] = []
    for person, hist in person_histories.items():
        for when, state in hist or []:
            if when is None:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=tz)
            events.append((when.astimezone(tz), person, str(state).lower()))
    events.sort(key=lambda e: e[0])

    current: dict[str, bool] = {}  # person -> home? (only persons with a known state)
    away_since: datetime | None = None
    arrivals: list[datetime] = []
    for when, person, state in events:
        was_known = bool(current)
        was_home = any(current.values())
        if state in _IGNORED:
            current.pop(person, None)
        else:
            current[person] = _is_home(state)
        known = bool(current)
        home = any(current.values())
        if not known:
            # nothing known about anyone: we cannot tell, forget the absence
            away_since = None
            continue
        if home:
            if not was_home and was_known and away_since is not None:
                # timestamps: same-zone datetimes subtract in wall time (DST nights)
                absent = when.timestamp() - away_since.timestamp()
                if absent >= MIN_ABSENCE.total_seconds() and when.hour >= NIGHT_END_HOUR:
                    arrivals.append(when)
            away_since = None
        elif was_home:
            away_since = when  # the last one left
        # unknown -> away: the absence start is unknown, so away_since stays None
    return arrivals


def learn_arrivals(
    person_histories: Mapping[str, Sequence[tuple[datetime | None, str]]],
    tz: tzinfo,
    now: datetime | None = None,
    previous: ArrivalModel | None = None,
) -> ArrivalModel:
    """Build an :class:`ArrivalModel` from presence histories.

    ``person_histories`` maps an entity id to ``(timestamp, state)`` tuples.
    ``previous`` (from storage) is merged so arrivals survive a short recorder
    retention; anything older than six weeks before ``now`` is dropped.
    """
    arrivals = find_arrivals(person_histories, tz)
    if now is None:
        stamps = [w for h in person_histories.values() for w, _ in (h or []) if w is not None]
        now = max(stamps) if stamps else datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    now = now.astimezone(tz)
    cutoff = (now - KEEP).date()

    first: dict[date, int] = {}
    if previous is not None:
        for obs in previous.observations.values():
            for iso, m in obs:
                try:
                    first[date.fromisoformat(iso)] = m
                except ValueError:
                    continue
    learned: dict[date, int] = {}
    for a in arrivals:
        m = a.hour * 60 + a.minute
        if a.date() not in learned or m < learned[a.date()]:
            learned[a.date()] = m
    first.update(learned)  # fresh history wins over stored values

    model = ArrivalModel(updated=now.isoformat())
    for d in sorted(first):
        if d < cutoff or d > now.date():
            continue
        model.observations[d.weekday()].append((d.isoformat(), first[d]))
    return model


def expected_arrival(model: ArrivalModel | None, day: date) -> time | None:
    """Expected (early) arrival for ``day``.

    Uses that weekday when it has at least three observations, otherwise the
    weekday (Mon–Fri) or weekend pool, otherwise ``None``.
    """
    est = expected_minutes(model, day)
    return None if est is None else _to_time(est[0])


def expected_minutes(model: ArrivalModel | None, day: date) -> tuple[int, int, str] | None:
    """``(minutes, observations, source)``; source is ``"weekday"`` or ``"pool"``."""
    if model is None:
        return None
    wd = day.weekday()
    mins = model.minutes((wd,))
    if len(mins) >= MIN_OBSERVATIONS:
        return _percentile(mins, PERCENTILE), len(mins), "weekday"
    pool = model.minutes(WEEKDAYS if wd in WEEKDAYS else WEEKEND)
    if len(pool) >= MIN_OBSERVATIONS:
        return _percentile(pool, PERCENTILE), len(pool), "pool"
    return None


def warm_from_hour(
    expected: time | None, lead_hours: float, default: int = 13
) -> int:
    """Hour to start warming so the room is warm at ``expected`` arrival.

    Arrival minus the room's warm-up lead time, rounded down to a whole hour and
    clamped to 10–18. Without an expected arrival, ``default`` is returned.
    """
    if expected is None:
        return default
    lead = max(0.0, float(lead_hours or 0.0))
    minutes = expected.hour * 60 + expected.minute - lead * 60
    hour = int(minutes // 60)
    return max(WARM_MIN_HOUR, min(WARM_MAX_HOUR, hour))


def summary(
    model: ArrivalModel | None, day: date, lead_hours: float | None = None
) -> dict[str, Any] | None:
    """Card-friendly summary for ``export["arrival"]`` (None if nothing learned).

    ``{"expected": "15:20", "count": 9, "source": "weekday", "weekday": 0,
    "warm_from": 13}`` – ``warm_from`` only when ``lead_hours`` is given.
    """
    est = expected_minutes(model, day)
    if est is None:
        return None
    minutes, count, source = est
    out: dict[str, Any] = {
        "expected": _fmt(minutes),
        "count": count,
        "source": source,
        "weekday": day.weekday(),
    }
    if lead_hours is not None:
        out["warm_from"] = warm_from_hour(_to_time(minutes), lead_hours)
    return out
