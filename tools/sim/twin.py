"""The "true" world of the digital twin: rooms, thermostats, weather, prices, people.

Everything here is deterministic for a given seed. The controller never sees
these objects directly – only noisy sensors, a forecast, published prices and a
whole-house meter, like in a real home.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import math
import random
from zoneinfo import ZoneInfo

from custom_components.heat_planner.planner import PricePoint
from custom_components.heat_planner.solar import solar_factor

TZ = ZoneInfo("Europe/Oslo")
LAT, LON = 59.91, 10.75
UTC = timezone.utc


def local(when: datetime) -> datetime:
    return when.astimezone(TZ)


def local_day_hours(day: date) -> list[datetime]:
    """Every real hour of a local day (23, 24 or 25 of them), as local datetimes."""
    start = datetime(day.year, day.month, day.day, tzinfo=TZ).astimezone(UTC)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ).astimezone(UTC)
    out, t = [], start
    while t < end:
        out.append(t.astimezone(TZ))
        t += timedelta(hours=1)
    return out


# --------------------------------------------------------------------------- rooms


@dataclass
class TrueRoom:
    """First-order room with dead time and sun – the physics the planner only estimates."""

    gross: float  # °C/h at full power
    tau: float  # h
    lag_h: float  # dead time of the floor
    solar_gain: float  # °C/h at solar factor 1
    temp: float
    internal_gain: float = 0.05  # °C/h when people are home
    fifo: deque = field(default_factory=deque)

    def __post_init__(self) -> None:
        n = int(round(self.lag_h * 60))
        self.fifo = deque([0.3] * n)

    def sensed(self, t_out: float) -> float:
        """What a floor-sensor thermostat effectively controls on: roughly where the
        room is heading once the heat already in the floor has arrived (it reacts
        to the floor warming, not the air) – heat in transit minus the losses meanwhile."""
        return self.temp + self.gross * sum(self.fifo) / 60 - (self.temp - t_out) / self.tau * self.lag_h

    def step(self, duty: float, t_out: float, sol: float, home: bool, minutes: float = 1.0) -> None:
        self.fifo.append(duty)
        felt = self.fifo.popleft()
        dt = minutes / 60
        loss = (self.temp - t_out) / self.tau
        self.temp += (self.gross * felt - loss + self.solar_gain * sol + (self.internal_gain if home else 0.0)) * dt


@dataclass
class Device:
    """A floor thermostat (setpoint) or a regulator-mode heater (percent)."""

    mode: str
    kw: float
    setpoint: float = 21.0
    off: bool = False
    value: float = 50.0  # percent zones
    step: float = 0.5
    heating: bool = False
    available: bool = True
    writes: list[datetime] = field(default_factory=list)

    def duty(self, room: float) -> float:
        if self.mode == "percent":
            return 0.0 if self.off else max(0.0, min(1.0, self.value / 100))
        if self.off:
            self.heating = False
            return 0.0
        # full power until the setpoint, restart 0.3 °C below it
        if self.heating and room >= self.setpoint:
            self.heating = False
        elif not self.heating and room < self.setpoint - 0.3:
            self.heating = True
        return 1.0 if self.heating else 0.0


# --------------------------------------------------------------------------- weather


@dataclass
class Weather:
    mean: float
    amp: float = 3.0
    seed: int = 0
    drift: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        rnd = random.Random(self.seed * 7 + 1)
        v, self.drift = 0.0, []
        for _ in range(24 * 40):
            v = 0.97 * v + rnd.gauss(0, 0.5)
            self.drift.append(v)

    def _idx(self, when: datetime) -> int:
        return int((when.astimezone(UTC) - datetime(2026, 9, 1, tzinfo=UTC)).total_seconds() // 3600) % len(self.drift)

    def outdoor(self, when: datetime) -> float:
        h = local(when).hour + local(when).minute / 60
        daily = -self.amp * math.cos(2 * math.pi * (h - 3) / 24)  # coldest ~03, warmest ~15
        return max(-15.0, min(10.0, self.mean + daily + self.drift[self._idx(when)]))

    def cloud(self, when: datetime) -> float:
        rnd = random.Random(self.seed * 1000 + self._idx(when) // 6)
        return rnd.choice((10.0, 40.0, 80.0, 100.0))

    def solar(self, when: datetime) -> float:
        return solar_factor(when, LAT, LON, self.cloud(when))

    def forecast(self, issued: datetime, hours: int = 48, error: float = 1.0) -> dict[datetime, float]:
        """Hourly forecast with an error that grows with the lead time."""
        rnd = random.Random(self.seed * 31 + self._idx(issued))
        base = issued.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        out: dict[datetime, float] = {}
        bias = 0.0
        for k in range(hours):
            t = base + timedelta(hours=k)
            bias += rnd.gauss(0, error * 0.25)
            out[t.astimezone(TZ)] = round(self.outdoor(t + timedelta(minutes=30)) + bias, 1)
        return out

    def forecast_solar(self, issued: datetime, hours: int = 48) -> dict[datetime, float]:
        base = issued.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        out = {}
        for k in range(hours):
            t = base + timedelta(hours=k)
            out[t.astimezone(TZ)] = round(solar_factor(t + timedelta(minutes=30), LAT, LON, self.cloud(t)), 3)
        return out


# --------------------------------------------------------------------------- prices

DAY_TYPES = ("normal", "flat", "spike", "cheap_night")


def spot_day(day: date, kind: str, seed: int, quarter: bool = False) -> list[PricePoint]:
    """NO1-like spot prices (NOK/kWh ex VAT) for every real hour of a local day."""
    rnd = random.Random(seed * 9973 + day.toordinal())
    pts: list[PricePoint] = []
    for t in local_day_hours(day):
        h = t.hour
        if kind == "flat":
            p = 0.42 + rnd.uniform(-0.02, 0.02)
        else:
            p = 0.55 + 0.1 * rnd.random()
            if h in (7, 8, 9):
                p += 0.55
            elif h in (17, 18, 19):
                p += 0.75
            elif h < 5:
                p -= 0.3 if kind == "cheap_night" else 0.15
            if kind == "spike" and h in (8, 18):
                p = 5.0
        p = max(0.01, round(p, 4))
        if quarter:
            for q in range(4):
                s = t + timedelta(minutes=15 * q)
                pts.append(PricePoint(s, s + timedelta(minutes=15), round(max(0.01, p + rnd.uniform(-0.08, 0.08)), 4)))
        else:
            pts.append(PricePoint(t, t + timedelta(hours=1), p))
    return pts


# --------------------------------------------------------------------------- people and the rest of the house


def presence(when: datetime, pattern: str, holiday: tuple[date, date] | None, seed: int) -> bool:
    """Is anyone home?"""
    t = local(when)
    d = t.date()
    if holiday and holiday[0] <= d <= holiday[1]:
        return False
    if pattern == "home" or t.weekday() >= 5:
        return True
    rnd = random.Random(seed * 17 + d.toordinal())
    leave = 7 * 60 + 45 + rnd.randint(-20, 20)
    back = 16 * 60 + 30 + rnd.randint(-40, 40)
    m = t.hour * 60 + t.minute
    return not leave <= m < back


def base_load_kw(when: datetime, home: bool, seed: int, cooking: bool = True) -> float:
    """Everything but the floor heating (kW)."""
    t = local(when)
    kw = 0.35
    if home:
        if 6 <= t.hour < 9:
            kw += 1.1
        elif 16 <= t.hour < 21:
            kw += 1.4
        elif 9 <= t.hour < 16:
            kw += 0.4
        rnd = random.Random(seed * 101 + int(when.timestamp() // 1800))
        if cooking and 17 <= t.hour < 19 and rnd.random() < 0.6:
            kw += 2.2  # cooking
    return kw
