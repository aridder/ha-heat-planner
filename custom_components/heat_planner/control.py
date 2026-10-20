"""Pure control decisions (no Home Assistant imports).

The coordinator wires these to Home Assistant; the digital-twin simulator in
``tools/sim`` drives exactly the same functions closed-loop, so what is proven
there is what runs on the thermostats.

* :func:`manual_changed`, :func:`pause_end` – someone touched the thermostat.
* :func:`decide` / :func:`settle` / :func:`needs_write` – what to write for the
  current hour (live capacity guard, safety floor with hysteresis, no flapping,
  no needless writes).
* :func:`off_run_hours` – how long a zone has already been off (``max_off_hours``).
* :class:`HourMeter` – live whole-house power integrated per clock hour.
* :func:`live_shed` – the realtime capacity guard.
* :func:`limit_peaks` – capacity-tariff cuts across zones with re-planning.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .peak import realtime_shed
from .planner import Plan, PlanHour, ZoneConfig, hour_key, is_protected

MANUAL_GRACE = timedelta(minutes=3)
SHED_DROP = 2.0  # °C the live guard lowers a setpoint zone (percent zones: 20 points)
SHED_DROP_PCT = 20.0
NEAR_FLOOR = 0.5  # °C above safety_min_temp where price/peak lowering stops
AVG_SECONDS = 600.0  # time constant of the live meter's moving average


def _utc(when: datetime) -> datetime:
    return when.astimezone(UTC)


def elapsed(later: datetime, earlier: datetime) -> timedelta:
    """Real time between two aware datetimes (same-zone subtraction ignores DST)."""
    return _utc(later) - _utc(earlier)


def round_step(value: float, step: float) -> float:
    return round(round(value / step) * step, 2) if step > 0 else value


# --------------------------------------------------------------------------- manual changes


def manual_changed(
    last: dict[str, Any] | None,
    dev_off: bool,
    current: float | None,
    step: float,
    now: datetime,
) -> bool:
    """The device differs from what we last wrote (after a short grace period)."""
    if not last or elapsed(now, last["at"]) <= MANUAL_GRACE:
        return False
    return dev_off != bool(last.get("off")) or (
        not dev_off
        and current is not None
        and last.get("value") is not None
        and abs(current - last["value"]) > step / 2 + 0.01
    )


def pause_end(plan: Plan, now: datetime) -> datetime:
    """A manual change holds until the schedule next changes (at most 3 h without one)."""
    now_sched = plan.hours[0].scheduled
    return next(
        (h.start for h in plan.hours[1:] if h.scheduled != now_sched),
        now + timedelta(hours=3),
    )


# --------------------------------------------------------------------------- the hour's command


def safety_hold(room: float | None, zone: ZoneConfig, hold: bool) -> bool:
    """Safety floor with hysteresis, from the measured room temperature.

    Holds once the room is within ``NEAR_FLOOR`` of ``safety_min_temp`` and
    releases 0.5 °C higher. Holding starts *above* the floor because a floor
    with dead time keeps cooling the room for an hour or more after the heat
    comes back (the simulation saw rooms 1–3 °C under the floor at -15 °C when
    the hold only started below it).
    """
    if room is not None:
        if room < zone.safety_min_temp + NEAR_FLOOR:
            return True
        if room >= zone.safety_min_temp + NEAR_FLOOR + 0.5:
            return False
    return hold


def decide(
    zone: ZoneConfig, hour: PlanHour, step: float, *, shed: bool = False, hold: bool = False
) -> tuple[bool, float | None]:
    """(off, value) to command for the current plan hour."""
    off = hour.target is None or (zone.is_percent and hour.target <= 0)
    value = None if hour.target is None else round_step(hour.target, step)
    # the user's own schedule (or away floor) for this hour
    ref = (
        min(hour.scheduled, zone.comfort + zone.away_offset) if "away" in hour.reasons else hour.scheduled
    )
    if shed and value is not None and not off and not hold:
        # live capacity guard: lower this room for the rest of the hour
        drop = SHED_DROP_PCT if zone.is_percent else SHED_DROP
        value = round_step(max(zone.min_value, value - drop), step)
    if value is not None and not off and not zone.is_percent:
        # whatever stacked up (price, peak, live guard): never below the safety floor,
        # unless the user's own schedule/away setting is lower than that
        floor = min(zone.safety_min_temp, ref)
        if value < floor:
            value = round_step(floor, step)
        if hold and value < ref:
            # the room is near its safety floor: no price or peak lowering now
            value = round_step(ref, step)
    if off and hold:
        off = False
        value = round_step(hour.scheduled if zone.is_percent else ref, step)
    return off, value


def settle(
    zone: ZoneConfig,
    last: dict[str, Any] | None,
    hour_start: datetime,
    off: bool,
    value: float | None,
    *,
    hold: bool = False,
) -> float | None:
    """Keep this hour's setpoint when a re-plan only nudges it by one step.

    The plan is rebuilt every 5 minutes from a room temperature that moves with
    the thermostat's own cycling and sensor noise, so the optimiser's choice for
    the current hour can flip between neighbouring values (the simulation saw 34
    writes a day). Within the same plan hour a change of at most 0.5 °C (10
    points for percent zones) is ignored; bigger changes, on/off, the live guard
    and the safety hold always go through.
    """
    if (
        hold
        or off
        or value is None
        or not last
        or last.get("off")
        or last.get("hour") != hour_start
        or last.get("value") is None
    ):
        return value
    tol = 10.0 if zone.is_percent else 0.5
    return last["value"] if abs(value - last["value"]) <= tol + 0.01 else value


def too_cold(zone: ZoneConfig, hour: PlanHour, room: float | None) -> bool:
    """The room is already more than half ``PEAK_SLACK`` below its own schedule/away floor."""
    from .energy import PEAK_SLACK
    from .planner import comfort_reference

    if zone.is_percent or room is None:
        return False
    return room < comfort_reference(zone, hour) - PEAK_SLACK / 2


def needs_write(
    zone: ZoneConfig,
    off: bool,
    value: float | None,
    dev_off: bool,
    current: float | None,
    step: float,
) -> bool:
    if zone.is_percent:
        # for percent entities "off" is simply 0
        return current is None or abs(current - (0 if off else value or 0)) > step / 2 + 0.01
    return (
        (off and not dev_off)
        or (not off and dev_off)
        or (not off and (current is None or abs(current - (value or 0)) > step / 2 + 0.01))
    )


def off_run_hours(off_since: datetime | None, now: datetime) -> int:
    """Hours a zone has been off *before* the hour containing ``now`` (for max-off).

    The plan starts at the hour containing ``now``, and that hour is the plan's
    first slot, so it must not be counted here as well (it was, which cut every
    allowed OFF run one hour short). Counted in real hours (DST safe).
    """
    if off_since is None:
        return 0
    return max(0, int(elapsed(hour_key(now), hour_key(off_since)).total_seconds() // 3600))


# --------------------------------------------------------------------------- live meter


@dataclass
class HourMeter:
    """Live whole-house power integrated into energy for the current clock hour."""

    last: tuple[datetime, float] | None = None
    hour: datetime | None = None
    kwh: float = 0.0
    avg_kw: float | None = None  # ~10-minute moving average of the readings

    # Hours are compared as hour_key()s and durations in UTC: with plain local
    # datetimes the second 02:00 of the autumn DST change looked like the first,
    # so that hour's energy was added on top of the previous hour's.

    def add(self, kw: float, now: datetime) -> None:
        hour = hour_key(now)
        last = self.last
        if self.hour != hour:
            self.hour = hour
            self.kwh = 0.0
            if last is not None and elapsed(now, last[0]) < timedelta(minutes=10):
                self.kwh = last[1] * elapsed(now, hour).total_seconds() / 3600
        elif last is not None:
            self.kwh += last[1] * elapsed(now, last[0]).total_seconds() / 3600
        if self.avg_kw is None or last is None:
            self.avg_kw = kw
        else:
            weight = min(1.0, elapsed(now, last[0]).total_seconds() / AVG_SECONDS)
            self.avg_kw += (kw - self.avg_kw) * weight
        self.last = (now, kw)

    def projected(self, now: datetime, *, smooth: bool = False) -> float | None:
        """This hour's energy so far plus the latest reading to the end of the hour.

        ``smooth`` uses the ~10-minute average power instead of the latest
        reading – what the live guard acts on: floor thermostats switch their
        full power on and off, and projecting a moment when they all happened to
        be on over the rest of the hour set rooms back for nothing (and the
        reheat afterwards made a *higher* peak in the simulation).
        """
        hour = hour_key(now)
        last = self.last
        if last is None or self.hour != hour or elapsed(now, last[0]) > timedelta(minutes=10):
            return None
        left = elapsed(hour + timedelta(hours=1), last[0]).total_seconds() / 3600
        rate = self.avg_kw if smooth and self.avg_kw is not None else last[1]
        if smooth and elapsed(now, hour).total_seconds() < AVG_SECONDS:
            # early in the hour the average still carries the previous hour (the cooking
            # that just ended): the live guard set rooms back at 19:00 for an hour that
            # ended a kilowatt under the cap, and they recovered late (benchmark)
            rate = min(rate, last[1])
        return self.kwh + rate * max(0.0, left)


def live_shed(
    projected: float,
    cap_kw: float,
    plans: dict[str, Plan],
    zones: dict[str, ZoneConfig],
    priority: dict[str, int],
    shed: dict[str, datetime],
    now: datetime,
    holding: set[str] | frozenset[str] = frozenset(),
) -> dict[str, datetime]:
    """Zones to set back until the end of this hour (``{zone: until}``).

    Zones in ``holding`` (near their safety floor, see :func:`safety_hold`, or
    already well below their schedule, see :func:`too_cold`) are never set back
    and are released at once if they were.
    """
    left = 1 - (now.minute * 60 + now.second) / 3600
    end_of_hour = hour_key(now) + timedelta(hours=1)  # real time, also on DST days
    items: list[dict[str, Any]] = []
    for zid, p in plans.items():
        if not p.hours or zid not in zones:
            continue
        zone = zones[zid]
        draw = p.hours[0].duty * zone.heater_kw  # what lowering it actually removes
        items.append({
            "zone_id": zid,
            "priority": priority.get(zid, 0),
            "kw": draw * left,  # relief for the rest of this hour (kWh)
            # (percent zones have no sensor: a set-back regulator is not seen recovering)
            "protected": is_protected(zone, p.hours[0].start) or draw < 0.05 or zid in holding
            or zone.is_percent,
        })
    shed = {z: until for z, until in shed.items() if until > now and z not in holding}
    relief = sum(i["kw"] for i in items if not i["protected"] and i["zone_id"] not in shed)
    if projected - relief > cap_kw:
        # Even setting back every room cannot keep this hour under the cap: it only
        # costs comfort, and the reheat next hour made the month's top-3 *higher*
        # than without limiting in the simulation. Let this hour go – but a room already
        # set back stays so until the hour ends (released mid-hour, it reheated at full
        # power on top of the peak and was shed again: two extra writes for nothing).
        return dict(shed)
    out = realtime_shed(projected, cap_kw, items, currently_shed=list(shed))
    # once shed, a room stays set back until the hour ends (no flapping)
    return {z: shed.get(z, end_of_hour) for z in set(out) | set(shed)}


# --------------------------------------------------------------------------- capacity tariff


def day_peaks(
    plans: dict[str, Plan], caps: dict[datetime, float], base: dict[int, float]
) -> dict[date, float]:
    """Highest house load per day relative to that hour's cap (what the tariff counts)."""
    load: dict[datetime, float] = {}
    for p in plans.values():
        for h in p.hours:
            load[h.start] = load.get(h.start, 0.0) + h.kwh
    out: dict[date, float] = {}
    for start, v in load.items():
        over = v + base.get(start.hour, 0.0) - caps.get(start, 1e9)
        out[start.date()] = max(out.get(start.date(), -1e9), over)
    return out


PEAK_TIGHT_KW = 0.5  # plan_peaks: an hour this close to its cap counts as tight


def plan_peaks(
    zones: dict[str, ZoneConfig],
    plans: dict[str, Plan],
    caps: dict[datetime, float],
    base: dict[int, float],
    priority: dict[str, int],
    replan: Callable[[str, dict[datetime, float]], Plan | None],
    *,
    rounds: int = 3,
) -> dict[str, Plan]:
    """Capacity tariff planned *with* the heating (optimiser): per-zone kW budgets.

    The house cap minus the rest of the house (``base``, a high estimate per hour of
    day) is the heating budget of each hour. Where the planned rooms exceed it, the
    excess is shared out as a lower ``kw_cap`` for those zones (lowest priority
    first, in proportion to what each draws then; never in a zone's protected window,
    never for a percent zone, which has no sensor to plan its stored heat with)
    and ``replan(zone_id, kw_cap)`` plans them again: the optimiser then stores heat
    in the floors in the hours with headroom (pre-heating before the cooking peak)
    instead of the room simply being set back in the peak hour, which is what
    :func:`limit_peaks` does after the fact. Repeated a few rounds, because heat
    moved into an earlier hour can make that hour the one over budget.
    """
    kw_caps: dict[str, dict[datetime, float]] = {}
    current = dict(plans)
    for _ in range(rounds):
        by_hour: dict[datetime, list[tuple[str, float]]] = {}
        for zid, p in current.items():
            for h in p.hours:
                by_hour.setdefault(h.start, []).append((zid, h.kwh))
        changed: set[str] = set()
        for start, entries in by_hour.items():
            cap = caps.get(start)
            if cap is None:
                continue
            excess = sum(kw for _, kw in entries) + base.get(start.hour, 0.0) - cap
            if excess <= -PEAK_TIGHT_KW:
                continue
            # percent zones have no sensor: their heat is not moved for the budget. A
            # protected hour does get a budget: the optimiser never lowers it below the
            # schedule, but a budget stops it pre-heating every room at once there (April:
            # all rooms on at 14:00 in a cheap solar hour, 5.1 kW)
            ok = [(z, kw) for z, kw in entries if z in zones and not zones[z].is_percent]
            # a tight hour: rooms already planned to coast through it get a zero budget
            # too, so they are planned with the same margin (a room that wakes its
            # thermostat there sets the day's peak)
            for zid, kw in ok:
                if kw <= 0.02 and start not in kw_caps.get(zid, {}):
                    kw_caps.setdefault(zid, {})[start] = 0.0
                    changed.add(zid)
            if excess <= 0.05:
                continue
            movable = [(z, kw) for z, kw in ok if kw > 0.02]
            for prio in sorted({priority.get(z, 0) for z, _ in movable}):
                group = [(z, kw) for z, kw in movable if priority.get(z, 0) == prio]
                avail = sum(kw for _, kw in group)
                share = min(1.0, excess / avail) if avail > 0 else 0.0
                for zid, kw in group:
                    new = round(max(0.0, kw * (1 - share)), 3)
                    acc = kw_caps.setdefault(zid, {})
                    if new < acc.get(start, 1e9) - 1e-3:
                        acc[start] = new
                        changed.add(zid)
                excess -= avail * share
                if excess <= 0.05:
                    break
        if not changed:
            break
        for zid in sorted(changed):
            new_plan = replan(zid, dict(kw_caps[zid]))
            if new_plan is not None:
                current[zid] = new_plan
    return current


def limit_peaks(
    zones: dict[str, ZoneConfig],
    plans: dict[str, Plan],
    caps: dict[datetime, float],
    base: dict[int, float],
    priority: dict[str, int],
    replan: Callable[[str, dict[datetime, float], dict[datetime, float]], Plan | None],
    *,
    rounds: int = 4,
    check_base: dict[int, float] | None = None,
) -> dict[str, Plan]:
    """Spread heating between rooms so the month's capacity step holds.

    ``replan(zone_id, duty_cuts, predicted)`` rebuilds one zone's plan with the
    cuts applied (see :func:`energy.apply_cuts`); ``None`` keeps the old plan.
    Cuts are repeated a few rounds and kept only on days where they actually
    lower that day's peak – cutting hours on a day whose peak is set elsewhere
    costs comfort for nothing. ``check_base`` (a *high* estimate of the rest of
    the house) is used for that check: the cuts are planned against a low
    estimate, and in the simulation they moved heat into the evening hours where
    cooking then set a higher peak than without any limiting.
    """
    from .energy import limit  # energy imports HA helpers; keep this module light

    original = dict(plans)

    def rebuild(cuts_by_zone: dict[str, dict[datetime, float]]) -> dict[str, Plan]:
        out = dict(original)
        for zid, acc in cuts_by_zone.items():
            if not acc:
                continue
            pred = {h.start: h.predicted for h in original[zid].hours if h.predicted is not None}
            new = replan(zid, dict(acc), pred)
            if new is not None:
                out[zid] = new
        return out

    current = dict(plans)
    total: dict[str, dict[datetime, float]] = {}
    for _ in range(rounds):
        cuts = limit(zones, current, caps, base, priority)
        if not cuts:
            break
        for zid, zc in cuts.items():
            acc = total.setdefault(zid, {})
            for h, v in zc.items():
                acc[h] = acc.get(h, 0.0) + v
        current = rebuild(total)
    if total:
        check = check_base if check_base is not None else base
        before, after = day_peaks(original, caps, check), day_peaks(current, caps, check)
        useful = {
            d for d in after if after[d] < before.get(d, 0.0) - 0.05 and before.get(d, 0.0) > 0
        }
        kept = {z: {h: v for h, v in acc.items() if h.date() in useful} for z, acc in total.items()}
        if kept != total:
            current = rebuild(kept) if any(kept.values()) else original
    return current
