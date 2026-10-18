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
from datetime import date, datetime, timedelta
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


# --------------------------------------------------------------------------- prices


def hourly_prices(points: Iterable[PricePoint]) -> dict[datetime, float]:
    """Aggregate any price resolution (15 min, 1 h) to hourly averages."""
    buckets: dict[datetime, list[float]] = {}
    for p in points:
        hour = p.start.replace(minute=0, second=0, microsecond=0)
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
) -> Plan:
    """Build an hourly plan starting at the hour containing ``start``.

    ``method="optimize"`` lets :func:`optimizer.optimize_zone` re-choose the
    targets of the rule plan (needs ``room_temp``; otherwise the rules result is
    used). ``solar`` (0..1 per hour) and ``kw_cap`` (kW per hour) are used by the
    optimiser and, where supported, by the room simulation.

    ``prices`` must be hourly (see :func:`hourly_prices`) and keyed by
    timezone-aware local datetimes. ``away_now`` only affects the first hour
    and the following hours until someone is expected home (``away_until_hour``).
    """
    start = start.replace(minute=0, second=0, microsecond=0)
    levels = classify_prices(prices, rules)
    agg = _clamp(zone.price_aggressiveness, 0.0, 2.0)

    slots: list[PlanHour] = []
    away_active = away_now
    for i in range(hours):
        t = start + timedelta(hours=i)
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

    _cap_off_runs(zone, slots)
    refs = [comfort_reference(zone, s) for s in slots]
    used = "rules"
    if method == "optimize" and room_temp is not None:
        from .optimizer import optimize_zone  # local import: optimizer imports planner

        try:
            slots = optimize_zone(
                zone, slots, prices, room_temp, outdoor or {}, outdoor_default,
                solar=solar, kw_cap=kw_cap, weights=optimizer_weights,
            )
            used = "optimize"
        except Exception:  # noqa: BLE001 - never lose the plan: keep the rules result
            pass
    if adjust is not None:
        # e.g. capacity-tariff cuts decided across all zones; simulated like the rest
        adjust(slots)
    _simulate_with(zone, slots, room_temp, outdoor or {}, outdoor_default, solar)

    # Baseline: same schedule, no price logic, nobody away.
    base_slots = [
        PlanHour(
            start=s.start,
            price=s.price,
            level=s.level,
            scheduled=s.scheduled,
            target=s.scheduled,
            mode="normal",
            reasons=[],
        )
        for s in slots
    ]
    # Same model, start temperature and weather as the plan, so pre-heat energy counts.
    _simulate_with(zone, base_slots, room_temp, outdoor or {}, outdoor_default, solar)

    def total(items: list[PlanHour]) -> tuple[float | None, float]:
        kwh = sum(i.kwh for i in items)
        if any(i.cost is None for i in items):
            return None, kwh
        return sum(i.cost for i in items), kwh  # type: ignore[misc]

    cost, kwh = total(slots)
    bcost, bkwh = total(base_slots)
    return Plan(
        zone.zone_id, slots, cost, bcost, round(kwh, 3), round(bkwh, 3),
        comfort_deficit=comfort_deficit(zone, slots, refs),
        baseline_comfort_deficit=comfort_deficit(zone, base_slots, refs),
        method=used,
    )


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


def _cap_off_runs(zone: ZoneConfig, slots: list[PlanHour]) -> None:
    """Never leave heating off for more than ``max_off_hours`` in a row."""
    run = 0
    for s in slots:
        if s.target is None:
            run += 1
            if run > zone.max_off_hours:
                t = _clamp(s.scheduled - zone.price_shift, zone.min_value, zone.max_value)
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
) -> None:
    """Fill predicted temperature, duty, kWh and cost for each slot (in place).

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
                        fifo = deque([duty] * n_lag)
                    fifo.append(duty)
                    temp = model.step(temp, fifo.popleft(), t_out, dt, sol)
            elif temp is None:
                # no temperature known: assume the setpoint is held
                duty = 0.0 if s.target is None else 0.35
            else:
                if fifo is None:
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
