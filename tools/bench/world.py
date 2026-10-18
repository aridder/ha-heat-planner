"""The benchmark world: house, people, weather, prices, tariffs (all deterministic per seed).

Everything a strategy may know is reachable through :class:`tools.bench.sim.Context`;
this module is the "truth" the simulator integrates and the metrics are taken on.

House (a two-storey terraced house in Oslo, NO1) – four heated zones:

=========  ========  =====  =====================================================
zone       mode      kW     true room (gross °C/h at full power, tau h, dead time)
=========  ========  =====  =====================================================
stue       setpoint  2.0    slow concrete floor: 1.4 °C/h, 40 h, 2.0 h lag, sun
kjokken    setpoint  1.2    fast, panel-like: 2.4 °C/h, 22 h, no lag
sov        setpoint  0.8    thin floor: 1.5 °C/h, 35 h, 0.5 h lag
bad        percent   0.8    bathroom floor on a regulator (percent), no sensor
=========  ========  =====  =====================================================

Model-based strategies (ours, EMHASS-style, RoomMind-style) all get the same
*believed* room models: the true parameters with 5–15 % error (what a few weeks
of learning, or a careful user, ends up with). Setpoint zones are floor/panel
thermostats with 0.3 °C hysteresis (``tools.sim.twin.Device``); a floor
thermostat controls on the floor sensor, i.e. it anticipates heat in transit
(``TrueRoom.sensed``) – exactly the twin used by ``tools.sim``.

Prices (NOK/kWh ex VAT) are *synthetic* NO1 days, see :func:`spot_day`.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
import math
import random

from custom_components.heat_planner.energy import tariff_from_options
from custom_components.heat_planner.peak import PeakConfig, top3_average
from custom_components.heat_planner.planner import PricePoint, ThermalModel
from custom_components.heat_planner.tariff import TariffConfig, effective_price, energy_price, grid_price

from tools.sim.twin import TZ, UTC, TrueRoom, Weather, local_day_hours

DAYS = 14


def at(y: int, m: int, d: int, h: int = 0) -> datetime:
    return datetime(y, m, d, h, tzinfo=TZ)


# --------------------------------------------------------------------------- house


@dataclass(frozen=True)
class Zone:
    zid: str
    mode: str  # setpoint | percent
    kw: float
    true: tuple[float, float, float, float]  # gross, tau, lag_h, solar_gain
    believed: tuple[float, float, float, float]
    sensor: bool  # a separate room sensor (percent zones: none at all)
    priority: int = 0
    comfort: float = 21.0  # our integration's "comfort" (schedule offsets are relative to it)

    @property
    def is_percent(self) -> bool:
        return self.mode == "percent"

    def model(self) -> ThermalModel:
        g, tau, lag, sol = self.believed
        return ThermalModel(g, tau, learned=True, samples=200, solar_gain=sol, lag_hours=lag, rmse=0.2)


ZONES: tuple[Zone, ...] = (
    Zone("stue", "setpoint", 2.0, (1.4, 40.0, 2.0, 0.5), (1.25, 44.0, 2.0, 0.4), True, 0, 21.0),
    Zone("kjokken", "setpoint", 1.2, (2.4, 22.0, 0.0, 0.2), (2.2, 24.0, 0.0, 0.15), False, 1, 21.0),
    Zone("sov", "setpoint", 0.8, (1.5, 35.0, 0.5, 0.1), (1.35, 38.0, 0.5, 0.1), True, 0, 19.0),
    Zone("bad", "percent", 0.8, (2.0, 25.0, 1.0, 0.0), (1.8, 28.0, 1.0, 0.0), False, 0, 0.0),
)
ZONE = {z.zid: z for z in ZONES}


class FastRoom(TrueRoom):
    """``TrueRoom`` with a running sum of the dead-time queue (same physics, ~20× faster)."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.queued = sum(self.fifo)

    def sensed(self, t_out: float) -> float:
        return self.temp + self.gross * self.queued / 60 - (self.temp - t_out) / self.tau * self.lag_h

    def step(self, duty: float, t_out: float, sol: float, home: bool, minutes: float = 1.0) -> None:
        if self.fifo:
            self.fifo.append(duty)
            felt = self.fifo.popleft()
            self.queued += duty - felt
        else:
            felt = duty
        dt = minutes / 60
        loss = (self.temp - t_out) / self.tau
        self.temp += (self.gross * felt - loss + self.solar_gain * sol + (self.internal_gain if home else 0.0)) * dt


# --------------------------------------------------------------------------- seasons and weather


@dataclass(frozen=True)
class Season:
    key: str
    label: str
    start: datetime  # the 1st of the month 00:00 (the capacity tariff's month starts with the run)
    knots: tuple[tuple[float, float], ...]  # (day, mean outdoor °C), linear in between
    amp: float  # daily half-swing °C
    price_level: tuple[tuple[float, float], ...]  # (day, NOK/kWh ex VAT daily mean level)
    spike_p: float  # probability per day of a price spike in a peak hour
    solar_dip: bool  # spring: cheap midday hours
    # the user's regulator setting for the bathroom this season (day; night −15): the lowest
    # whole percent at which the schedule alone keeps it at its 22 °C (on a tuning seed)
    bath_pct: float

    def mean(self, day: float) -> float:
        return _interp(self.knots, day)

    def level(self, day: float) -> float:
        return _interp(self.price_level, day)


def _interp(knots: tuple[tuple[float, float], ...], x: float) -> float:
    xs = [k[0] for k in knots]
    i = bisect_right(xs, x)
    if i == 0:
        return knots[0][1]
    if i >= len(knots):
        return knots[-1][1]
    (x0, y0), (x1, y1) = knots[i - 1], knots[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


SEASONS: dict[str, Season] = {
    "oct": Season(
        "oct", "Oslo October (autumn)", at(2026, 10, 1), ((0, 8.0), (14, 4.0)), 3.0,
        ((0, 0.62), (7, 0.70), (14, 0.66)), 0.08, False, 38.0,
    ),
    "jan": Season(
        "jan", "January cold snap (to −15…−20 °C)", at(2026, 1, 1),
        ((0, -4.0), (2.5, -5.0), (4, -14.0), (9, -15.0), (11, -7.0), (14, -5.0)), 3.0,
        ((0, 0.95), (3, 1.05), (4.5, 1.55), (9, 1.6), (11, 1.1), (14, 1.0)), 0.25, False, 73.0,
    ),
    "apr": Season(
        "apr", "Oslo April (spring)", at(2026, 4, 1), ((0, 4.0), (14, 8.0)), 4.5,
        ((0, 0.48), (7, 0.42), (14, 0.50)), 0.05, True, 38.0,
    ),
}


class BenchWeather(Weather):
    """``twin.Weather`` (AR(1) drift, clouds, lead-time-growing forecast error) on a seasonal mean."""

    def __init__(self, season: Season, seed: int) -> None:
        self.season = season
        super().__init__(mean=0.0, amp=season.amp, seed=seed + 1000 * (1 + list(SEASONS).index(season.key)))
        self._t0 = season.start.astimezone(UTC)
        self._cache: dict[int, float] = {}

    def outdoor(self, when: datetime) -> float:
        key = int(when.timestamp() // 60)
        v = self._cache.get(key)
        if v is None:
            lt = when.astimezone(TZ)
            h = lt.hour + lt.minute / 60
            day = (when.astimezone(UTC) - self._t0).total_seconds() / 86400
            daily = -self.amp * math.cos(2 * math.pi * (h - 3) / 24)
            v = max(-25.0, min(18.0, self.season.mean(day) + daily + self.drift[self._idx(when)]))
            if len(self._cache) > 4000:
                self._cache.clear()
            self._cache[key] = v
        return v


# --------------------------------------------------------------------------- prices

# Synthetic NO1 day-ahead prices. Hourly shape (weekday) from typical NO1 2025–2026
# days: a night trough (~0.75×), a morning peak 07–09 (~1.3×) and a higher evening
# peak 17–19 (~1.4×); weekends flatter. The daily level follows the season (Oct
# ≈ 0.6–0.7, January cold snap ≈ 1.0 → 1.6, April ≈ 0.45 NOK/kWh ex VAT, roughly
# the 2025–26 monthly NO1 averages) with ±15 % day-to-day noise; the shape's
# amplitude varies per day (0.5–1.7×: windy flat days and peaky days); spikes
# (2.5–4× in one or two peak hours) occur with the season's probability; April
# has a midday solar dip. Real price files could not be fetched in this sandbox.
WEEKDAY_SHAPE = (
    0.78, 0.75, 0.73, 0.73, 0.76, 0.85, 1.00, 1.25, 1.35, 1.20, 1.05, 1.00,
    0.97, 0.95, 0.97, 1.02, 1.12, 1.30, 1.40, 1.30, 1.12, 1.00, 0.92, 0.85,
)


def spot_day(season: Season, day: date, seed: int) -> list[PricePoint]:
    rnd = random.Random(seed * 7919 + day.toordinal() * 31 + len(season.key))
    d = (datetime(day.year, day.month, day.day, tzinfo=TZ) - season.start).total_seconds() / 86400
    level = season.level(d + 0.5) * math.exp(rnd.gauss(0, 0.15))
    weekend = day.weekday() >= 5
    amp = rnd.uniform(0.5, 1.7) * (0.5 if weekend else 1.0)
    spike_hours: dict[int, float] = {}
    if rnd.random() < season.spike_p and not weekend:
        h0 = rnd.choice((8, 17, 18))
        spike_hours[h0] = rnd.uniform(2.5, 4.0)
        if rnd.random() < 0.5:
            spike_hours[h0 + 1] = 1 + (spike_hours[h0] - 1) * 0.6
    out = []
    for t in local_day_hours(day):
        shape = 1 + (WEEKDAY_SHAPE[t.hour] - 1) * amp
        if season.solar_dip and 10 <= t.hour <= 15:
            shape *= rnd.uniform(0.45, 0.8)
        p = level * (0.9 if weekend else 1.0) * shape * (1 + rnd.gauss(0, 0.04))
        p *= spike_hours.get(t.hour, 1.0)
        out.append(PricePoint(t, t + timedelta(hours=1), round(max(0.01, p), 4)))
    return out


# --------------------------------------------------------------------------- contracts

CONTRACTS = ("spot", "spot_support", "norgespris")
CONTRACT_LABEL = {"spot": "spot (no support)", "spot_support": "spot + strømstøtte", "norgespris": "Norgespris"}


@lru_cache(maxsize=8)
def tariff(contract: str) -> TariffConfig:
    mode = "fixed" if contract == "norgespris" else contract
    return tariff_from_options({"energy": {"contract": mode, "grid": "elvia_2026", "fixed_price": 0.5}})


PEAK = PeakConfig()


def capacity_cost(top3: float, days: float, month_days: int) -> float:
    """Elvia 2026 capacity step for the period's top-3 average, pro-rated to the simulated days."""
    return PEAK.cost_for(top3) * days / month_days


def bill(hourly: dict[datetime, float], spot: dict[datetime, float], contract: str, days: float) -> dict[str, float]:
    """What the household pays for ``hourly`` house kWh (UTC hour keys) under ``contract``."""
    cfg = tariff(contract)
    energy = grid = 0.0
    daily_max: dict[date, float] = {}
    for h, kwh in hourly.items():
        lt = h.astimezone(TZ)
        energy += kwh * (energy_price(spot[h], cfg) or 0.0)
        grid += kwh * grid_price(lt, cfg)
        daily_max[lt.date()] = max(daily_max.get(lt.date(), 0.0), kwh)
    top3 = top3_average(list(daily_max.values()))
    first = min(hourly).astimezone(TZ)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_days = (nxt - first.replace(day=1)).days
    cap = capacity_cost(top3, days, month_days)
    return {"energy": energy, "grid": grid, "capacity": cap, "total": energy + grid + cap, "top3_kw": top3}


def effective(hour: datetime, spot: float, contract: str) -> float:
    return effective_price(hour, spot, tariff(contract)) or 0.0


# --------------------------------------------------------------------------- people and schedules


def holidays(season: Season) -> tuple[date, date]:
    """Two away weekdays in the second week (Tue–Wed): nobody home, calendar setback for everyone."""
    d = season.start.date() + timedelta(days=7)
    while d.weekday() != 1:
        d += timedelta(days=1)
    return d, d + timedelta(days=1)


@dataclass
class People:
    season: Season
    seed: int
    away: tuple[date, date] = field(init=False)

    def __post_init__(self) -> None:
        self.away = holidays(self.season)
        self._day: dict[date, tuple[int, int] | None] = {}

    def home(self, when: datetime) -> bool:
        """Work weekdays (leave ~07:45, back ~16:30, both ± random), home at weekends."""
        t = when.astimezone(TZ)
        d = t.date()
        if self.away[0] <= d <= self.away[1]:
            return False
        if t.weekday() >= 5:
            return True
        if d not in self._day:
            rnd = random.Random(self.seed * 17 + d.toordinal())
            self._day[d] = (7 * 60 + 45 + rnd.randint(-20, 20), 16 * 60 + 30 + rnd.randint(-40, 40))
        leave, back = self._day[d]  # type: ignore[misc]
        m = t.hour * 60 + t.minute
        return not leave <= m < back


def schedule_value(zid: str, t: datetime) -> float:
    """The household's weekly schedule (°C, or percent for the bathroom regulator) – no calendar."""
    h, wd = t.hour, t.weekday() >= 5
    if zid == "stue":
        day = (7 <= h < 23) if wd else (6 <= h < 8 or 16 <= h < 23)
        return 21.0 if day else 19.0
    if zid == "kjokken":
        day = (7 <= h < 22) if wd else (6 <= h < 8 or 16 <= h < 22)
        return 21.0 if day else 18.0
    if zid == "sov":
        return 19.0 if (h >= 21 or h < 7) else 18.0
    raise KeyError(zid)


AWAY_SETBACK = {"stue": 17.0, "kjokken": 17.0, "sov": 17.0}


def bath_pct(season: Season, t: datetime) -> float:
    return season.bath_pct if 5 <= t.hour < 23 else season.bath_pct - 15.0


def target(season: Season, zid: str, t: datetime, away: tuple[date, date] | None) -> float:
    """What every strategy is told to hold: the schedule, with the calendar setback on away days."""
    on_holiday = away is not None and away[0] <= t.date() <= away[1]
    if zid == "bad":
        v = bath_pct(season, t)
        return max(10.0, v - 15.0) if on_holiday else v
    return AWAY_SETBACK[zid] if on_holiday else schedule_value(zid, t)


def comfort_target(zid: str, t: datetime) -> float:
    """The temperature the household wants while home (metrics). Bathroom: 22 °C by day, 20 at night."""
    if zid == "bad":
        return 22.0 if 6 <= t.hour < 23 else 20.0
    return schedule_value(zid, t)


# overheating is counted above the zone's highest comfort target + 1 °C (setbacks are not "too warm")
TOO_WARM = {"stue": 22.0, "kjokken": 22.0, "sov": 20.0, "bad": 23.0}


def new_rooms(season: Season) -> dict[str, FastRoom]:
    rooms = {}
    for z in ZONES:
        g, tau, lag, sol = z.true
        t0 = 22.0 if z.is_percent else schedule_value(z.zid, season.start)
        rooms[z.zid] = FastRoom(gross=g, tau=tau, lag_h=lag, solar_gain=sol, temp=t0)
    return rooms
