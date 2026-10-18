"""Heat Planner itself: the ``tools.sim`` coordinator mirror (real planner/optimiser/control code).

Configured as a user would: the household schedule as per-weekday hours (offsets
from each zone's comfort temperature; absolute percent for the bathroom), the
away days as per-date overrides, the integration's default sliders, the same
believed room models as the other model-based strategies, the household's
contract and Elvia grid tariff. Presence-based away is not used (no strategy
gets presence; away days come from the calendar for everyone).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import random
from typing import Any

from tools.sim.harness import Controller, Scenario, ZoneSpec
from tools.sim.twin import TZ, UTC, base_load_kw

from . import world as W
from .sim import Context, Strategy

CONTRACT_OPT = {"spot": "spot", "spot_support": "spot_support", "norgespris": "fixed"}


class _World:
    """What ``harness.Controller`` reads from its world, backed by the benchmark context."""

    def __init__(self, ctx: Context, sc: Scenario) -> None:
        self.ctx, self.sc = ctx, sc
        self.weather = ctx.weather
        self.house_hourly = ctx.house_hourly  # completed hours, filled by the simulator

    @property
    def house_kw(self) -> float:
        return self.ctx.house_kw

    def spot(self, day: date) -> list:
        return self.ctx.spot(day)

    def home(self, now: datetime) -> bool:
        return True  # no presence-based away (see module docstring)

    def sensor(self, zid: str, now: datetime, rooms=None) -> float | None:
        return self.ctx.sensor(zid, now, rooms)


class _Controller(Controller):
    def update_savings(self, now: datetime) -> None:  # bookkeeping only, not control
        pass

    def check_plans(self, now: datetime) -> None:
        pass


class Ours(Strategy):
    """Heat Planner: ``rules`` | ``optimize`` | ``optimize`` + capacity-tariff limiting."""

    period = 1

    def __init__(self, method: str = "optimize", peak_limit: bool = False, **settings: Any) -> None:
        self.method, self.peak_limit = method, peak_limit
        self.settings = settings
        suffix = {"rules": "rules", "optimize": "opt"}[method] + ("_cap" if peak_limit else "")
        self.name = f"heat_planner_{suffix}"
        self.params = {"method": method, "peak_limit": peak_limit, **settings}

    def start(self, ctx: Context) -> None:
        season = ctx.season
        zones = []
        for z in W.ZONES:
            zones.append(ZoneSpec(
                z.zid, mode=z.mode, kw=z.kw, comfort=z.comfort, believed=z.model(), sensor=z.sensor,
                priority=z.priority, settings=dict(self.settings),
            ))
        sc = Scenario(
            f"bench_{self.name}", season.start, days=ctx.case.days, contract=CONTRACT_OPT[ctx.case.contract],
            grid="elvia_2026", optimize=self.method == "optimize", peak_limit=self.peak_limit,
            presence="home", seed=ctx.case.seed, zones=zones, cooking=True,
        )
        self.world = _World(ctx, sc)
        self._prehistory(ctx)
        ctl = _Controller(sc, self.world, rooms=ctx.rooms, devices=ctx.devices)
        away = ctx.people.away
        for z in W.ZONES:
            st = ctl.state["zones"][z.zid]
            week = {}
            for wd in range(7):
                day = season.start + timedelta(days=(wd - season.start.weekday()) % 7)
                vals = [W.target(season, z.zid, day.replace(hour=h), None) for h in range(24)]
                week[str(wd)] = [v if z.is_percent else round(v - z.comfort, 2) for v in vals]
            st["schedule"] = week
            ov = {}
            d = away[0]
            while d <= away[1]:
                start = datetime(d.year, d.month, d.day, tzinfo=TZ)
                vals = [W.target(season, z.zid, start.replace(hour=h), away) for h in range(24)]
                ov[d.isoformat()] = [v if z.is_percent else round(v - z.comfort, 2) for v in vals]
                d += timedelta(days=1)
            st["overrides"] = ov
        self.ctl = ctl
        self.last_refresh: datetime | None = None

    def _prehistory(self, ctx: Context) -> None:
        """Hourly house energy from the 1st of last month (HA statistics): the base load plus
        roughly what the schedule needs at the season's starting temperature."""
        season = ctx.season
        start = season.start
        first = (start.replace(day=1) - timedelta(days=1)).replace(day=1)
        rnd = random.Random(ctx.case.seed + 5)
        t = first.astimezone(UTC)
        end = start.astimezone(UTC)
        heat = 0.0
        for z in W.ZONES:
            g, tau, _, _ = z.true
            want = 22.0 if z.is_percent else z.comfort
            heat += z.kw * max(0.0, min(1.0, (want - season.mean(0)) / tau / g))
        while t < end:
            lt = t.astimezone(TZ)
            kwh = base_load_kw(t, True, ctx.case.seed, True) + heat * (1.3 if lt.hour in (5, 6, 7) else 1.0)
            self.world.house_hourly[lt.replace(minute=0, second=0, microsecond=0)] = round(
                kwh + rnd.uniform(-0.3, 0.3), 3)
            t += timedelta(hours=1)

    def tick(self, ctx: Context) -> None:
        now = ctx.now
        ctl = self.ctl
        if self.last_refresh is None or now.minute == 0:
            ctl.refresh(now)
            self.last_refresh = now
        elif now.minute % 5 == 0:
            for zid, plan in ctl.plans.items():
                ctl.apply(zid, plan, now)
        ctl.meter_changed(now)
