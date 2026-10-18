"""Competing heating strategies, re-implemented from their public docs and source.

Each one controls the same devices through :class:`tools.bench.sim.Context`
and starts from the same household schedule (``ctx.target``). Offsets in °C
are applied to setpoint zones; on the bathroom's percent regulator 1 °C counts
as 10 percentage points (what a user of these tools would configure).

Every class lists its source URL and the rule it implements. ``DEFAULTS`` are
the documented defaults; ``TUNED`` are chosen by ``python -m tools.bench --tune``
(grid search on separate tuning seeds, see ``tuning.json``).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from custom_components.heat_planner.planner import hour_key

from . import lp
from . import world as W
from .sim import Context, Strategy

PCT_PER_C = 10.0


def _offset(ctx: Context, zid: str, off: float) -> float:
    base = ctx.target(zid, ctx.now)
    return base + off * (PCT_PER_C if W.ZONE[zid].is_percent else 1.0)


def _day_prices(prices: dict[datetime, float], day) -> list[tuple[datetime, float]]:
    return sorted((h, p) for h, p in prices.items() if h.date() == day)


# --------------------------------------------------------------------------- reference


class Fixed(Strategy):
    """Plain weekly-schedule thermostat (reference): no prices, no model."""

    name = "fixed_schedule"
    price_aware = False

    def decide(self, ctx: Context) -> dict[str, float | None]:
        return {z.zid: ctx.target(z.zid, ctx.now) for z in W.ZONES}


# --------------------------------------------------------------------------- PriceAnalyzer


class PriceAnalyzer(Strategy):
    """PriceAnalyzer (erlendsellie/priceanalyzer) climate correction.

    Source: https://github.com/erlendsellie/priceanalyzer – custom_components/priceanalyzer/data.py
    (``_get_temperature_correction``, ``_is_gaining``, ``_is_falling``) and the blueprint
    blueprints/automation/priceanalyzer/priceanalyzer.yaml (setpoint = base + correction).
    Rule (per hour, prices incl. additional costs): 0 if today's max/min < 1 + percent_difference
    (20 %); −1 at the day's max, before a ≥40 % drop next hour and at 23:00 before a ≥20 % drop;
    +1 when "gaining" (one of the next ``num_hours_to_boost`` (2) hours is more than
    (1 − min/max)/4 dearer) unless in the 5 most expensive hours; −1 when "falling" (next
    ``num_hours_to_save`` (2) hours cheaper by that margin) and not below the day average;
    otherwise 0. The correction is multiplied by ``multiply_template`` (default 1 → ±1 °C).
    Quirks kept: needs the next three hours to be published (else 0 at the end of the day)
    and ``get_price_for_hour`` wraps hour 25+ back to *today* when tomorrow is unknown.
    """

    name = "priceanalyzer"
    DEFAULTS = {"multiply": 1.0, "boost": 2, "save": 2, "percent_difference": 20.0, "low_cutoff": 1.0}

    def __init__(self, **params: Any) -> None:
        self.params = {**self.DEFAULTS, **params}
        self._hour: datetime | None = None
        self._corr = 0.0

    def correction(self, ctx: Context) -> float:
        p = self.params
        prices = ctx.prices()
        today = ctx.now.date()
        tday = [v for _, v in _day_prices(prices, today)]
        tmw = [v for _, v in _day_prices(prices, today + timedelta(days=1))]
        tomorrow_valid = len(tmw) >= 23
        if len(tday) < 23:
            return 0.0
        hour = ctx.now.hour
        mx, mn = max(tday), min(tday)
        avg = sum(tday) / len(tday)
        threshold = ((mn / mx) - 1) / 4 * -1
        diff = mx / max(mn, 0.00001)

        def get(hh: int) -> float | None:
            if hh > 24:  # sic: data.py maps 25+ back to today when not is_tomorrow
                hh -= 24
            if hh < len(tday):
                return tday[hh]
            if tomorrow_valid and hh - 24 < len(tmw):
                return tmw[hh - 24]
            return None

        now_p = tday[hour]
        n1, n2, n3 = get(hour + 1), get(hour + 2), get(hour + 3)
        if n1 is None or n2 is None or n3 is None:
            gaining = falling = None
            price_next = None
        else:
            price_next = n1
            gaining = falling = False
            for i in reversed(range(int(p["boost"]) or 2)):
                nh = get(hour + i + 1)
                if nh is not None and now_p / max(nh, 0.00001) < 1 - threshold:
                    gaining = True
            for i in reversed(range(int(p["save"]) or 2)):
                nh = get(hour + i + 1)
                if nh is not None and now_p / max(nh, 0.00001) > 1 + threshold:
                    falling = True
        if diff < (p["percent_difference"] + 100) / 100:
            return 0.0
        is_max = now_p == mx
        five = sorted(tday, reverse=True)[:5]
        five_most_expensive = now_p in five
        is_low = now_p < avg * p["low_cutoff"]
        pn = max(now_p, 0.00001)
        pnext = max(price_next if price_next is not None else pn, 0.00001)
        pretty_cheap = pn < 0.05
        if hour == 23 and price_next is not None and pnext / pn < 0.80 and not pretty_cheap:
            return -1.0
        if is_max:
            return -1.0
        if price_next is not None and price_next / pn < 0.60 and not pretty_cheap:
            return -1.0
        if gaining and pn < pnext and not five_most_expensive:
            return 1.0
        if falling and not is_low:
            return -1.0
        return 0.0

    def decide(self, ctx: Context) -> dict[str, float | None]:
        h = hour_key(ctx.now)
        if h != self._hour:
            self._hour = h
            self._corr = self.correction(ctx) * self.params["multiply"]
        return {z.zid: _offset(ctx, z.zid, self._corr) for z in W.ZONES}


# --------------------------------------------------------------------------- nordpool_diff


class NordpoolDiff(Strategy):
    """nordpool_diff (jpulakka/nordpool_diff): FIR "price derivative" → thermostat offset.

    Source: https://github.com/jpulakka/nordpool_diff – custom_components/nordpool_diff/sensor.py
    and README. Rule: sensor = Σ f_k·p_k over the next ``filter_length`` prices with the triangle
    filter f = [−1, (L−1)/T, …, 1/T] (T = L(L−1)/2), normalised ``max_min_sqrt_max`` (× √max ÷
    (max − min)); missing future prices are padded with the last known one. The README aims at
    a thermostat varying about ±2 °C under normal price variation: offset = clamp(gain × sensor,
    ±limit). Hourly prices: filter_length 10 h (= the 40-quarter default).
    """

    name = "nordpool_diff"
    DEFAULTS = {"length": 10, "gain": 3.0, "limit": 2.0}

    def __init__(self, **params: Any) -> None:
        self.params = {**self.DEFAULTS, **params}
        self._hour: datetime | None = None
        self._off = 0.0

    def sensor(self, ctx: Context) -> float:
        n = int(self.params["length"])
        h0 = hour_key(ctx.now)
        prices = ctx.prices()
        seq = []
        for k in range(n + 1):
            v = prices.get(h0 + timedelta(hours=k))
            if v is None:
                break
            seq.append(v)
        if not seq:
            return 0.0
        seq += [seq[-1]] * (n + 1 - len(seq))
        seq = seq[:-1]
        tri = n * (n - 1) / 2
        filt = [-1.0] + [i / tri for i in range(n - 1, 0, -1)]
        raw = sum(a * b for a, b in zip(seq, filt))
        mx, mn = max(seq), min(seq)
        norm = math.sqrt(mx if mx > 0 else 0) / (mx - mn if mx - mn > 0 else 1)
        return raw * norm

    def decide(self, ctx: Context) -> dict[str, float | None]:
        h = hour_key(ctx.now)
        if h != self._hour:
            self._hour = h
            lim = self.params["limit"]
            self._off = round(max(-lim, min(lim, self.params["gain"] * self.sensor(ctx))) * 2) / 2
        return {z.zid: _offset(ctx, z.zid, self._off) for z in W.ZONES}


# --------------------------------------------------------------------------- cheapest hours


class CheapestHours(Strategy):
    """Cheapest / most expensive N hours per day (aio_energy_management / cheapest-energy-hours).

    Sources: https://github.com/kotope/aio_energy_management (cheapest_hours binary sensor,
    ``number_of_slots``, ``inversed: true`` for the most expensive; README example: 3 cheapest,
    4 most expensive) and https://github.com/TheFes/cheapest-energy-hours. Typical community
    automation: +1 °C while "cheapest hours" is on, −1 °C while "expensive hours" is on.
    Non-sequential, per calendar day (00–24), recomputed when the day's prices exist.
    """

    name = "cheapest_hours"
    DEFAULTS = {"cheap_n": 3, "exp_n": 4, "delta": 1.0}

    def __init__(self, **params: Any) -> None:
        self.params = {**self.DEFAULTS, **params}
        self._day = None
        self._cheap: set[datetime] = set()
        self._exp: set[datetime] = set()

    def decide(self, ctx: Context) -> dict[str, float | None]:
        day = ctx.now.date()
        if day != self._day:
            self._day = day
            dp = _day_prices(ctx.prices(), day)
            order = sorted(dp, key=lambda x: x[1])
            self._cheap = {h for h, _ in order[: int(self.params["cheap_n"])]}
            self._exp = {h for h, _ in order[::-1][: int(self.params["exp_n"])]} - self._cheap
        h = hour_key(ctx.now)
        d = self.params["delta"]
        off = d if h in self._cheap else -d if h in self._exp else 0.0
        return {z.zid: _offset(ctx, z.zid, off) for z in W.ZONES}


# --------------------------------------------------------------------------- Sparegris


class Sparegris(Strategy):
    """Homey "Piggy Bank" / Sparegris (frodeheg/no.sparegris): price points + hourly power limit.

    Source: https://github.com/frodeheg/no.sparegris – app.js (``calculateNextPP``,
    ``onProcessPower``) and README. Price points from a rolling average of the last
    ``averageTimePast`` = 96 h: dirt cheap < −40 %, low < −15 %, high > +25 %, extreme > +70 %,
    widened so at least ``minCheapTime`` = 4 hours are low and ``minExpensiveTime`` = 4 high per
    day; with a fixed-price contract only "low" (below the day average) is used. Each device gets
    a per-price-point temperature delta (user setting; here +1 low/dirt cheap, −1 high/extreme).
    Power limiter: ``maxPower`` (default 5000 Wh per hour); every minute the energy budget left
    in the hour, spread over the remaining time (+20 % ``crossSlotSmooth`` of it), is compared
    with the live power; above it devices are switched off, lowest priority first, and switched
    back on (most important first) when there is room again for their power.
    """

    name = "sparegris"
    period = 1
    DEFAULTS = {"max_wh": 5000.0, "low": 1.0, "high": -1.0, "extreme": -1.0}

    def __init__(self, **params: Any) -> None:
        self.params = {**self.DEFAULTS, **params}
        self._hour: datetime | None = None
        self._delta = 0.0
        self._avg: float | None = None
        self.shed: list[str] = []
        self.max_kwh = 5.0

    def start(self, ctx: Context) -> None:
        w = self.params["max_wh"]
        if w == "seasonal":  # the user moves the limit to the step the season needs
            w = 10000.0 if ctx.case.season == "jan" else 5000.0
        self.max_kwh = float(w) / 1000

    def price_point(self, ctx: Context) -> str:
        prices = ctx.prices()
        dp = [v for _, v in _day_prices(prices, ctx.now.date())]
        now_p = prices.get(hour_key(ctx.now))
        if not dp or now_p is None:
            return "normal"
        if ctx.case.contract == "norgespris":
            return "low" if now_p < sum(dp) / len(dp) else "normal"
        hist = 96
        self._avg = now_p if self._avg is None else (self._avg * (hist - 1) + now_p) / hist
        avg = self._avg
        dirt, low, high, extreme = avg * 0.6, avg * 0.85, avg * 1.25, avg * 1.7
        order = sorted(dp)
        lo_i, hi_i = 4, len(order) - 1 - 4
        if low < order[lo_i]:
            low = order[lo_i]
            high, extreme = max(high, low), max(extreme, low)
        if high > order[hi_i]:
            high = order[hi_i]
            low, dirt = min(low, high), min(dirt, high)
        if now_p < dirt:
            return "dirt"
        if now_p < low:
            return "low"
        if now_p > extreme:
            return "extreme"
        if now_p > high:
            return "high"
        return "normal"

    def tick(self, ctx: Context) -> None:
        h = hour_key(ctx.now)
        if h != self._hour:
            if self._hour is None:
                # warm start of the 96 h average with today's prices
                dp = [v for _, v in _day_prices(ctx.prices(), ctx.now.date())]
                self._avg = sum(dp) / len(dp) if dp else None
            self._hour = h
            pp = self.price_point(ctx)
            p = self.params
            self._delta = {"dirt": p["low"], "low": p["low"], "high": p["high"], "extreme": p["extreme"]}.get(pp, 0.0)
        # power limiter (minute resolution)
        left_h = 1 - (ctx.now.minute * 60 + ctx.now.second) / 3600
        budget = self.max_kwh - ctx.hour_kwh
        allowed = budget / max(left_h, 1 / 60) + 0.2 * budget * left_h
        diff = allowed - ctx.house_kw
        order = sorted(W.ZONES, key=lambda z: (z.priority, -z.kw, z.zid))
        if diff < 0:
            need = -diff
            for z in order:
                if need <= 0:
                    break
                if z.zid in self.shed or ctx.draw_kw[z.zid] <= 0.01:
                    continue
                self.shed.append(z.zid)
                need -= ctx.draw_kw[z.zid]
        else:
            for zid in sorted(self.shed, key=lambda i: (W.ZONE[i].priority, -W.ZONE[i].kw), reverse=True):
                if W.ZONE[zid].kw < diff:
                    self.shed.remove(zid)
                    diff -= W.ZONE[zid].kw
        for z in W.ZONES:
            ctx.write(z.zid, None if z.zid in self.shed else _offset(ctx, z.zid, self._delta))


# --------------------------------------------------------------------------- EMHASS


class Emhass(Strategy):
    """EMHASS thermal model (davidusb-geek/emhass) as an hourly MPC linear programme.

    Sources: https://github.com/davidusb-geek/emhass – src/emhass/optimization.py
    (``_add_thermal_load_constraints``) and docs/thermal_model.md. Per zone:
    T[t+1] = T[t] + heating_rate·P[t−L]/P_nom − cooling_constant·(T[t] − T_out[t]),
    the first L = thermal_inertia steps without heating effect; ``min_temperatures`` /
    ``max_temperatures`` as the comfort range (docs' recommended mode; here the schedule and
    schedule + 3 °C), minimise Σ load_cost·P. ``naive-mpc-optim`` every hour with
    ``prediction_horizon`` 24 h; the published ``predicted_temp`` is used as the thermostat
    setpoint (docs). Parameters as a user would configure them: heating_rate = gross_rate,
    cooling_constant = 1/tau, thermal_inertia = the floor's dead time (believed model).
    ``mode="power"`` (a tuning option) drives the zone with the optimised power instead, as a
    deferrable load on a switch: full power for P/P_nom of the hour, then off.
    The minimum is enforced with a stiff penalty (10 kr per °C·h) instead of a hard bound,
    so a room that cannot reach it in time gives the closest plan rather than "Infeasible". Hours beyond the published prices
    repeat the last known day's price for that hour. The bathroom has no sensor (no thermal
    model possible) and follows its schedule.
    """

    name = "emhass"
    DEFAULTS = {"horizon": 24, "band": 3.0, "mode": "temp", "lag_aware": False, "bias": 0.0, "penalty": 10.0}

    def __init__(self, **params: Any) -> None:
        self.params = {**self.DEFAULTS, **params}
        self._hour: datetime | None = None
        self._set: dict[str, float] = {}

    def plan_zone(self, ctx: Context, zid: str, prices: dict[datetime, float]) -> float:
        z = W.ZONE[zid]
        g, tau, lag, _sol = z.believed
        n = int(self.params["horizon"])
        h0 = hour_key(ctx.now)
        temp0 = ctx.sensor(zid)
        outdoor, _ = ctx.forecast()
        hours = [h0 + timedelta(hours=k) for k in range(n + 1)]
        known = sorted(prices)
        price = []
        for h in hours[:n]:
            v = prices.get(h)
            if v is None:
                back = h - timedelta(days=1)
                while back not in prices and back > known[0]:
                    back -= timedelta(days=1)
                v = prices.get(back, prices[known[-1]])
            price.append(v)
        tout = [outdoor.get(h, ctx.outdoor_now()) for h in hours[:n]]
        lo = np.array([ctx.target(zid, h) for h in hours[1:]])
        hi = lo + self.params["band"]
        L = int(lag)  # whole time steps
        cool = 1.0 / tau
        # T[1..n] = c0 + M P,  P[0..n-1] in kW
        c0 = np.zeros(n)
        M = np.zeros((n, n))
        temp = temp0
        free = []
        for t in range(n):
            temp = temp - cool * (temp - tout[t])
            free.append(temp)
        c0[:] = free
        a = 1 - cool
        gain = g / z.kw
        # row t is T[t+1]; P[k] first acts on T[k+L+1] and then decays by a per step
        lagged = np.arange(n)[:, None] - np.arange(n)[None, :] - L
        M[:] = np.where(lagged >= 0, gain * a ** np.maximum(lagged, 0), 0.0)
        pmax = z.kw
        # variables [P (kW), s (°C short of the minimum)]: the minimum is kept with a
        # penalty of ``penalty`` per °C·h (EMHASS: hard min_temperatures – "Infeasible" when the
        # room cannot get there in time; its desired_temperatures mode uses penalty_factor 10)
        hi = np.maximum(hi, c0 + 0.01)
        eye, zero = np.eye(n), np.zeros((n, n))
        A = np.vstack([np.hstack([-M, -eye]), np.hstack([M, zero])])
        b = np.concatenate([c0 - lo, hi - c0])
        lb = np.zeros(2 * n)
        ub = np.concatenate([np.full(n, pmax), np.full(n, 60.0)])
        cost = np.concatenate([np.array(price), np.full(n, float(self.params["penalty"]))])
        p = lp.solve(cost, A, b, lb, ub)[:n]
        traj = c0 + M @ p
        if self.params["mode"] == "power":
            return float(p[0] / pmax)  # share of this hour at full power
        # the published predicted_temp for the next step (lag_aware: the step the heat lands in)
        k = min(n - 1, L) if self.params["lag_aware"] else 0
        return float(traj[k]) + self.params["bias"]

    def decide(self, ctx: Context) -> dict[str, float | None]:
        h = hour_key(ctx.now)
        if h != self._hour:
            self._hour = h
            prices = ctx.prices()
            for z in W.ZONES:
                if not z.is_percent:
                    self._set[z.zid] = self.plan_zone(ctx, z.zid, prices)
        out: dict[str, float | None] = {}
        frac = ctx.now.minute / 60
        for z in W.ZONES:
            if z.is_percent:
                out[z.zid] = ctx.target(z.zid, ctx.now)
            elif self.params["mode"] == "power":
                # the deferrable-load power as a switch: full power for that share of the hour
                out[z.zid] = 28.0 if frac < self._set[z.zid] - 1e-6 else None
            else:
                out[z.zid] = self._set[z.zid]
        return out


# --------------------------------------------------------------------------- RoomMind


class RoomMind(Strategy):
    """RoomMind-style comfort MPC (snazzybean/roommind): model-based, no price awareness.

    Source: https://github.com/snazzybean/roommind README ("Self-Learning MPC … plans ahead",
    schedule comfort/eco targets, full control may send a high setpoint to force full power).
    Rule here: hold the schedule; with the (believed) room model including the floor's dead
    time, start heating toward the next higher schedule target just early enough to reach it
    on time (optimal start). ``coast`` (tuned variant): also stop heating ahead of a setback
    when the room's own cooling reaches the lower target on time (optimal stop). The bathroom
    has no sensor and follows its schedule.
    """

    name = "roommind"
    price_aware = False
    DEFAULTS = {"coast": False, "margin_h": 0.25}

    def __init__(self, **params: Any) -> None:
        self.params = {**self.DEFAULTS, **params}

    @staticmethod
    def _reach(z: W.Zone, temp: float, hours: float, tout: float, duty: float) -> float:
        g, tau, lag, _ = z.believed
        dt = 0.25
        t = 0.0
        while t < hours - 1e-9:
            # during the dead time the room gets what was commanded before: assume holding
            felt = duty if t >= lag else min(1.0, max(0.0, (temp - tout) / tau / g))
            temp += (g * felt - (temp - tout) / tau) * dt
            t += dt
        return temp

    def decide(self, ctx: Context) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        tout = ctx.outdoor_now()
        for z in W.ZONES:
            now_t = ctx.target(z.zid, ctx.now)
            if z.is_percent:
                out[z.zid] = now_t
                continue
            temp = ctx.sensor(z.zid)
            sp = now_t
            lead = z.believed[2] + 6.0
            for k in range(1, int(lead * 4) + 1):
                t = ctx.now + timedelta(minutes=15 * k)
                tk = ctx.target(z.zid, t)
                if tk > now_t + 0.01:
                    hours = 15 * k / 60
                    # can we still make it if we wait one more control period?
                    if self._reach(z, max(temp, now_t - 0.3), hours - 5 / 60 - self.params["margin_h"], tout, 1.0) < tk:
                        sp = tk
                    break
                if tk < now_t - 0.01:
                    if self.params["coast"]:
                        hours = 15 * k / 60
                        if hours <= z.believed[2] + 1.0 and self._reach(z, temp, hours, tout, 0.0) >= tk:
                            sp = tk
                    break
            out[z.zid] = sp
        return out


def registry() -> dict[str, type[Strategy]]:
    return {c.name: c for c in (Fixed, PriceAnalyzer, NordpoolDiff, CheapestHours, Sparegris, Emhass, RoomMind)}
