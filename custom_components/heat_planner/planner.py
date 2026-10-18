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

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
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
        if not rules.enabled or n < 8:
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

    @property
    def savings(self) -> float | None:
        if self.cost is None or self.baseline_cost is None:
            return None
        return self.baseline_cost - self.cost

    def as_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "hours": [h.as_dict() for h in self.hours],
            "cost": self.cost,
            "baseline_cost": self.baseline_cost,
            "savings": self.savings,
            "kwh": self.kwh,
            "baseline_kwh": self.baseline_kwh,
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
) -> Plan:
    """Build an hourly plan starting at the hour containing ``start``.

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
    _simulate(zone, slots, room_temp, outdoor or {}, outdoor_default)

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
    _simulate(zone, base_slots, room_temp, outdoor or {}, outdoor_default)

    def total(items: list[PlanHour]) -> tuple[float | None, float]:
        kwh = sum(i.kwh for i in items)
        if any(i.cost is None for i in items):
            return None, kwh
        return sum(i.cost for i in items), kwh  # type: ignore[misc]

    cost, kwh = total(slots)
    bcost, bkwh = total(base_slots)
    return Plan(zone.zone_id, slots, cost, bcost, round(kwh, 3), round(bkwh, 3))


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
) -> None:
    """Fill predicted temperature, duty, kWh and cost for each slot (in place)."""
    model = zone.thermal
    temp = room_temp
    substeps = 4
    dt = 1 / substeps
    for s in slots:
        t_out = outdoor.get(s.start, outdoor_default)
        duty_sum = 0.0
        for _ in range(substeps):
            if zone.is_percent:
                duty = 0.0 if s.target is None else _clamp(s.target / 100.0, 0, 1)
                if temp is not None:
                    loss = (temp - t_out) / model.tau_hours
                    temp += (model.gross_rate * duty - loss) * dt
            elif temp is None:
                # no temperature known: assume the setpoint is held
                duty = 0.0 if s.target is None else 0.35
            else:
                loss = max(0.0, (temp - t_out) / model.tau_hours)
                if s.target is None or temp > s.target + 0.05:
                    duty = 0.0
                    temp -= loss * dt
                elif temp < s.target - 0.05:
                    rise = model.gross_rate - loss
                    need = s.target - temp
                    if rise <= 0:
                        duty = 1.0
                        temp += rise * dt
                    else:
                        full = rise * dt
                        frac = _clamp(need / full, 0.0, 1.0) if full > 0 else 1.0
                        # heat for frac of the substep, then hold
                        hold = loss / model.gross_rate if model.gross_rate > 0 else 1.0
                        duty = frac + (1 - frac) * hold
                        temp = min(s.target, temp + full)
                else:
                    duty = _clamp(loss / model.gross_rate, 0.0, 1.0) if model.gross_rate else 0
                    temp = s.target
            duty_sum += duty
        s.duty = round(duty_sum / substeps, 3)
        s.kwh = round(s.duty * zone.heater_kw, 3)
        s.cost = None if s.price is None else round(s.kwh * s.price, 4)
        s.predicted = None if temp is None else round(temp, 2)


# --------------------------------------------------------------------------- learning


@dataclass
class Sample:
    """One observation used for fitting the thermal model."""

    when: datetime
    room: float
    outdoor: float
    heating: bool


def fit_thermal(samples: list[Sample], prior: ThermalModel | None = None) -> ThermalModel:
    """Estimate ``tau_hours`` from cooling periods and ``gross_rate`` from heating periods.

    Uses consecutive sample pairs 10–90 minutes apart. Falls back to the prior
    (or defaults) when there is too little data, and clamps results to
    physically plausible ranges.
    """
    prior = prior or ThermalModel()
    taus: list[float] = []
    gross: list[float] = []
    pts = sorted(samples, key=lambda s: s.when)
    for a, b in zip(pts, pts[1:]):
        hours = (b.when - a.when).total_seconds() / 3600
        if not (1 / 6 <= hours <= 1.5):
            continue
        slope = (b.room - a.room) / hours
        diff = a.room - a.outdoor
        if diff < 3:
            continue
        if not a.heating and slope < 0:
            taus.append(diff / -slope)
        elif a.heating and slope > 0:
            gross.append(slope + diff / (taus and median(taus) or prior.tau_hours))
    tau = median(taus) if len(taus) >= 6 else prior.tau_hours
    tau = _clamp(tau, 8.0, 150.0)
    g = median(gross) if len(gross) >= 6 else prior.gross_rate
    g = _clamp(g, 0.2, 4.0)
    return ThermalModel(
        gross_rate=round(g, 3),
        tau_hours=round(tau, 1),
        learned=len(taus) >= 6 or len(gross) >= 6,
        samples=len(taus) + len(gross),
    )
