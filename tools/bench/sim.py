"""One benchmark run: a strategy controls the twin house for 14 days; metrics on the true rooms.

Every strategy sees the same things through :class:`Context`: noisy room
sensors (±0.1 °C; none in the bathroom), the outdoor temperature and a 48 h
forecast (error growing with lead time), the household's effective price per
hour (contract + grid, published for tomorrow at 13:00), the whole-house meter,
the schedule (with the calendar setback on away days) and the believed room
models. Nobody gets presence: away days come from the calendar for everyone.

Physics are the ``tools.sim`` twin, integrated minute by minute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import time as _time
from typing import Any

from custom_components.heat_planner.planner import hour_key

from tools.sim.twin import TZ, UTC, Device, base_load_kw

from . import world as W

STEP = {"setpoint": 0.5, "percent": 1.0}
COMFORT_PENALTY = 2.0  # kr per degree-hour below the occupied target (value score)
# The value score counts degree-hours below (target − COMFORT_BAND): 0.3 °C is the
# thermostats' own hysteresis (they restart 0.3 °C under the setpoint), so a room held
# exactly as a thermostat holds it is not "too cold". The strict deficit (from the
# target itself) is reported too, with its own value score ("value_strict").
COMFORT_BAND = 0.3
COLD_MARGIN = 1.0  # a "cold hour": more than this below the occupied target
OVERHEAT_MARGIN = 1.0  # overheating: more than this above the zone's highest (daytime) target


@dataclass
class Case:
    season: str
    seed: int
    contract: str
    days: int = W.DAYS


class Context:
    """What a strategy may read (and its only way to write)."""

    def __init__(self, case: Case) -> None:
        self.case = case
        self.season = W.SEASONS[case.season]
        self.weather = W.BenchWeather(self.season, case.seed)
        self.people = W.People(self.season, case.seed)
        self.rooms = W.new_rooms(self.season)
        self.devices: dict[str, Device] = {}
        for z in W.ZONES:
            if z.is_percent:
                dev = Device("percent", z.kw, value=W.bath_pct(self.season, self.season.start), step=1.0)
            else:
                dev = Device("setpoint", z.kw, setpoint=W.schedule_value(z.zid, self.season.start), step=0.5)
            self.devices[z.zid] = dev
        self.now: datetime = self.season.start
        self._spot: dict[date, list] = {}
        self._fc: tuple[datetime, dict, dict] | None = None
        self.house_kw = 0.0
        self.hour_kwh = 0.0  # this clock hour so far (live meter integration)
        self.house_hourly: dict[datetime, float] = {}  # local hour -> kWh (completed hours)
        self.draw_kw: dict[str, float] = {z.zid: 0.0 for z in W.ZONES}

    # -- truth helpers (also used by the ours adapter's world)
    def spot(self, day: date) -> list:
        if day not in self._spot:
            self._spot[day] = W.spot_day(self.season, day, self.case.seed)
        return self._spot[day]

    def sensor(self, zid: str, now: datetime | None = None, rooms: dict | None = None) -> float | None:
        z = W.ZONE[zid]
        if z.is_percent:
            return None
        now = now or self.now
        minute = int(now.timestamp() // 60)
        key = (self.case.seed * 1_000_003 + sum(map(ord, zid)) * 7_919 + minute) * 2_654_435_761 % 2**32
        return round((rooms or self.rooms)[zid].temp + (key / 2**32 - 0.5) * 0.2, 1)

    # -- what strategies read
    def outdoor_now(self) -> float:
        return round(self.weather.outdoor(self.now), 1)

    def forecast(self) -> tuple[dict[datetime, float], dict[datetime, float]]:
        """(outdoor, solar) hourly forecasts for 48 h, refreshed hourly."""
        issued = hour_key(self.now)
        if self._fc is None or self._fc[0] != issued:
            self._fc = (issued, self.weather.forecast(self.now), self.weather.forecast_solar(self.now))
        return self._fc[1], self._fc[2]

    def prices(self) -> dict[datetime, float]:
        """Published effective household prices (NOK/kWh incl. VAT, support/fixed price and grid)."""
        today = self.now.date()
        days = [today]
        if self.now.hour >= 13:
            days.append(today + timedelta(days=1))
        out = {}
        for d in days:
            for p in self.spot(d):
                h = hour_key(p.start)
                out[h] = W.effective(h, p.price, self.case.contract)
        return out

    def target(self, zid: str, t: datetime) -> float:
        return W.target(self.season, zid, t, self.people.away)

    def write(self, zid: str, value: float | None) -> None:
        """Command a device (None = off); only actual changes are sent (and counted)."""
        dev = self.devices[zid]
        if dev.mode == "percent":
            v = 0.0 if value is None else max(0.0, min(100.0, round(value)))
            if abs(dev.value - v) > 0.5:
                dev.value = v
                dev.writes.append(self.now)
            return
        if value is None:
            if not dev.off:
                dev.off = True
                dev.writes.append(self.now)
            return
        v = round(max(5.0, min(28.0, value)) * 2) / 2
        if dev.off or abs(dev.setpoint - v) > 0.01:
            dev.off, dev.setpoint = False, v
            dev.writes.append(self.now)


class Strategy:
    """Base class: ``decide`` every ``period`` minutes returns {zone: value | None (off)}."""

    name = "base"
    period = 5
    price_aware = True
    params: dict[str, Any] = {}

    def start(self, ctx: Context) -> None:  # noqa: B027
        pass

    def decide(self, ctx: Context) -> dict[str, float | None]:
        raise NotImplementedError

    def tick(self, ctx: Context) -> None:
        """Called every minute (before the physics); default: decide every ``period`` minutes."""
        if (ctx.now.minute % self.period) == 0:
            for zid, v in self.decide(ctx).items():
                ctx.write(zid, v)


@dataclass
class Metrics:
    hourly: dict[datetime, float] = field(default_factory=dict)  # UTC hour -> house kWh
    heat_kwh: float = 0.0
    deficit: dict[str, float] = field(default_factory=dict)  # °C·h below target while home
    band: float = 0.0  # °C·h below target − COMFORT_BAND while home
    band_rooms: dict[str, float] = field(default_factory=dict)
    below18: float = 0.0
    overheat: float = 0.0
    max_cold_h: float = 0.0
    writes: int = 0


def run(strategy: Strategy, case: Case) -> dict[str, Any]:
    t_cpu = _time.process_time()
    ctx = Context(case)
    season = ctx.season
    zones = [z.zid for z in W.ZONES]
    m = Metrics(deficit={z: 0.0 for z in zones})
    cold_run = {z: 0 for z in zones}
    strategy.start(ctx)
    t = season.start.astimezone(UTC)
    end = t + timedelta(days=case.days)
    cur_hour: datetime | None = None
    hour_kwh = 0.0
    spot_hour: dict[datetime, float] = {}
    rooms, devs = ctx.rooms, ctx.devices
    while t < end:
        now = t.astimezone(TZ)
        ctx.now = now
        home = ctx.people.home(now)
        h_utc = t.replace(minute=0)
        if cur_hour != h_utc:
            if cur_hour is not None:
                m.hourly[cur_hour] = hour_kwh
                ctx.house_hourly[cur_hour.astimezone(TZ)] = round(hour_kwh, 3)
            cur_hour, hour_kwh = h_utc, 0.0
            lt = h_utc.astimezone(TZ)
            spot_hour[h_utc] = next(p.price for p in ctx.spot(lt.date()) if p.start == lt)
        ctx.hour_kwh = hour_kwh
        strategy.tick(ctx)
        t_out = ctx.weather.outdoor(now)
        sol = ctx.weather.solar(now)
        heat = 0.0
        for zid in zones:
            room, dev = rooms[zid], devs[zid]
            duty = dev.duty(room.sensed(t_out))
            room.step(duty, t_out, sol, home)
            kw = duty * dev.kw
            ctx.draw_kw[zid] = kw
            heat += kw
            if home:
                want = W.comfort_target(zid, now)
                gap = want - room.temp
                if gap > 0:
                    m.deficit[zid] += gap / 60
                    if gap > COMFORT_BAND:
                        m.band += (gap - COMFORT_BAND) / 60
                        m.band_rooms[zid] = m.band_rooms.get(zid, 0.0) + (gap - COMFORT_BAND) / 60
                if gap > COLD_MARGIN:
                    cold_run[zid] += 1
                    m.max_cold_h = max(m.max_cold_h, cold_run[zid] / 60)
                else:
                    cold_run[zid] = 0
                if zid != "bad" and room.temp < 18.0:
                    m.below18 += (18.0 - room.temp) / 60
                hot = room.temp - W.TOO_WARM[zid]
                if hot > 0:
                    m.overheat += hot / 60
            else:
                cold_run[zid] = 0
        m.heat_kwh += heat / 60
        ctx.house_kw = base_load_kw(t, home, case.seed, True) + heat
        hour_kwh += ctx.house_kw / 60
        t += timedelta(minutes=1)
    if cur_hour is not None:
        m.hourly[cur_hour] = hour_kwh
    m.writes = sum(len(d.writes) for d in devs.values())

    # stored heat at the end vs the schedule: reheating (or the surplus) at the period's mean price
    end_now = end.astimezone(TZ)
    store_kwh = 0.0
    for z in W.ZONES:
        want = W.comfort_target(z.zid, end_now)
        g = z.true[0]
        store_kwh += (want - rooms[z.zid].temp) * z.kw / g
    out: dict[str, Any] = {
        "strategy": strategy.name, "season": case.season, "seed": case.seed, "contract": case.contract,
        "params": strategy.params, "days": case.days,
        "heat_kwh": round(m.heat_kwh, 2), "house_kwh": round(sum(m.hourly.values()), 2),
        "store_kwh": round(store_kwh, 2),
        "deficit": round(sum(m.deficit.values()), 2),
        "deficit_band": round(m.band, 2),
        "deficit_rooms": {k: round(v, 2) for k, v in m.deficit.items()},
        "band_rooms": {z: round(m.band_rooms.get(z, 0.0), 2) for z in zones},
        "below18": round(m.below18, 2), "overheat": round(m.overheat, 2),
        "max_cold_h": round(m.max_cold_h, 2),
        "writes_day": round(m.writes / case.days, 1),
        "cpu_s": round(_time.process_time() - t_cpu, 1),
    }
    # bills: the run's own contract, and (for price-blind strategies) every contract
    contracts = W.CONTRACTS if not strategy.price_aware else (case.contract,)
    out["bills"] = {}
    for c in contracts:
        b = W.bill(m.hourly, spot_hour, c, case.days)
        mean_price = b["energy"] / max(1e-9, out["house_kwh"]) + b["grid"] / max(1e-9, out["house_kwh"])
        b["store_adj"] = store_kwh * mean_price
        b["cost"] = b["total"] + b["store_adj"]
        b["value"] = b["cost"] + COMFORT_PENALTY * out["deficit_band"]
        b["value_strict"] = b["cost"] + COMFORT_PENALTY * out["deficit"]
        out["bills"][c] = {k: round(v, 2) for k, v in b.items()}
    return out
