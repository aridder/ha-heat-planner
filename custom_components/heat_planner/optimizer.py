"""Cost/comfort optimiser for Heat Planner (pure Python, no Home Assistant imports).

Takes the slots the rule stage produced (schedule, overrides, away, protected
window) and re-chooses the target of every hour from a small action set by
dynamic programming over a discretised room-temperature grid. The objective is

    sum(price * kWh) + comfort penalty + peak penalty (+ terminal heat debt)

using the zone's own room model (``ThermalModel.step``). Pre-heating is only
chosen when the model says the extra energy (including the extra losses of a
warmer room) is cheaper than heating in the expensive hours.

The action set honours the zone's sliders: an hour is lowered at most
``price_shift * aggressiveness`` below its comfort reference and raised at most
``2 * preheat * min(aggressiveness, 1)`` above it; OFF is only offered in
"extreme" hours (``extreme_off``, ``max_off_hours``), and a lowered setpoint is
never below ``safety_min_temp``.

Dead time (``ThermalModel.lag_hours``): like :func:`planner._simulate`, a
thermostat is assumed to control on the temperature the room will have once
the heat already queued has arrived. The DP state is therefore the room
temperature ``lag`` hours ahead: an action is paid for in its own hour, but
its comfort (reference, weight, safety floor) is judged ``lag`` hours later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime

from .planner import (
    LEVEL_EXPENSIVE,
    LEVEL_EXTREME,
    R_AWAY,
    R_EXPENSIVE,
    R_EXTREME_OFF,
    R_PREHEAT,
    R_PREHEAT_EXTREME,
    R_PEAK,
    R_PROTECTED,
    R_WARMUP,
    PlanHour,
    ZoneConfig,
    _apply_warmup,
    comfort_reference,
    is_protected,
)

R_OPTIMIZED = "optimized"

SUBSTEPS = 4
_DT = 1.0 / SUBSTEPS
_INF = float("inf")
# candidate step sizes; the zone's own bound (rounded down to the grid) is added
_SETPOINT_STEPS = (0.5, 1.0, 2.0, 3.0, 4.0)
_PERCENT_STEPS = (10.0, 20.0, 30.0, 40.0, 60.0)
_MAX_BINS = 80
PEAK_PREP_HOURS = 4  # peak_only: hours before a budget hour in which heat may be stored


@dataclass
class OptimizerWeights:
    """Weights of the objective.

    Comfort weights are expressed in "reheat units": the cost of raising the
    room 1 °C at the average price of the horizon (``price * heater_kw /
    gross_rate``), so they mean the same thing for every room and price level.
    """

    comfort: float = 1.0  # per (°C below reference)² · h, normal hours
    comfort_high: float = 3.0  # multiplier in comfort/protected hours
    comfort_low: float = 0.3  # multiplier when away or at night
    # plus, per °C · h more than ``band`` below the reference, a linear term: the
    # quadratic penalty alone made dips of 0.3–0.5 °C look nearly free (a capacity
    # budget was then met by letting a room sag instead of storing heat before)
    linear: float = 1.0
    band: float = 0.2  # °C
    # per °C · h above the reference: 0 – the extra losses of a warmer room are already
    # paid in the model; a penalty on top (0.1 before) kept pre-heating out of the cheap
    # hours of a cold spell (benchmark, January spot: 13–17 kr per 14 days)
    above: float = 0.0
    # °C above the reference aimed for in hours with a capacity budget (``kw_cap``)
    cap_margin: float = 0.3
    peak: float | None = None  # per kW above the cap; None = 10 × average price
    terminal: float = 1.0  # reheat units per °C the horizon ends below reference
    min_gain: float = 0.02  # reheat units an hour must gain before leaving the schedule
    night_from: int = 23
    night_to: int = 6
    off_levels: tuple[str, ...] = (LEVEL_EXTREME,)  # price levels where OFF may be chosen


@dataclass
class _Hour:
    out: float
    a: float  # affine substep model: T' = a*T + b0 + duty*bd
    b0: float
    bd: float
    affine: bool
    sol: float
    price: float
    ref: float  # comfort reference (setpoint zones: °C; percent zones: action centre)
    ref_temps: tuple[float, ...]  # reference temperature per substep (lag-shifted)
    w_sub: tuple[float, ...]  # comfort weight per substep (lag-shifted)
    w_comfort: float
    protected: bool
    actions: list[float | None]
    cap: float | None


def optimize_zone(
    zone: ZoneConfig,
    slots: list[PlanHour],
    prices: dict[datetime, float],
    room_temp: float | None,
    outdoor: dict,
    outdoor_default: float,
    solar: dict[datetime, float] | None = None,
    kw_cap: dict[datetime, float] | None = None,
    weights: OptimizerWeights | None = None,
    *,
    initial_off_run: int = 0,
    warmup: dict[int, float] | None = None,
    peak_only: bool = False,
) -> list[PlanHour]:
    """Return new slots with optimised targets (simulation fields are not filled).

    ``initial_off_run`` is the number of hours heating has already been off
    right before the first slot (plans are rebuilt often; the ``max_off_hours``
    run must not restart at every rebuild).

    ``warmup`` (see :func:`planner.warmup_targets`): hours before a schedule step whose
    target an optimal-start thermostat would already raise. The comfort reference
    follows that warm-up (the room is meant to *be* at the new target when the step
    begins) and the warm-up target is one of the actions of those hours; the
    optimiser may still warm up earlier, in cheaper hours.

    ``peak_only``: the prices carry nothing worth shifting (Norgespris) and the run is
    for the capacity budget ``kw_cap`` alone; changed hours are labelled ``peak_limit``
    instead of as price decisions.

    Falls back to ``slots`` unchanged when the room temperature is unknown,
    there are no prices, or ``price_aggressiveness`` is 0.
    """
    agg = max(0.0, min(2.0, zone.price_aggressiveness))

    def fallback() -> list[PlanHour]:
        if warmup:
            _apply_warmup(zone, slots, warmup)  # nothing to optimise: still reach the schedule on time
        return slots

    if room_temp is None or not slots or agg <= 0:
        return fallback()
    known = [prices[s.start] for s in slots if prices.get(s.start) is not None]
    if not known:
        return fallback()
    w = weights or OptimizerWeights()
    solar = solar or {}
    kw_cap = kw_cap or {}
    model = zone.thermal
    mean_price = sum(known) / len(known)
    gross = getattr(model, "gross_rate", 1.0) or 1.0
    unit = max(1e-6, mean_price * zone.heater_kw / gross)  # cost of +1 °C
    peak_w = w.peak if w.peak is not None else 10.0 * mean_price
    percent = zone.is_percent
    max_off = max(0, int(zone.max_off_hours)) if zone.extreme_off else 0

    lag = max(0.0, float(getattr(model, "lag_hours", 0.0) or 0.0))
    n_lag = int(round(lag / _DT))  # substeps, as in planner._simulate
    lag_h = int(round(lag))
    shift_max = max(0.0, zone.price_shift * agg)
    raise_max = max(0.0, 2.0 * zone.preheat * min(agg, 1.0))
    floor = None if percent else zone.safety_min_temp
    n = len(slots)
    prot = [is_protected(zone, s.start) for s in slots]

    # Capacity budget only (nothing worth shifting for price): the plan may only change
    # in the hours that store heat for a budget hour (up to PEAK_PREP_HOURS plus the dead
    # time before it) and in the budget hour itself. Free everywhere, a flat-price run
    # re-shaped the whole day – stopping early in the evening (0.5 °C under the schedule)
    # and pre-heating at night for the next day's cooking peak.
    free: set[int] | None = None
    if peak_only:
        free = set()
        capped = [i for i, s in enumerate(slots) if s.start in kw_cap]
        for c in capped:
            free.update(range(max(0, c - PEAK_PREP_HOURS - lag_h), c + 1))

    cap_hold: set[int] = set()
    hours: list[_Hour] = []
    for i, s in enumerate(slots):
        t_out = outdoor.get(s.start, outdoor_default)
        sol = float(solar.get(s.start, 0.0) or 0.0)
        a, b0, bd, affine = _affine(model, t_out, sol)
        protected = prot[i]
        # with a lag, lowering now cools the room lag hours later: protect that too
        no_lower = protected or prot[min(n - 1, i + lag_h)]
        away = R_AWAY in s.reasons
        ref = comfort_reference(zone, s)
        night = (
            s.start.hour >= w.night_from or s.start.hour < w.night_to
            if w.night_from > w.night_to
            else w.night_from <= s.start.hour < w.night_to
        )
        if s.start in kw_cap and not percent and not away and not night:
            cap_hold.add(i)
        acts = _actions(zone, ref, shift_max, raise_max, no_lower, floor)
        if free is not None and i not in free:
            acts = [round(ref) if percent else round(ref * 2) / 2]
        goal = (warmup or {}).get(i)
        if goal is not None and not percent:
            # comfort first: a warm-up hour holds at least the coming target (if the room
            # was pre-heated earlier, in cheaper hours, the thermostat simply idles)
            g = round(min(zone.max_value, goal) * 2) / 2
            acts = sorted({a for a in acts if a is not None and a >= g - 1e-9} | {g})
        if (
            max_off
            and goal is None
            and not no_lower
            and s.level in w.off_levels
            and (floor is None or ref >= floor)
        ):
            acts.append(None)
        if protected or (not percent and ref > zone.comfort + 0.01 and not away):
            mult = w.comfort_high
        elif away or night:
            mult = w.comfort_low
        else:
            mult = 1.0
        price = prices.get(s.start, s.price)
        hours.append(
            _Hour(
                out=t_out, a=a, b0=b0, bd=bd, affine=affine, sol=sol,
                price=mean_price if price is None else price,
                ref=ref, ref_temps=(), w_sub=(), w_comfort=w.comfort * mult * unit / agg,
                protected=protected, actions=acts, cap=kw_cap.get(s.start),
            )
        )

    # Comfort reference per substep: the temperature the room would actually have
    # on the unmodified schedule (a setpoint cannot be reached instantly, and we
    # must not "buy" comfort the schedule itself would not give).
    temp = room_temp
    ref_flat: list[float] = []
    w_flat: list[float] = []
    warm = warmup or {}
    for i, h in enumerate(hours):
        aim = max(h.ref, warm[i]) if i in warm and not percent else h.ref
        for _ in range(SUBSTEPS):
            temp = _sub(model, h, temp, _duty(model, h, temp, aim, percent))
            ref_flat.append(temp)
            w_flat.append(h.w_comfort)
    # Dead time: the state is the room temperature n_lag substeps ahead, so the
    # action of substep g is judged against the reference of substep g + n_lag.
    last = len(ref_flat) - 1
    for i, h in enumerate(hours):
        idx = [min(last, i * SUBSTEPS + j + n_lag) for j in range(SUBSTEPS)]
        # an hour with a capacity budget is coasted through on stored heat: aim that much
        # above the reference, so a model that is a little off (a room that cools faster
        # than believed) does not wake the thermostat in the peak hour
        lift = w.cap_margin if h.cap is not None else 0.0
        h.ref_temps = tuple(ref_flat[g] + lift for g in idx)
        h.w_sub = tuple(w_flat[g] for g in idx)
        if i in cap_hold and ref_flat[min(last, (i + 1) * SUBSTEPS - 1)] >= h.ref - 0.3:
            # a capacity budget is met with heat stored before the hour, never by setting an
            # occupied room that the schedule holds below it (a thermostat 0.5 °C lower lets
            # the room sag 0.8 °C before it restarts – the benchmark's kitchen while cooking).
            # Only where the schedule itself is still climbing to a new step may a lower
            # setpoint spread that climb.
            keep = [a for a in h.actions if a is not None and a >= h.ref - 0.25]
            h.actions = keep or [round(h.ref * 2) / 2]

    # Temperature grid.
    all_refs = ref_flat
    lo = min(room_temp, min(all_refs), zone.safety_min_temp) - 3.0
    hi = max(room_temp, max(all_refs)) + 3.0
    t_off, t_on, lo_reach, hi_reach = room_temp, room_temp, room_temp, room_temp
    for h in hours:
        for _ in range(SUBSTEPS):
            t_off = _sub(model, h, t_off, 0.0)
            t_on = _sub(model, h, t_on, 1.0)
        lo_reach, hi_reach = min(lo_reach, t_off), max(hi_reach, t_on)
    lo, hi = max(lo, lo_reach - 0.5), min(hi, hi_reach + 0.5)
    if hi - lo < 1.0:
        hi = lo + 1.0
    step = max(0.25, (hi - lo) / (_MAX_BINS - 1))
    nb = int((hi - lo) / step) + 2
    grid = [lo + i * step for i in range(nb)]

    end_ref = ref_flat[-1]
    n_r = max_off + 1
    v_next = [[w.terminal * unit * max(0.0, end_ref - g) for g in grid] for _ in range(n_r)]

    def interp(vals: list[float], temp: float) -> float:
        f = (temp - lo) / step
        if f <= 0:
            return vals[0]
        j = int(f)
        if j >= nb - 1:
            return vals[-1]
        x = f - j
        return vals[j] * (1 - x) + vals[j + 1] * x

    # Backward pass: value per (hour, off-run, temperature bin).
    values: list[list[list[float]]] = [None] * len(hours)  # type: ignore[list-item]
    for i in range(len(hours) - 1, -1, -1):
        h = hours[i]
        v_cur = [[_INF] * nb for _ in range(n_r)]
        for k, temp in enumerate(grid):
            best_on = _INF
            off_cost = None
            for act in h.actions:
                res = _hour_cost(zone, h, temp, act, percent, w, unit, peak_w)
                if res is None:
                    continue
                cost, t_end = res
                if act is None:
                    off_cost = (cost, t_end)
                else:
                    c = cost + interp(v_next[0], t_end)
                    if c < best_on:
                        best_on = c
            for r in range(n_r):
                best = best_on
                if off_cost is not None and r < max_off:
                    c = off_cost[0] + interp(v_next[r + 1], off_cost[1])
                    if c < best:
                        best = c
                v_cur[r][k] = best
        values[i] = v_cur
        v_next = v_cur

    # Forward pass from the actual room temperature.
    out: list[PlanHour] = []
    temp = room_temp
    run = min(max(0, int(initial_off_run)), max_off)
    tie = w.min_gain * unit
    for i, (s, h) in enumerate(zip(slots, hours)):
        nxt = values[i + 1] if i + 1 < len(hours) else None
        best, best_act, best_t = _INF, h.ref, temp
        for act in h.actions:
            if act is None and run >= max_off:
                continue
            res = _hour_cost(zone, h, temp, act, percent, w, unit, peak_w)
            if res is None:
                continue
            cost, t_end = res
            r2 = run + 1 if act is None else 0
            if nxt is not None:
                cost += interp(nxt[r2], t_end)
            else:
                cost += w.terminal * unit * max(0.0, end_ref - t_end)
            if act == h.ref:
                cost -= tie  # only leave the schedule for a real gain
            if cost < best:
                best, best_act, best_t = cost, act, t_end
        run = run + 1 if best_act is None else 0
        temp = best_t
        out.append(_label(zone, s, h, best_act, slots[i + 1 : i + 4], warm.get(i), peak_only))
    return out


# --------------------------------------------------------------------------- helpers


def _actions(
    zone: ZoneConfig,
    ref: float,
    shift_max: float,
    raise_max: float,
    no_lower: bool,
    floor: float | None,
) -> list[float | None]:
    """Targets the optimiser may choose for one hour (OFF is added by the caller).

    Lowered at most ``shift_max`` below ``ref`` (never below ``floor``), raised
    at most ``raise_max`` above it, on a 0.5 °C grid (percent zones: 5/10 points).
    """
    percent = zone.is_percent
    grid = 5.0 if percent else 0.5
    steps = _PERCENT_STEPS if percent else _SETPOINT_STEPS

    def sizes(bound: float) -> list[float]:
        top = int(bound / grid + 1e-9) * grid  # bound rounded down to the grid
        out = [d for d in steps if d <= top + 1e-9]
        if top > 0 and top not in out:
            out.append(top)
        return out

    def snap(v: float) -> float:
        v = min(zone.max_value, max(zone.min_value, v))
        return float(round(v)) if percent else round(v * 2) / 2

    base = snap(ref)
    acts: list[float | None] = [base]
    if not no_lower:
        for d in sizes(shift_max):
            v = snap(ref - d)
            if floor is not None and v < floor - 1e-9:
                v = math.ceil(floor * 2 - 1e-9) / 2  # lowest grid value ≥ floor
            if v < base - 1e-9 and v not in acts:
                acts.append(v)
    for d in sizes(raise_max):
        v = snap(ref + d)
        if v > base + 1e-9 and v not in acts:
            acts.append(v)
    return acts


def _affine(model, t_out: float, sol: float) -> tuple[float, float, float, bool]:
    """Extract T' = a*T + b0 + duty*bd from ``model.step`` for one substep."""
    try:
        b0 = model.step(0.0, 0.0, t_out, _DT, sol)
        a = model.step(1.0, 0.0, t_out, _DT, sol) - b0
        bd = model.step(0.0, 1.0, t_out, _DT, sol) - b0
        probe = model.step(20.0, 0.5, t_out, _DT, sol)
        affine = abs(probe - (a * 20.0 + b0 + 0.5 * bd)) < 1e-9
    except Exception:  # noqa: BLE001 - unexpected model: use direct steps
        return 0.0, 0.0, 0.0, False
    return a, b0, bd, affine


def _sub(model, h: _Hour, temp: float, duty: float) -> float:
    if h.affine:
        return h.a * temp + h.b0 + duty * h.bd
    return model.step(temp, duty, h.out, _DT, h.sol)


def _duty(model, h: _Hour, temp: float, act: float | None, percent: bool) -> float:
    """Heater duty for one substep: fixed for percent zones, thermostat otherwise."""
    if act is None:
        return 0.0
    if percent:
        return min(1.0, max(0.0, act / 100.0))
    if h.affine:
        free, gain = h.a * temp + h.b0, h.bd
    else:
        free = model.step(temp, 0.0, h.out, _DT, h.sol)
        gain = model.step(temp, 1.0, h.out, _DT, h.sol) - free
    return 0.0 if gain <= 0 else min(1.0, max(0.0, (act - free) / gain))


def _hour_cost(
    zone: ZoneConfig,
    h: _Hour,
    temp: float,
    act: float | None,
    percent: bool,
    w: OptimizerWeights,
    unit: float,
    peak_w: float,
) -> tuple[float, float] | None:
    """Cost of one hour with action ``act`` from ``temp``; None if infeasible."""
    model = zone.thermal
    lowered = act is None or act < h.ref - 1e-9
    duty_sum = 0.0
    deficit = 0.0
    above = 0.0
    floor = zone.safety_min_temp
    a, b0, bd, affine = h.a, h.b0, h.bd, h.affine
    refs, wsub = h.ref_temps, h.w_sub
    lin = w.linear  # (w_sub already carries the comfort weight)
    band = w.band
    if affine and not percent and act is not None and bd > 0:
        # fast path (the common case): thermostat on an affine room model
        lim = floor - 1e-6
        for j in range(SUBSTEPS):
            free = a * temp + b0
            if free >= act:
                temp = free
            elif free + bd <= act:
                temp = free + bd
                duty_sum += 1.0
            else:
                duty_sum += (act - free) / bd
                temp = act
            if lowered and temp < lim:
                return None
            diff = refs[j] - temp
            if diff > 0:
                deficit += wsub[j] * (diff * diff + (lin * (diff - band) if diff > band else 0.0))
            else:
                above -= diff
        return _finish(zone, h, w, unit, peak_w, duty_sum, deficit, above, temp)
    for j in range(SUBSTEPS):
        if act is None:
            d = 0.0
        elif percent:
            d = min(1.0, max(0.0, act / 100.0))
        else:
            # thermostat: the duty that brings the room to the target this substep
            if affine:
                free = a * temp + b0
                gain = bd
            else:
                free = model.step(temp, 0.0, h.out, _DT, h.sol)
                gain = model.step(temp, 1.0, h.out, _DT, h.sol) - free
            d = 0.0 if gain <= 0 else min(1.0, max(0.0, (act - free) / gain))
        if affine:
            temp = a * temp + b0 + d * bd
        else:
            temp = model.step(temp, d, h.out, _DT, h.sol)
        if lowered and temp < floor - 1e-6:
            return None
        duty_sum += d
        diff = refs[j] - temp
        if diff > 0:
            deficit += wsub[j] * (diff * diff + (lin * (diff - band) if diff > band else 0.0))
        else:
            above -= diff
    return _finish(zone, h, w, unit, peak_w, duty_sum, deficit, above, temp)


def _finish(
    zone: ZoneConfig, h: _Hour, w: OptimizerWeights, unit: float, peak_w: float,
    duty_sum: float, deficit: float, above: float, temp: float,
) -> tuple[float, float]:
    kw = duty_sum / SUBSTEPS * zone.heater_kw
    cost = kw * h.price
    cost += deficit * _DT + w.above * unit * above * _DT
    if h.cap is not None and kw > h.cap:
        cost += peak_w * (kw - h.cap)
    return cost, temp


def _label(
    zone: ZoneConfig, s: PlanHour, h: _Hour, act: float | None, ahead: list[PlanHour],
    warm: float | None = None,
    peak_only: bool = False,
) -> PlanHour:
    """Copy of ``s`` with the chosen target and consistent mode/reasons."""
    base_reasons = [r for r in s.reasons if r in ("schedule", "override", R_AWAY)]
    reasons = list(base_reasons)
    costly = s.level in (LEVEL_EXPENSIVE, LEVEL_EXTREME)
    if peak_only and (act is None or abs(act - h.ref) > 1e-9) and not (
        warm is not None and act is not None and h.ref < act <= warm + 1e-9
    ):
        mode = "off" if act is None else "save" if act < h.ref else "preheat"
        return replace(s, target=act, mode=mode, reasons=[*reasons, R_PEAK], predicted=None,
                       duty=0.0, kwh=0.0, cost=None)
    if act is None:
        reasons += [R_EXTREME_OFF if s.level == LEVEL_EXTREME else R_EXPENSIVE, R_OPTIMIZED]
        mode = "off"
    elif act < h.ref - 1e-9:
        # only call it an expensive hour when it is one; otherwise it is the optimiser's choice
        reasons += [R_EXPENSIVE, R_OPTIMIZED] if costly else [R_OPTIMIZED]
        mode = "save"
    elif act > h.ref + 1e-9 and warm is not None and act <= warm + 1e-9:
        reasons += [R_WARMUP, R_OPTIMIZED]  # reaching the next schedule step on time
        mode = "preheat"
    elif act > h.ref + 1e-9:
        extreme_ahead = any(a.level == LEVEL_EXTREME for a in ahead)
        reasons += [R_PREHEAT_EXTREME if extreme_ahead else R_PREHEAT, R_OPTIMIZED]
        mode = "preheat"
    else:
        base = 0.0 if zone.is_percent else zone.comfort
        if R_AWAY in reasons:
            mode = "away"
        elif not zone.is_percent and s.scheduled > base + 0.01:
            mode = "comfort"
        elif not zone.is_percent and s.scheduled < base - 0.01:
            mode = "setback"
        else:
            mode = "normal"
    if h.protected and costly and s.price is not None:
        reasons.append(R_PROTECTED)
    return replace(
        s,
        target=act,
        mode=mode,
        reasons=reasons,
        predicted=None,
        duty=0.0,
        kwh=0.0,
        cost=None,
    )


__all__ = ["OptimizerWeights", "R_OPTIMIZED", "optimize_zone"]
