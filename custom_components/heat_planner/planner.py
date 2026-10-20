"""Pure planning engine for Heat Planner.

No Home Assistant imports here, so the logic can be unit tested in isolation.

The engine turns
  * a per-weekday hourly schedule (plus optional per-date overrides),
  * electricity prices (any resolution, aggregated to hours),
  * presence and a simple thermal model
into an hourly plan with a target, a mode, human readable reason codes,
a predicted room temperature and an estimated cost.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass, field, asdict, replace
from datetime import date, datetime, timedelta, timezone, tzinfo, UTC
from math import ceil, sqrt
from statistics import median
from typing import Iterable

MODE_SETPOINT = "setpoint"
MODE_PERCENT = "percent"

LEVEL_CHEAP = "cheap"
LEVEL_NORMAL = "normal"
LEVEL_EXPENSIVE = "expensive"
LEVEL_EXTREME = "extreme"

# Reason codes (translated in the card)
R_SCHEDULE = "schedule"
R_OVERRIDE = "override"
R_AWAY = "away"
R_EXPENSIVE = "expensive"
R_PREHEAT = "preheat"
R_PREHEAT_EXTREME = "preheat_extreme"
R_EXTREME_OFF = "extreme_off"
R_PROTECTED = "protected"
R_SAFETY = "safety"
R_WARMUP = "warmup"
R_PEAK = "peak_limit"  # capacity tariff (the month's top-3 hours)

WARMUP_MAX_HOURS = 6  # longest warm-up before a schedule step (plus the floor's dead time)
WARMUP_TOLERANCE = 0.1  # °C: "reached" when the modelled room is this close to the new target


@dataclass
class PricePoint:
    """A price for an interval (price per kWh, any currency)."""

    start: datetime
    end: datetime
    price: float


@dataclass
class PriceRules:
    """How prices influence the plan."""

    enabled: bool = True
    cheap_fraction: float = 1 / 3  # the cheapest third of the day counts as cheap
    expensive_hours: int = 3  # the N most expensive hours of the day
    extreme_ratio: float = 1.5  # >= ratio * daily average ...
    extreme_min_price: float = 2.0  # ... and at least this (per kWh)
    extreme_abs_price: float = 3.0  # or at least this, regardless of the average
    lookahead_hours: int = 3  # how far ahead pre-heating looks
    # A day whose max - min price is below this (per kWh) has nothing worth
    # shifting (e.g. Norgespris): every hour of that day is "normal".
    min_spread: float = 0.15


@dataclass
class ThermalModel:
    """First order model of a heated room.

    gross_rate: °C/h the room would rise at full power with no losses.
    tau_hours:  time constant of heat loss toward outdoor temperature.
    """

    gross_rate: float = 1.0
    tau_hours: float = 40.0
    learned: bool = False
    samples: int = 0
    solar_gain: float = 0.0  # °C/h added at full clear-sky sun (0..1 solar factor)
    lag_hours: float = 0.0  # dead time before heating shows in the room (slow floors)
    rmse: float | None = None  # fit error on history, °C (None = unknown)

    def step(self, temp: float, duty: float, outdoor: float, dt: float, solar: float = 0.0) -> float:
        """Advance the room temperature by ``dt`` hours.

        ``duty`` is the share of time the heater is on (0..1) and ``solar`` a 0..1
        clear-sky solar factor. Shared by the planner, the optimiser and learning.
        """
        loss = (temp - outdoor) / self.tau_hours
        return temp + (self.gross_rate * duty - loss + self.solar_gain * solar) * dt


@dataclass
class ZoneConfig:
    """Configuration of one heated zone."""

    zone_id: str
    name: str
    mode: str = MODE_SETPOINT
    comfort: float = 21.0  # °C (setpoint) – percent zones use the schedule as absolute values
    min_value: float = 5.0
    max_value: float = 28.0
    away_offset: float = -4.0  # °C, or percentage points for percent zones
    away_until_hour: int = 13  # away setback only before this hour
    protected_from_hour: int = 13  # no price reductions in [from, to)
    protected_to_hour: int = 15
    price_shift: float = 1.0  # reduction in expensive hours
    preheat: float = 1.0  # increase in cheap hours before expensive hours
    extreme_off: bool = True
    safety_min_temp: float = 18.0  # never cut off when the room is colder than this
    heater_kw: float = 1.0
    price_aggressiveness: float = 1.0  # 0 = ignore prices, 1 = normal, 2 = aggressive
    max_off_hours: int = 2  # never cut off more than this many hours in a row
    # Optimal start: begin heating before a schedule step early enough (room model) that the
    # room reaches the new target on time, instead of only starting at the step. Off for a
    # bare ZoneConfig (the pure planning API keeps its schedule-only behaviour); the
    # integration turns it on for every zone.
    warmup: bool = False
    thermal: ThermalModel = field(default_factory=ThermalModel)

    @property
    def is_percent(self) -> bool:
        return self.mode == MODE_PERCENT


def default_schedule(mode: str) -> dict[int, list[float]]:
    """Sensible default schedule (Mon=0..Sun=6 -> 24 hourly values).

    Setpoint zones store offsets from the comfort temperature,
    percent zones store absolute heating demand in percent.
    """
    if mode == MODE_PERCENT:
        day = [20.0] * 5 + [70.0] * 4 + [40.0] * 11 + [70.0] * 2 + [40.0] + [20.0]
    else:
        day = [-2.0, -2.0, 0, 0, 0, 1.0, 1.0, 1.0] + [0.0] * 15 + [-2.0]
    return {wd: list(day) for wd in range(7)}


# --------------------------------------------------------------------------- hours


def hour_key(when: datetime) -> datetime:
    """Start of the hour containing ``when``, with a fixed UTC offset.

    Aware datetimes sharing a ``zoneinfo`` zone compare and hash by wall time,
    so the two 02:00 hours of the autumn DST change (Europe/Oslo, last Sunday
    of October) collide as dict keys and ``+ timedelta(hours=1)`` skips one of
    them. With a fixed offset every real hour is a distinct key, arithmetic is
    real-time, and ``hour``/``date()``/``weekday()`` still read local wall time.
    """
    if when.tzinfo is None:
        return when.replace(minute=0, second=0, microsecond=0)
    return when.astimezone(timezone(when.utcoffset() or timedelta(0))).replace(
        minute=0, second=0, microsecond=0
    )


def hours_from(start: datetime, n: int, tz: tzinfo | None = None) -> list[datetime]:
    """``n`` consecutive real hours from the hour containing ``start`` (see :func:`hour_key`).

    Steps in UTC and converts back to ``tz`` (default: ``start``'s zone), so a
    25-hour day has two 02:00 hours and a 23-hour day none.
    """
    first = hour_key(start)
    if first.tzinfo is None:
        return [first + timedelta(hours=i) for i in range(n)]
    zone = tz or start.tzinfo
    base = first.astimezone(UTC)
    return [hour_key((base + timedelta(hours=i)).astimezone(zone)) for i in range(n)]


def rekey_hours(values: dict[datetime, float] | None) -> dict[datetime, float]:
    """The same mapping keyed by :func:`hour_key` (later entries win on collisions)."""
    return {hour_key(k): v for k, v in (values or {}).items()}


# --------------------------------------------------------------------------- prices


def hourly_prices(points: Iterable[PricePoint]) -> dict[datetime, float]:
    """Aggregate any price resolution (15 min, 1 h) to hourly averages."""
    buckets: dict[datetime, list[float]] = {}
    for p in points:
        hour = hour_key(p.start)
        buckets.setdefault(hour, []).append(p.price)
    return {h: sum(v) / len(v) for h, v in sorted(buckets.items())}


def classify_prices(
    hours: dict[datetime, float], rules: PriceRules
) -> dict[datetime, str]:
    """Classify each hour relative to the other hours of the same local day."""
    by_day: dict[date, list[float]] = {}
    for h, p in hours.items():
        by_day.setdefault(h.date(), []).append(p)

    levels: dict[datetime, str] = {}
    for h, p in hours.items():
        day = sorted(by_day[h.date()])
        n = len(day)
        if not rules.enabled or n < 8 or day[-1] - day[0] < rules.min_spread:
            levels[h] = LEVEL_NORMAL
            continue
        avg = sum(day) / n
        top = day[max(0, n - rules.expensive_hours)]
        cheap = day[min(n - 1, int(n * rules.cheap_fraction))]
        extreme = (p >= rules.extreme_ratio * avg and p >= rules.extreme_min_price) or (
            p >= rules.extreme_abs_price
        )
        if extreme:
            levels[h] = LEVEL_EXTREME
        elif p >= top:
            levels[h] = LEVEL_EXPENSIVE
        elif p <= cheap:
            levels[h] = LEVEL_CHEAP
        else:
            levels[h] = LEVEL_NORMAL
    return levels


# --------------------------------------------------------------------------- plan


@dataclass
class PlanHour:
    start: datetime
    price: float | None
    level: str
    scheduled: float  # target from schedule/override only
    target: float | None  # final target, None = heating off
    mode: str  # comfort | setback | preheat | save | off | away | normal
    reasons: list[str]
    predicted: float | None = None
    duty: float = 0.0  # 0..1 share of the hour the heater is on
    kwh: float = 0.0
    cost: float | None = None
    # optimal start: the coming schedule target this hour warms up to (None = no warm-up);
    # part of the comfort the schedule asks for, so baselines (savings) include it too
    warmup: float | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        return d


@dataclass
class Plan:
    zone_id: str
    hours: list[PlanHour]
    cost: float | None
    baseline_cost: float | None
    kwh: float
    baseline_kwh: float
    # Degree-hours the predicted room temperature is below the comfort reference
    # (schedule, or the away floor). None when no temperature is known or for
    # percent zones (no temperature reference).
    comfort_deficit: float | None = None
    baseline_comfort_deficit: float | None = None
    method: str = "rules"

    @property
    def savings(self) -> float | None:
        if self.cost is None or self.baseline_cost is None:
            return None
        return self.baseline_cost - self.cost

    @property
    def extra_kwh(self) -> float:
        """Energy the plan uses beyond the baseline (pre-heat shows up here)."""
        return round(self.kwh - self.baseline_kwh, 3)

    @property
    def savings_pct(self) -> float | None:
        sav = self.savings
        if sav is None or not self.baseline_cost or self.baseline_cost <= 0:
            return None
        return round(100.0 * sav / self.baseline_cost, 1)

    def as_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "hours": [h.as_dict() for h in self.hours],
            "cost": self.cost,
            "baseline_cost": self.baseline_cost,
            "savings": self.savings,
            "kwh": self.kwh,
            "baseline_kwh": self.baseline_kwh,
            "extra_kwh": self.extra_kwh,
            "savings_pct": self.savings_pct,
            "comfort_deficit": self.comfort_deficit,
            "baseline_comfort_deficit": self.baseline_comfort_deficit,
            "method": self.method,
        }


def scheduled_value(
    zone: ZoneConfig,
    schedule: dict[int, list[float]],
    overrides: dict[str, list[float]],
    when: datetime,
) -> tuple[float, bool]:
    """Scheduled target for an hour (absolute), and whether an override applied."""
    ov = overrides.get(when.date().isoformat())
    if ov is not None and len(ov) == 24:
        raw, is_override = ov[when.hour], True
    else:
        raw, is_override = schedule.get(when.weekday(), default_schedule(zone.mode)[0])[
            when.hour
        ], False
    value = raw if zone.is_percent else zone.comfort + raw
    return value, is_override


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def is_protected(zone: ZoneConfig, when: datetime) -> bool:
    """Whether ``when`` falls in the zone's protected window (no price reductions)."""
    pf, pt = zone.protected_from_hour, zone.protected_to_hour
    h = when.hour
    return (pf <= h < pt) if pf <= pt else (h >= pf or h < pt)


def comfort_reference(zone: ZoneConfig, slot: PlanHour) -> float:
    """The comfort reference of an hour: the schedule, or the away floor when away."""
    if R_AWAY in slot.reasons:
        if zone.is_percent:
            return _clamp(slot.scheduled + zone.away_offset, zone.min_value, zone.max_value)
        return _clamp(
            min(slot.scheduled, zone.comfort + zone.away_offset), zone.min_value, zone.max_value
        )
    return _clamp(slot.scheduled, zone.min_value, zone.max_value)


def comfort_deficit(zone: ZoneConfig, slots: list[PlanHour], refs: list[float]) -> float | None:
    """Degree-hours the predicted temperature ends an hour below its reference."""
    if zone.is_percent or any(s.predicted is None for s in slots):
        return None
    return round(sum(max(0.0, r - s.predicted) for s, r in zip(slots, refs)), 2)  # type: ignore[operator]


def build_plan(
    zone: ZoneConfig,
    schedule: dict[int, list[float]],
    overrides: dict[str, list[float]],
    prices: dict[datetime, float],
    rules: PriceRules,
    start: datetime,
    hours: int = 24,
    *,
    away_now: bool = False,
    room_temp: float | None = None,
    outdoor: dict[datetime, float] | None = None,
    outdoor_default: float = 5.0,
    method: str = "rules",
    solar: dict[datetime, float] | None = None,
    kw_cap: dict[datetime, float] | None = None,
    optimizer_weights=None,
    adjust=None,
    initial_off_run: int = 0,
) -> Plan:
    """Build an hourly plan starting at the hour containing ``start``.

    ``method="optimize"`` lets :func:`optimizer.optimize_zone` re-choose the
    targets of the rule plan (needs ``room_temp``; otherwise the rules result is
    used). ``solar`` (0..1 per hour) and ``kw_cap`` (kW per hour) are used by the
    optimiser and, where supported, by the room simulation.

    ``prices`` must be hourly (see :func:`hourly_prices`) and keyed by
    timezone-aware local datetimes. ``away_now`` only affects the first hour
    and the following hours until someone is expected home (``away_until_hour``).
    ``initial_off_run`` is how many hours heating has already been off right
    before ``start``; it counts toward ``max_off_hours``.
    """
    local_tz = start.tzinfo
    starts = hours_from(start, hours)
    start = starts[0] if starts else hour_key(start)
    # one key per real hour (DST): see hour_key
    prices = rekey_hours(prices)
    outdoor = rekey_hours(outdoor)
    solar = rekey_hours(solar) if solar else solar
    kw_cap = rekey_hours(kw_cap) if kw_cap else kw_cap
    levels = classify_prices(prices, rules)
    agg = _clamp(zone.price_aggressiveness, 0.0, 2.0)

    slots: list[PlanHour] = []
    away_active = away_now
    # Percent zones (regulators) move heat from a lowered hour into the hours just before
    # it (see _balance_percent). The hours already gone that this can involve are
    # planned again too, so every re-plan makes the same choice for the hours to come.
    pre: list[datetime] = []
    if zone.is_percent and starts:
        n_pre = 2 * PERCENT_WINDOW
        pre = hours_from(start - timedelta(hours=n_pre), n_pre, local_tz)
    n_pre_slots = len(pre)
    for idx, t in enumerate(pre + starts):
        if idx == n_pre_slots:
            away_active = away_now
        elif idx < n_pre_slots:
            away_active = False  # (whether anyone was away then is not known here)
        price = prices.get(t)
        level = levels.get(t, LEVEL_NORMAL)
        sched, is_override = scheduled_value(zone, schedule, overrides, t)
        reasons = [R_OVERRIDE if is_override else R_SCHEDULE]
        target = sched
        pf, pt = zone.protected_from_hour, zone.protected_to_hour
        protected = (pf <= t.hour < pt) if pf <= pt else (t.hour >= pf or t.hour < pt)

        # Away: only until the expected warm-up hour of that day.
        if away_active and t.hour >= zone.away_until_hour:
            away_active = False
        if away_active and t.date() != start.date() and t.hour >= 0:
            # never assume an absence into the next day
            away_active = False
        if away_active:
            if zone.is_percent:
                target = sched + zone.away_offset
            else:
                # away is a floor, not additive: night setback and away do not stack
                target = min(sched, zone.comfort + zone.away_offset)
            reasons.append(R_AWAY)
        reference = target  # schedule, or the away floor: what price logic lowers from

        # Look ahead for expensive/extreme hours (crosses midnight).
        ahead = [
            levels.get(t + timedelta(hours=k), LEVEL_NORMAL)
            for k in range(1, rules.lookahead_hours + 1)
        ]
        extreme_ahead = LEVEL_EXTREME in ahead
        expensive_ahead = extreme_ahead or LEVEL_EXPENSIVE in ahead

        mode = "normal"
        off = False
        if agg > 0 and rules.enabled and price is not None:
            if level == LEVEL_EXTREME and zone.extreme_off and not protected:
                off = True
                reasons.append(R_EXTREME_OFF)
            elif level in (LEVEL_EXPENSIVE, LEVEL_EXTREME) and not protected:
                target -= zone.price_shift * agg
                reasons.append(R_EXPENSIVE)
                mode = "save"
            elif level in (LEVEL_EXPENSIVE, LEVEL_EXTREME):
                pass  # protected window: keep the schedule, never pre-heat in a costly hour
            elif extreme_ahead and not away_active:
                target += 2 * zone.preheat * min(agg, 1.0)
                reasons.append(R_PREHEAT_EXTREME)
                mode = "preheat"
            elif level == LEVEL_CHEAP and expensive_ahead and not away_active:
                target += zone.preheat * min(agg, 1.0)
                reasons.append(R_PREHEAT)
                mode = "preheat"
            if protected and level in (LEVEL_EXPENSIVE, LEVEL_EXTREME):
                reasons.append(R_PROTECTED)

        if mode == "normal":
            base = 0.0 if zone.is_percent else zone.comfort
            if R_AWAY in reasons:
                mode = "away"
            elif not zone.is_percent and sched > base + 0.01:
                mode = "comfort"
            elif not zone.is_percent and sched < base - 0.01:
                mode = "setback"
        if off:
            mode = "off"

        target = _clamp(target, zone.min_value, zone.max_value)
        if not zone.is_percent and not off and target < reference and target < zone.safety_min_temp:
            # price lowering never takes a room below the safety floor (or the away floor, if lower)
            target = max(target, min(reference, zone.safety_min_temp))
        slots.append(
            PlanHour(
                start=t,
                price=price,
                level=level,
                scheduled=round(sched, 2),
                target=None if off else round(target * 2) / 2 if not zone.is_percent else round(target),
                mode=mode,
                reasons=reasons,
            )
        )

    if zone.is_percent:
        _cap_off_runs(zone, slots, 0)
        _balance_percent(zone, slots)
        slots = slots[n_pre_slots:]
    _cap_off_runs(zone, slots, initial_off_run)
    refs =[comfort_reference(zone, s) for s in slots]
    used = "rules"
    warm = warmup_targets(zone, slots, refs, room_temp, outdoor or {}, outdoor_default, solar)
    if method != "optimize" and room_temp is not None and not zone.is_percent:
        _comfort_guard(zone, slots, refs, room_temp, outdoor or {}, outdoor_default, solar)
    optimized = False
    if method == "optimize" and room_temp is not None:
        from .optimizer import optimize_zone  # local import: optimizer imports planner

        # Nothing worth shifting (e.g. Norgespris + a small day/night grid
        # difference): the rules call every hour "normal" and the schedule is kept
        # as is – the optimiser would still trade comfort for energy.
        horizon = [prices[s.start] for s in slots if s.start in prices]
        flat = bool(horizon) and max(horizon) - min(horizon) < rules.min_spread
        try:
            if not flat or kw_cap:
                opt_prices = prices
                if flat:
                    # nothing worth shifting for price: plan for the capacity budget only
                    mean = sum(horizon) / len(horizon)
                    opt_prices = {h: mean for h in prices}
                slots = optimize_zone(
                    zone, slots, opt_prices, room_temp, outdoor or {}, outdoor_default,
                    solar=solar, kw_cap=kw_cap, weights=optimizer_weights,
                    initial_off_run=initial_off_run, warmup=warm, peak_only=flat,
                )
                used = "optimize"
                optimized = True
        except Exception:  # noqa: BLE001 - never lose the plan: keep the rules result
            pass
    if warm and not optimized:
        _apply_warmup(zone, slots, warm)
    for i, goal in warm.items():
        slots[i].warmup = goal
    if adjust is not None:
        # e.g. capacity-tariff cuts decided across all zones; simulated like the rest
        adjust(slots)
    _simulate_with(zone, slots, room_temp, outdoor or {}, outdoor_default, solar)
    if kw_cap and optimized and room_temp is not None and not zone.is_percent:
        if _coast(zone, slots, kw_cap, room_temp):
            _simulate_with(zone, slots, room_temp, outdoor or {}, outdoor_default, solar)

    # Baseline: same schedule (with its warm-up), no price logic, nobody away.
    base_slots = [
        PlanHour(
            start=s.start,
            price=s.price,
            level=s.level,
            scheduled=s.scheduled,
            target=max(s.scheduled, s.warmup) if s.warmup is not None else s.scheduled,
            mode="normal",
            reasons=[],
        )
        for s in slots
    ]
    # Same model, start temperature and weather as the plan, so pre-heat energy counts.
    _simulate_with(zone, base_slots, room_temp, outdoor or {}, outdoor_default, solar)

    # Cost and savings over the hours that have a price (the plan often runs past
    # the last published price); energy over the whole horizon.
    priced = [i for i, s in enumerate(slots) if s.cost is not None and base_slots[i].cost is not None]

    def total(items: list[PlanHour]) -> tuple[float | None, float]:
        kwh = sum(i.kwh for i in items)
        if not priced:
            return None, kwh
        return sum(items[i].cost for i in priced), kwh  # type: ignore[misc]

    cost, kwh = total(slots)
    bcost, bkwh = total(base_slots)
    return Plan(
        zone.zone_id, slots, cost, bcost, round(kwh, 3), round(bkwh, 3),
        comfort_deficit=comfort_deficit(zone, slots, refs),
        baseline_comfort_deficit=comfort_deficit(zone, base_slots, refs),
        method=used,
    )


def baseline_hour(
    zone: ZoneConfig,
    schedule: dict[int, list[float]],
    overrides: dict[str, list[float]],
    prices: dict[datetime, float],
    start: datetime,
    *,
    room_temp: float | None,
    queue: list[float] | None = None,
    outdoor: dict[datetime, float] | None = None,
    outdoor_default: float = 5.0,
    solar: dict[datetime, float] | None = None,
    warmup: float | None = None,
) -> tuple[PlanHour, list[float]]:
    """One hour of the schedule alone (no price logic, nobody away).

    ``warmup``: the plan hour's optimal-start target (``PlanHour.warmup``), which the
    schedule alone would also heat to.

    Returns the hour (``cost``, ``kwh``, ``predicted`` = where the room ends) and
    the heat still on its way through a slow floor (``lag_hours``) at the end of
    the hour. Pass both back in (``room_temp``, ``queue``) for the next hour to
    carry this "shadow" house along; ``queue=None`` starts it as if the
    thermostat had been holding ``room_temp``.
    """
    t = hour_key(start)
    sched, _ = scheduled_value(zone, schedule, overrides, t)
    target = _clamp(max(sched, warmup) if warmup is not None else sched, zone.min_value, zone.max_value)
    slot = PlanHour(
        start=t, price=rekey_hours(prices).get(t), level=LEVEL_NORMAL, scheduled=round(sched, 2),
        target=round(target) if zone.is_percent else round(target * 2) / 2, mode="normal", reasons=[],
    )
    return simulate_hour(zone, slot, room_temp=room_temp, queue=queue, outdoor=outdoor,
                         outdoor_default=outdoor_default, solar=solar)


def simulate_hour(
    zone: ZoneConfig,
    slot: PlanHour,
    *,
    room_temp: float | None,
    queue: list[float] | None = None,
    outdoor: dict[datetime, float] | None = None,
    outdoor_default: float = 5.0,
    solar: dict[datetime, float] | None = None,
) -> tuple[PlanHour, list[float]]:
    """Simulate one hour with ``slot``'s target from a carried model state.

    Returns a copy of the slot with ``duty``/``kwh``/``cost``/``predicted``
    filled in and the dead-time queue at the end (see :func:`baseline_hour`).
    """
    out = replace(slot, reasons=list(slot.reasons))
    q = list(queue) if queue is not None else []
    _simulate(zone, [out], room_temp, rekey_hours(outdoor), outdoor_default,
              solar=rekey_hours(solar) if solar else None, queue=q)
    return out, q


def _simulate_with(
    zone: ZoneConfig,
    slots: list[PlanHour],
    room_temp: float | None,
    outdoor: dict[datetime, float],
    outdoor_default: float,
    solar: dict[datetime, float] | None,
) -> None:
    """Call :func:`_simulate`, passing ``solar`` when the simulation supports it."""
    if solar:
        _simulate(zone, slots, room_temp, outdoor, outdoor_default, solar=solar)
    else:
        _simulate(zone, slots, room_temp, outdoor, outdoor_default)


def explain_savings(plan: Plan, lang: str = "nb", currency: str | None = None) -> str:
    """An honest one-line summary of what price control does to cost, energy and comfort.

    Small savings are called out as "almost nothing", extra energy from
    pre-heating and any loss of comfort are always mentioned.
    """
    nb = not lang.lower().startswith("en")
    cur = currency if currency is not None else ("kr" if nb else "")

    def num(v: float, d: int) -> str:
        txt = f"{v:.{d}f}"
        return txt.replace(".", ",") if nb else txt

    def money(v: float) -> str:
        return f"{num(v, 2)} {cur}".strip()

    sav = plan.savings
    if sav is None:
        return (
            "Mangler priser – kan ikke beregne besparelse"
            if nb
            else "No prices – savings cannot be estimated"
        )
    pct = plan.savings_pct or 0.0
    extra = plan.extra_kwh
    colder = None
    if plan.comfort_deficit is not None and plan.baseline_comfort_deficit is not None:
        colder = plan.comfort_deficit - plan.baseline_comfort_deficit

    if sav < -0.005:
        return (
            f"Prisstyring koster ca. {money(-sav)} mer i dag"
            if nb
            else f"Price control costs about {money(-sav)} more today"
        )
    if sav < 0.5 and pct < 3.0:
        return (
            "Prisstyring sparer nesten ingenting i dag"
            if nb
            else "Price control saves almost nothing today"
        )
    parts = [f"Sparer ca. {money(sav)}" if nb else f"Saves about {money(sav)}"]
    if extra >= 0.05:
        parts.append(f"men bruker {num(extra, 1)} kWh mer" if nb else f"but uses {num(extra, 1)} kWh more")
    elif extra <= -0.05:
        parts.append(f"og bruker {num(-extra, 1)} kWh mindre" if nb else f"and uses {num(-extra, 1)} kWh less")
    if colder is not None and colder >= 0.5:
        parts.append(
            f"og rommet er {num(colder, 1)} gradtimer kaldere"
            if nb
            else f"and the room is {num(colder, 1)} degree-hours colder"
        )
    return ", ".join(parts)


def warmup_targets(
    zone: ZoneConfig,
    slots: list[PlanHour],
    refs: list[float],
    room_temp: float | None,
    outdoor: dict[datetime, float],
    outdoor_default: float,
    solar: dict[datetime, float] | None,
) -> dict[int, float]:
    """Optimal start: ``{slot index: target}`` for the hours before each upward schedule step.

    A thermostat that only switches to the new target at the step leaves a slow room
    (heavy floor, long dead time) below it for hours (the benchmark measured 3–6 °C·h a
    day on a concrete floor). For every rise of the comfort reference the room model is
    run on the schedule from the current temperature, and the step's target is moved
    forward hour by hour until the modelled room reaches it (within
    ``WARMUP_TOLERANCE``) when the step begins – at most the floor's dead time plus
    ``WARMUP_MAX_HOURS``, and never before now. Setpoint zones only (percent zones have
    no temperature to aim for); without a room reading the room is assumed to be at its
    current reference (the thermostat holding it).
    """
    if not zone.warmup or zone.is_percent or len(slots) < 2:
        return {}
    if room_temp is None:
        room_temp = refs[0]
    work = [replace(s, target=r, reasons=[], mode="normal") for s, r in zip(slots, refs)]
    lead_max = WARMUP_MAX_HOURS + int(ceil(zone.thermal.lag_hours or 0.0))

    def one(slot: PlanHour, temp: float, queue: list[float] | None) -> tuple[float, list[float]]:
        out = replace(slot, reasons=[])
        q = list(queue) if queue is not None else []
        _simulate(zone, [out], temp, outdoor, outdoor_default, solar=solar, queue=q)
        return (out.predicted if out.predicted is not None else temp), q

    def states(first: int, temp: float, queue: list[float] | None, out: list) -> None:
        # out[j] = (temperature, dead-time queue) at the start of slot j
        for j in range(first, len(work)):
            out[j] = (temp, queue)
            temp, queue = one(work[j], temp, queue)
        out[len(work)] = (temp, queue)

    st: list = [None] * (len(work) + 1)
    states(0, room_temp, None, st)
    warm: dict[int, float] = {}
    for i in range(1, len(work)):
        goal = refs[i]
        if goal <= refs[i - 1] + 0.01 or st[i][0] >= goal - WARMUP_TOLERANCE:
            continue
        chosen = 0
        for k in range(1, min(lead_max, i) + 1):
            temp, q = st[i - k]
            for j in range(i - k, i):
                temp, q = one(replace(work[j], target=max(work[j].target or goal, goal)), temp, q)
            chosen = k
            if temp >= goal - WARMUP_TOLERANCE:
                break
        if not chosen:
            continue
        for j in range(i - chosen, i):
            if work[j].target is not None and work[j].target < goal:
                work[j].target = goal
                warm[j] = goal
        states(i - chosen, st[i - chosen][0], st[i - chosen][1], st)
    return warm


PERCENT_WINDOW = 3  # hours before a lowered hour that a percent zone pre-heats in


def _balance_percent(zone: ZoneConfig, slots: list[PlanHour]) -> None:
    """Percent zones: move heat into the hours just before a lowered hour, never remove it.

    A regulator has no sensor, so nothing makes up afterwards for what an expensive hour
    took: the rules' −``price_shift`` there (and OFF in extreme hours) left the room
    colder, and heat "given back" in the day's cheapest hours (often the night, or after
    the cut) came too early or too late to help (the benchmark's bathroom: up to six
    times the plain schedule's comfort deficit in a cold spell). Now the percentage-hours
    taken from each lowered hour are put back in the ``PERCENT_WINDOW`` hours before it
    (cheapest first, at most ``price_shift`` points above the schedule each; the rules'
    own pre-heat in that window counts), so the room is warm when the heat is taken.
    What cannot be put back there is not taken: the cut is made smaller (an OFF hour
    becomes a lowered one).
    """
    used: dict[int, float] = {}  # rule pre-heat already counted against a cut
    for c, s in enumerate(slots):
        if R_AWAY in s.reasons or not (R_EXPENSIVE in s.reasons or R_EXTREME_OFF in s.reasons):
            continue
        target = 0.0 if s.target is None else s.target
        debt = s.scheduled - target
        if debt <= 0.5:
            continue
        window = [j for j in range(max(0, c - PERCENT_WINDOW), c)
                  if slots[j].target is not None and R_AWAY not in slots[j].reasons
                  and R_EXPENSIVE not in slots[j].reasons and R_EXTREME_OFF not in slots[j].reasons]
        for j in window:  # the rules' pre-heat for this cut
            p = slots[j]
            extra = max(0.0, p.target - p.scheduled) - used.get(j, 0.0)  # type: ignore[operator]
            take = min(extra, debt)
            if take > 0:
                used[j] = used.get(j, 0.0) + take
                debt -= take
        for j in sorted(window, key=lambda j: (slots[j].price is None, slots[j].price or 0.0, -j)):
            if debt <= 0.5:
                break
            p = slots[j]
            room = min(zone.max_value - p.target, zone.price_shift - max(0.0, p.target - p.scheduled))  # type: ignore[operator]
            add = round(min(room, debt))
            if add <= 0:
                continue
            p.target += add  # type: ignore[operator]
            used[j] = used.get(j, 0.0) + add
            debt -= add
            if R_PREHEAT not in p.reasons:
                p.reasons.append(R_PREHEAT)
            p.mode = "preheat"
        if debt > 0.5:
            # not enough room before it: take only what was put back
            s.target = _clamp(round(target + debt), zone.min_value, zone.max_value)
            if R_EXTREME_OFF in s.reasons:
                s.reasons = [r for r in s.reasons if r != R_EXTREME_OFF] + [R_EXPENSIVE]
            if s.target >= s.scheduled - 0.5:
                s.target = s.scheduled
                s.reasons = [r for r in s.reasons if r != R_EXPENSIVE]
                s.mode = _plain_mode(zone, s)
            else:
                s.mode = "save"


COAST_DROP = 0.5  # °C a coasting capacity hour's setpoint goes under the plan's target
COAST_MARGIN = 0.15  # °C the room must be planned above its target to count as coasting


def _coast(zone: ZoneConfig, slots: list[PlanHour], kw_cap: dict[datetime, float], room_temp: float) -> bool:
    """Capacity hours the plan coasts through on stored heat: set the thermostat lower.

    A floor thermostat does not see the plan: it switches on when *its* reading drops
    0.3 °C under the setpoint, and a floor sensor reads low once the stored heat is
    used up (it anticipates the cooling), so a room planned to idle at 21.4 °C with
    the setpoint at 21 still drew 2 kW in the cooking hour in the benchmark and set
    the day's peak. Where an hour has a kW budget, the plan draws (almost) nothing and
    the room is planned to stay ``COAST_MARGIN`` above the target, the setpoint goes
    ``COAST_DROP`` lower (never below the schedule minus ``energy.PEAK_SLACK`` nor the
    safety floor): the room's comfort is the plan's stored heat, the thermostat only
    stands by.
    """
    changed = False
    prev = room_temp
    for s in slots:
        start_t, prev = prev, s.predicted if s.predicted is not None else prev
        if s.start not in kw_cap or s.target is None or s.duty > 0.05 or s.predicted is None:
            continue
        if min(start_t, s.predicted) < s.target + COAST_MARGIN:
            continue
        floor = max(zone.min_value, zone.safety_min_temp, comfort_reference(zone, s) - 0.5)
        new = max(round((s.target - COAST_DROP) * 2) / 2, math_ceil_half(floor))
        if new < s.target - 1e-9:
            s.target = new
            s.mode = "save"
            if R_PEAK not in s.reasons:
                s.reasons.append(R_PEAK)
            changed = True
    return changed


def math_ceil_half(v: float) -> float:
    """Smallest 0.5 °C step at or above ``v``."""
    return ceil(v * 2 - 1e-9) / 2


def _apply_warmup(zone: ZoneConfig, slots: list[PlanHour], warm: dict[int, float]) -> None:
    """Rules mode: raise the warm-up hours to the coming target (also over price lowering)."""
    for i, goal in warm.items():
        s = slots[i]
        if s.target is None or s.target >= goal - 0.01:
            continue
        s.target = round(goal * 2) / 2
        s.reasons = [r for r in s.reasons if r not in (R_EXPENSIVE, R_EXTREME_OFF)] + [R_WARMUP]
        s.mode = "preheat"


COMFORT_BUDGET = 1.0  # °C·h per day of recovery tail the price rules may add (rules mode)


def _comfort_guard(
    zone: ZoneConfig,
    slots: list[PlanHour],
    refs: list[float],
    room_temp: float,
    outdoor: dict[datetime, float],
    outdoor_default: float,
    solar: dict[datetime, float] | None,
) -> None:
    """Rules mode: back off price reductions the room model says cost too much.

    The rules lower expensive hours by a fixed step whatever the weather; in a
    cold spell a slow room needs hours to recover from that (the simulation
    measured about +3 °C·h a day at -15 °C). Here the plan is simulated and

    * an OFF hour after which the room would end below ``safety_min_temp``
      (within the floor's dead time) is turned back into a lowered setpoint, and
    * while the comfort deficit exceeds the schedule's own by more than the
      setback the rules intend (°C lowered × hours) plus ``COMFORT_BUDGET`` per
      day – i.e. the room would take long to recover – every price-lowered
      hour is raised 0.5 °C (OFF hours count as intended).
    """
    ref_slots = [replace(s, target=r, reasons=[], mode="normal") for s, r in zip(slots, refs)]
    _simulate_with(zone, ref_slots, room_temp, outdoor, outdoor_default, solar)
    base = comfort_deficit(zone, ref_slots, refs) or 0.0
    budget = COMFORT_BUDGET * len(slots) / 24
    lag = int(ceil(zone.thermal.lag_hours or 0.0))
    for _ in range(8):
        _simulate_with(zone, slots, room_temp, outdoor, outdoor_default, solar)
        changed = False
        for i, s in enumerate(slots):
            if s.target is not None:
                continue
            after = [x.predicted for x in slots[i : i + lag + 1] if x.predicted is not None]
            if after and min(after) < zone.safety_min_temp:
                t = _clamp(s.scheduled - zone.price_shift, zone.min_value, zone.max_value)
                s.target = round(max(t, min(s.scheduled, zone.safety_min_temp)) * 2) / 2
                s.mode = "save"
                s.reasons = [r for r in s.reasons if r != R_EXTREME_OFF] + [R_EXPENSIVE, R_SAFETY]
                changed = True
        if changed:
            continue
        intended = sum(
            max(0.0, r - (s.predicted if s.target is None else s.target))  # type: ignore[operator]
            for s, r in zip(slots, refs)
            if s.target is not None or s.predicted is not None
        )
        if (comfort_deficit(zone, slots, refs) or 0.0) - base <= intended + budget:
            break
        for s, r in zip(slots, refs):
            # (OFF hours are the user's explicit extreme_off choice: only the safety check above)
            if s.target is None or R_EXPENSIVE not in s.reasons or s.target >= r - 0.01:
                continue
            s.target = min(r, s.target + 0.5)
            changed = True
            if s.target >= r - 0.01:
                s.reasons = [x for x in s.reasons if x != R_EXPENSIVE]
                s.mode = _plain_mode(zone, s)
        if not changed:
            break


def _plain_mode(zone: ZoneConfig, s: PlanHour) -> str:
    base = 0.0 if zone.is_percent else zone.comfort
    if R_AWAY in s.reasons:
        return "away"
    if not zone.is_percent and s.scheduled > base + 0.01:
        return "comfort"
    if not zone.is_percent and s.scheduled < base - 0.01:
        return "setback"
    return "normal"


def _cap_off_runs(zone: ZoneConfig, slots: list[PlanHour], initial_off_run: int = 0) -> None:
    """Never leave heating off for more than ``max_off_hours`` in a row.

    ``initial_off_run`` hours of OFF right before the first slot count too. The
    replacement setpoint is never below ``safety_min_temp`` (unless the schedule is).
    """
    run = max(0, int(initial_off_run))
    for s in slots:
        if s.target is None:
            run += 1
            if run > zone.max_off_hours:
                t = _clamp(s.scheduled - zone.price_shift, zone.min_value, zone.max_value)
                if not zone.is_percent:
                    t = max(t, min(s.scheduled, zone.safety_min_temp))
                s.target = round(t) if zone.is_percent else round(t * 2) / 2
                s.mode = "save"
                s.reasons = [r for r in s.reasons if r != R_EXTREME_OFF] + [R_EXPENSIVE]
        else:
            run = 0


def _simulate(
    zone: ZoneConfig,
    slots: list[PlanHour],
    room_temp: float | None,
    outdoor: dict[datetime, float],
    outdoor_default: float,
    *,
    solar: dict[datetime, float] | None = None,
    queue: list[float] | None = None,
) -> None:
    """Fill predicted temperature, duty, kWh and cost for each slot (in place).

    ``queue`` (optional, in/out): duty commanded but not yet felt (dead time),
    one entry per quarter hour. Used as the starting state when it has the
    right length, and replaced by the state at the end.

    ``solar`` maps hour starts to a 0..1 solar factor (see ``solar.py``); missing
    hours count as no sun. The heater's effect is delayed by the model's
    ``lag_hours`` (dead time of heavy floors): the duty commanded now warms the
    room ``lag_hours`` later. A setpoint thermostat is assumed to anticipate
    that delay (it controls on the predicted temperature once the queued heat
    has arrived), which is how floor-sensor thermostats behave in practice.
    """
    model = zone.thermal
    temp = room_temp
    substeps = 4
    dt = 1 / substeps
    gross = model.gross_rate
    n_lag = max(0, int(round((model.lag_hours or 0.0) / dt)))
    fifo: deque[float] | None = None  # duty commanded but not yet felt

    def passive(t: float, t_out: float, sol: float) -> float:
        """°C/h drift without heating (loss never warms a room colder than outside)."""
        return -max(0.0, (t - t_out) / model.tau_hours) + model.solar_gain * sol

    def hold_duty(t: float, t_out: float, sol: float) -> float:
        if gross <= 0:
            return 1.0
        return _clamp(-passive(t, t_out, sol) / gross, 0.0, 1.0)

    for s in slots:
        t_out = outdoor.get(s.start, outdoor_default)
        sol = (solar or {}).get(s.start, 0.0) or 0.0
        duty_sum = 0.0
        for _ in range(substeps):
            if zone.is_percent:
                duty = 0.0 if s.target is None else _clamp(s.target / 100.0, 0, 1)
                if temp is not None:
                    if fifo is None:
                        fifo = deque(queue if queue and len(queue) == n_lag else [duty] * n_lag)
                    fifo.append(duty)
                    temp = model.step(temp, fifo.popleft(), t_out, dt, sol)
            elif temp is None:
                # no temperature known: assume the setpoint is held
                duty = 0.0 if s.target is None else 0.35
            else:
                if fifo is None:
                    if queue and len(queue) == n_lag:
                        fifo = deque(queue)
                    else:
                        # assume the thermostat was holding the start temperature
                        fifo = deque([hold_duty(temp, t_out, sol)] * n_lag)
                # temperature once the heat already queued has arrived
                pred = temp
                for d in fifo:
                    pred = model.step(pred, d, min(t_out, pred), dt, sol)
                drift = passive(pred, t_out, sol)
                hold = hold_duty(pred, t_out, sol)
                snap: float | None = None
                if s.target is None or pred > s.target + 0.05:
                    duty = 0.0
                elif pred < s.target - 0.05:
                    rise = gross + drift
                    if rise <= 0:
                        duty = 1.0
                    else:
                        full = rise * dt
                        need = s.target - pred
                        frac = _clamp(need / full, 0.0, 1.0) if full > 0 else 1.0
                        # heat for frac of the substep, then hold
                        duty = frac + (1 - frac) * hold
                        snap = min(s.target, temp + full)
                else:
                    duty = hold
                    if drift <= 0 and -drift < gross:
                        snap = s.target
                fifo.append(duty)
                applied = fifo.popleft()
                if n_lag == 0 and snap is not None:
                    temp = snap  # thermostat holds the target exactly
                else:
                    temp = model.step(temp, applied, min(t_out, temp), dt, sol)
            duty_sum += duty
        s.duty = round(duty_sum / substeps, 3)
        s.kwh = round(s.duty * zone.heater_kw, 3)
        s.cost = None if s.price is None else round(s.kwh * s.price, 4)
        s.predicted = None if temp is None else round(temp, 2)
    if queue is not None:
        queue[:] = list(fifo or [])


# --------------------------------------------------------------------------- learning

LAG_CANDIDATES: tuple[float, ...] = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0)
QUIET_HOURS = 3.0  # no heating for this long = no delayed heat still arriving
QUALITY_GOOD = 0.3  # °C RMSE of 1-hour-ahead predictions
QUALITY_FAIR = 0.6
MIN_RMSE_POINTS = 10


@dataclass
class Sample:
    """One observation used for fitting the thermal model.

    ``duty`` is the share of the following interval the heater was on (0..1);
    when unknown, ``heating`` (on/off at ``when``) is used instead. ``solar``
    is the 0..1 solar factor at ``when`` (see ``solar.py``).
    """

    when: datetime
    room: float
    outdoor: float
    heating: bool
    solar: float = 0.0
    duty: float | None = None

    @property
    def effective_duty(self) -> float:
        if self.duty is not None:
            return _clamp(self.duty, 0.0, 1.0)
        return 1.0 if self.heating else 0.0


class _Series:
    """Time-sorted samples with fast "value at time" lookups."""

    def __init__(self, pts: list[Sample]) -> None:
        self.pts = pts
        self.ts = [p.when.timestamp() for p in pts]

    def index_at(self, ts: float, tol: float = 3600.0) -> int | None:
        i = bisect_right(self.ts, ts + 1e-6) - 1
        if i < 0 or ts - self.ts[i] > tol:
            return None
        return i

    def duty_at(self, ts: float) -> float | None:
        i = self.index_at(ts)
        return None if i is None else self.pts[i].effective_duty

    def quiet(self, i: int) -> bool:
        """No heating at sample ``i`` nor in the ``QUIET_HOURS`` before it."""
        ts = self.ts[i]
        lo = self.index_at(ts - QUIET_HOURS * 3600)
        if lo is None:
            return False
        return all(self.pts[k].effective_duty < 0.05 for k in range(lo, i + 1))


def _pairs(series: _Series) -> list[tuple[int, int, float, float]]:
    """Consecutive sample pairs 10–90 minutes apart: (i, j, hours, slope °C/h)."""
    out = []
    pts = series.pts
    for i in range(len(pts) - 1):
        hours = (series.ts[i + 1] - series.ts[i]) / 3600
        if 1 / 6 <= hours <= 1.5:
            out.append((i, i + 1, hours, (pts[i + 1].room - pts[i].room) / hours))
    return out


def _one_hour_errors(model: ThermalModel, series: _Series) -> list[float]:
    """Errors (°C) of 1-hour-ahead predictions started from each sample."""
    pts, ts = series.pts, series.ts
    lag_s = (model.lag_hours or 0.0) * 3600
    errors: list[float] = []
    for i in range(len(pts)):
        goal = ts[i] + 3600
        j = bisect_left(ts, goal - 600, lo=i + 1)
        if j >= len(pts) or abs(ts[j] - goal) > 600:
            continue
        temp = pts[i].room
        ok = True
        for k in range(i, j):
            hours = (ts[k + 1] - ts[k]) / 3600
            if hours > 1.5:
                ok = False
                break
            duty = series.duty_at(ts[k] - lag_s) if lag_s else pts[k].effective_duty
            if duty is None:
                duty = pts[k].effective_duty
            n = max(1, ceil(hours / 0.25))
            for _ in range(n):
                temp = model.step(temp, duty, pts[k].outdoor, hours / n, pts[k].solar)
        if ok:
            errors.append(temp - pts[j].room)
    return errors


def model_rmse(model: ThermalModel, samples: list[Sample]) -> float | None:
    """RMSE (°C) of 1-hour-ahead predictions on ``samples``; None if too few."""
    series = _Series(sorted(samples, key=lambda s: s.when))
    errors = _one_hour_errors(model, series)
    if len(errors) < MIN_RMSE_POINTS:
        return None
    return round(sqrt(sum(e * e for e in errors) / len(errors)), 3)


def model_quality(model: ThermalModel | None) -> str:
    """"good" | "fair" | "poor" | "unknown" from the model's fit error."""
    if model is None or model.rmse is None:
        return "unknown"
    if model.rmse < QUALITY_GOOD:
        return "good"
    if model.rmse < QUALITY_FAIR:
        return "fair"
    return "poor"


def _snap_lag(hours: float) -> float:
    return min(LAG_CANDIDATES, key=lambda c: abs(c - (hours or 0.0)))


def blend_models(a: ThermalModel, b: ThermalModel, weight_a: float = 0.5) -> ThermalModel:
    """Parameter-wise blend of two models (bookkeeping fields from ``a``)."""
    w = _clamp(weight_a, 0.0, 1.0)

    def mix(x: float, y: float) -> float:
        return w * x + (1 - w) * y

    return replace(
        a,
        gross_rate=round(mix(a.gross_rate, b.gross_rate), 3),
        tau_hours=round(mix(a.tau_hours, b.tau_hours), 1),
        solar_gain=round(mix(a.solar_gain, b.solar_gain), 3),
        lag_hours=round(mix(a.lag_hours or 0.0, b.lag_hours or 0.0) * 2) / 2,
    )


def fit_thermal(
    samples: list[Sample],
    prior: ThermalModel | None = None,
    *,
    blend_poor: bool = True,
) -> ThermalModel:
    """Fit the room model to observed history.

    * ``tau_hours`` from cooling periods (no heating for ``QUIET_HOURS``, sun down;
      relaxed step by step when there are too few),
    * ``solar_gain`` from quiet daytime periods: warming beyond the cooling
      model, regressed on the solar factor (°C/h at factor 1, 0..1.5),
    * ``lag_hours`` (dead time) as the candidate in ``LAG_CANDIDATES`` that best
      explains the room around heating switches (needs ≥ 3 onsets),
    * ``gross_rate`` from heating periods given the chosen lag,
    * ``rmse`` of 1-hour-ahead predictions on the samples.

    Falls back to the prior (or defaults) per parameter when there is too
    little data, clamps everything to plausible ranges, and if the fit is still
    poor (see :func:`model_quality`) blends it 50/50 with the prior, keeping the
    measured ``rmse`` so the UI can show low confidence.
    """
    prior = prior or ThermalModel()
    pts = sorted(samples, key=lambda s: s.when)
    series = _Series(pts)
    pairs = _pairs(series)

    def diff(i: int) -> float:
        return pts[i].room - pts[i].outdoor

    def sun(i: int, j: int) -> float:
        return (pts[i].solar + pts[j].solar) / 2

    # --- tau: free cooling
    taus: list[float] = []
    quiet = [series.quiet(i) for i in range(len(pts))]
    for cond in (
        lambda i: quiet[i] and pts[i].solar < 0.05,
        lambda i: pts[i].effective_duty < 0.05 and pts[i].solar < 0.05,
        lambda i: pts[i].effective_duty < 0.05,
    ):
        taus = [diff(i) / -slope for i, _, _, slope in pairs if cond(i) and slope < 0 and diff(i) >= 3]
        if len(taus) >= 6:
            break
    tau_ok = len(taus) >= 6
    tau = _clamp(median(taus) if tau_ok else prior.tau_hours, 8.0, 150.0)

    # --- solar gain: quiet daytime warming beyond the cooling model
    sx = sxy = 0.0
    n_sol = 0
    for i, j, _, slope in pairs:
        s = sun(i, j)
        if quiet[i] and s >= 0.1:
            resid = slope + diff(i) / tau
            sx += s * s
            sxy += s * resid
            n_sol += 1
    sol_ok = n_sol >= 8 and sx > 0
    gain = _clamp(sxy / sx if sol_ok else prior.solar_gain, 0.0, 1.5)

    # --- lag and gross rate: what is left is the heater's (delayed) effect
    resid = [
        (i, slope + diff(i) / tau - gain * sun(i, j)) for i, j, _, slope in pairs
    ]

    def gross_for(lag: float) -> tuple[float | None, list[tuple[int, float, float]]]:
        rows = []
        for i, y in resid:
            d = series.duty_at(series.ts[i] - lag * 3600) if lag else pts[i].effective_duty
            if d is not None:
                rows.append((i, d, y))
        ratios = [y / d for _, d, y in rows if d >= 0.5]
        return (median(ratios) if len(ratios) >= 6 else None), rows

    # heating switches (on/off) and the pairs right after them
    switches: list[float] = []
    onsets = 0
    for k in range(1, len(pts)):
        a, b = pts[k - 1].effective_duty, pts[k].effective_duty
        if abs(b - a) >= 0.5 and series.ts[k] - series.ts[k - 1] <= 3600:
            switches.append(series.ts[k])
            onsets += b > a
    window_s = (max(LAG_CANDIDATES) + 1) * 3600

    def near_switch(i: int) -> bool:
        k = bisect_right(switches, series.ts[i]) - 1
        return k >= 0 and series.ts[i] - switches[k] <= window_s

    lag = _snap_lag(prior.lag_hours)
    if onsets >= 3:
        errs: dict[float, float] = {}
        for cand in LAG_CANDIDATES:
            g, rows = gross_for(cand)
            if g is None:
                continue
            sel = [(y - g * d) ** 2 for i, d, y in rows if near_switch(i)]
            if len(sel) >= 12:
                errs[cand] = sum(sel) / len(sel)
        if len(errs) >= 2:
            best = min(errs)
            for cand in sorted(errs):
                # a longer lag must explain clearly more than a shorter one
                if errs[cand] < errs[best] * 0.9:
                    best = cand
            lag = best

    g_fit, rows = gross_for(lag)
    gross_ok = g_fit is not None
    g = _clamp(g_fit if gross_ok else prior.gross_rate, 0.2, 4.0)

    model = ThermalModel(
        gross_rate=round(g, 3),
        tau_hours=round(tau, 1),
        learned=tau_ok or gross_ok,
        samples=len(taus) + (sum(1 for _, d, _ in rows if d >= 0.5) if gross_ok else 0),
        solar_gain=round(gain, 3),
        lag_hours=lag,
    )
    model.rmse = model_rmse(model, pts)
    if blend_poor and model.learned and model_quality(model) == "poor":
        model = blend_models(model, prior, 0.5)  # keeps learned=True and rmse
    return model
