"""Closed-loop digital twin: the real planning and control code against a simulated house.

:class:`Controller` is a thin mirror of ``HeatPlannerCoordinator`` (prices →
plans → capacity limiting → live guard → apply → savings) built from the same
pure functions (``planner.build_plan``, ``control.*``, ``energy.*``,
``peak.*``, ``insights.roll_savings``, ``occupancy.*``, ``feedback.*``). Only
the Home Assistant plumbing (states, services, Store) is replaced by the twin.

:func:`run` advances the world minute by minute: the meter reports every
minute (live guard), the controller applies every 5 minutes and re-plans every
``replan_minutes`` (and when presence changes). A schedule-only *baseline*
house (same rooms, same weather, thermostats simply follow the schedule) runs
alongside for the comfort and cost comparisons.
"""

from __future__ import annotations

import os

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
import random
import statistics
import time as _time  # process_time: stable while other jobs run
from typing import Any

from custom_components.heat_planner.const import PLAN_HOURS
from custom_components.heat_planner.control import (
    HourMeter,
    decide,
    limit_peaks,
    live_shed,
    manual_changed,
    needs_write,
    off_run_hours,
    pause_end,
    safety_hold,
)
from custom_components.heat_planner.energy import (
    R_PEAK,
    apply_cuts,
    base_load,
    plan_caps,
    tariff_from_options,
)
from custom_components.heat_planner.feedback import record_feedback
from custom_components.heat_planner.insights import roll_savings
from custom_components.heat_planner.occupancy import (
    AUTO_HOUR,
    expected_arrival,
    learn_arrivals,
    warm_from_hour,
)
from custom_components.heat_planner.optimizer import R_OPTIMIZED
from custom_components.heat_planner.peak import PeakConfig, top3_average
from custom_components.heat_planner.planner import (
    R_EXPENSIVE,
    R_EXTREME_OFF,
    R_PREHEAT,
    R_PREHEAT_EXTREME,
    Plan,
    PlanHour,
    PriceRules,
    ThermalModel,
    ZoneConfig,
    build_plan,
    comfort_reference,
    default_schedule,
    hourly_prices,
    is_protected,
    scheduled_value,
)
from custom_components.heat_planner.tariff import effective_price, effective_prices, fixed_prices

from .twin import (
    TZ,
    UTC,
    Device,
    TrueRoom,
    Weather,
    base_load_kw,
    local,
    presence,
    spot_day,
)

import custom_components.heat_planner.control as _control
import custom_components.heat_planner.energy as _energy
import custom_components.heat_planner.planner as _planner

# The harness mirrors the coordinator of the code under test; NEW = with the
# fixes from the simulation work (lets the same harness measure the old code).
NEW = hasattr(_planner, "baseline_hour")

PRICE_REASONS = {R_PREHEAT, R_PREHEAT_EXTREME, R_EXPENSIVE, R_EXTREME_OFF, R_OPTIMIZED}
TUNABLE = (
    "comfort", "min_value", "max_value", "away_offset", "away_until_hour", "protected_from_hour",
    "protected_to_hour", "price_shift", "preheat", "extreme_off", "safety_min_temp", "heater_kw",
    "price_aggressiveness", "max_off_hours",
)


# --------------------------------------------------------------------------- scenario


@dataclass
class ZoneSpec:
    zone_id: str
    mode: str = "setpoint"
    kw: float = 1.0
    comfort: float = 21.0
    true: dict[str, float] = field(default_factory=lambda: {"gross": 1.4, "tau": 30.0, "lag_h": 1.0, "solar_gain": 0.3})
    believed: ThermalModel = field(default_factory=lambda: ThermalModel(1.0, 40.0, learned=True, lag_hours=0.5))
    start_temp: float = 20.5
    sensor: bool = True  # separate room sensor (else the thermostat's own reading)
    priority: int = 0
    settings: dict[str, Any] = field(default_factory=dict)


def default_zones() -> list[ZoneSpec]:
    return [
        ZoneSpec("stue", kw=2.0, comfort=21.0,
                 true={"gross": 1.3, "tau": 35.0, "lag_h": 1.0, "solar_gain": 0.4},
                 believed=ThermalModel(1.0, 45.0, learned=True, lag_hours=0.5, solar_gain=0.2)),
        ZoneSpec("kjokken", kw=1.2, comfort=21.0, priority=1, sensor=False,
                 true={"gross": 1.6, "tau": 28.0, "lag_h": 0.5, "solar_gain": 0.2},
                 believed=ThermalModel(1.8, 30.0, learned=True, lag_hours=0.0)),
        ZoneSpec("sov", kw=0.8, comfort=19.0, start_temp=18.8,
                 true={"gross": 1.1, "tau": 40.0, "lag_h": 1.5, "solar_gain": 0.1},
                 believed=ThermalModel()),
        ZoneSpec("bad", mode="percent", kw=0.6, comfort=0.0, start_temp=22.0,
                 true={"gross": 1.0, "tau": 30.0, "lag_h": 0.5, "solar_gain": 0.0},
                 believed=ThermalModel(1.0, 40.0, learned=True)),
    ]


@dataclass
class Fault:
    kind: str  # prices_down | forecast_down | weather_down | sensor_down | meter_down | device_down
    start: datetime
    end: datetime
    zone: str | None = None

    def active(self, now: datetime) -> bool:
        return self.start <= now < self.end


@dataclass
class Scenario:
    name: str
    start: datetime
    days: float = 7
    contract: str = "spot_support"
    grid: str = "elvia_2026"
    optimize: bool = True
    peak_limit: bool = True
    outdoor_mean: float = 0.0
    forecast_error: float = 1.0
    price_pattern: tuple[str, ...] = ("normal",)
    quarter_hour: bool = False
    presence: str = "workweek"
    holiday: tuple[date, date] | None = None
    feedback: list[tuple[datetime, str, str]] = field(default_factory=list)
    manual: list[tuple[datetime, str, float]] = field(default_factory=list)
    faults: list[Fault] = field(default_factory=list)
    restarts: list[tuple[datetime, bool]] = field(default_factory=list)  # (when, graceful)
    restart_downtime: timedelta = timedelta(minutes=2)
    replan_minutes: int = 60
    aggressiveness: float = 1.0
    auto_arrival: bool = False
    seed: int = 1
    zones: list[ZoneSpec] = field(default_factory=default_zones)
    cooking: bool = True  # 2.2 kW cooking spikes 17–19 in the base load
    trace: str | None = None  # zone id: record a 15-minute trace in Result.trace

    def fault(self, kind: str, now: datetime, zone: str | None = None) -> bool:
        return any(f.kind == kind and f.active(now) and (f.zone is None or f.zone == zone) for f in self.faults)


# --------------------------------------------------------------------------- the world


class World:
    """True rooms, devices, weather, prices and people (controller and baseline house)."""

    def __init__(self, sc: Scenario) -> None:
        self.sc = sc
        self.weather = Weather(sc.outdoor_mean, seed=sc.seed)
        self.rooms = {z.zone_id: TrueRoom(temp=z.start_temp, **z.true) for z in sc.zones}
        self.base_rooms = {z.zone_id: TrueRoom(temp=z.start_temp, **z.true) for z in sc.zones}
        self.devices = {z.zone_id: Device(z.mode, z.kw, setpoint=z.comfort or 21.0, step=0.5 if z.mode == "setpoint" else 1.0) for z in sc.zones}
        self.base_devices = {z.zone_id: Device(z.mode, z.kw, setpoint=z.comfort or 21.0) for z in sc.zones}
        # schedule + away, no price or peak logic (the comfort reference)
        self.away_rooms = {z.zone_id: TrueRoom(temp=z.start_temp, **z.true) for z in sc.zones}
        self.away_devices = {z.zone_id: Device(z.mode, z.kw, setpoint=z.comfort or 21.0, step=0.5 if z.mode == "setpoint" else 1.0) for z in sc.zones}
        self.rnd = random.Random(sc.seed)
        self._spot: dict[date, list] = {}
        self.house_hourly: dict[datetime, float] = {}  # local hour -> kWh (as HA statistics would key it)
        self.house_kw = 0.0

    def spot(self, day: date) -> list:
        if day not in self._spot:
            kinds = self.sc.price_pattern
            kind = kinds[(day - self.sc.start.date()).days % len(kinds)]
            self._spot[day] = spot_day(day, kind, self.sc.seed, self.sc.quarter_hour)
        return self._spot[day]

    def true_spot(self, now: datetime) -> float:
        for p in self.spot(local(now).date()):
            if p.start <= now < p.end:
                return p.price
        return self.spot(local(now).date())[-1].price

    def home(self, now: datetime) -> bool:
        return presence(now, self.sc.presence, self.sc.holiday, self.sc.seed)

    def sensor(self, zid: str, now: datetime, rooms: dict[str, TrueRoom] | None = None) -> float | None:
        if self.sc.fault("sensor_down", now, zid):
            return None
        # ±0.1 °C noise, a fixed function of zone and minute: how often the controller
        # reads a sensor must not change what the next reading is (A/B runs comparable)
        minute = int(now.timestamp() // 60)
        key = (self.sc.seed * 1_000_003 + sum(map(ord, zid)) * 7_919 + minute) * 2_654_435_761 % 2**32
        return round((rooms or self.rooms)[zid].temp + (key / 2**32 - 0.5) * 0.2, 1)


# --------------------------------------------------------------------------- controller (coordinator mirror)


class Controller:
    """Mirror of HeatPlannerCoordinator built from the integration's pure functions."""

    def __init__(self, sc: Scenario, world: World, rooms=None, devices=None) -> None:
        self.sc, self.w = sc, world
        self.rooms = rooms if rooms is not None else world.rooms
        self.devices = devices if devices is not None else world.devices
        self.options = {
            "energy": {
                "contract": sc.contract, "grid": sc.grid, "fixed_price": 0.5,
                "peak_limit": sc.peak_limit, "house_power_sensor": "sensor.house_power",
            },
            "zones": [{"zone_id": z.zone_id, "priority": z.priority} for z in sc.zones],
        }
        self.specs = {z.zone_id: z for z in sc.zones}
        # persisted (Store)
        self.state: dict[str, Any] = {"zones": {}, "savings": {}, "optimize": sc.optimize}
        for z in sc.zones:
            settings = {"price_aggressiveness": sc.aggressiveness, **z.settings}
            if sc.auto_arrival:
                settings["away_until_hour"] = AUTO_HOUR
            self.state["zones"][z.zone_id] = {
                "schedule": {str(k): v for k, v in default_schedule(z.mode).items()},
                "overrides": {}, "settings": settings, "auto": True, "thermal": z.believed,
            }
        self.presence_log: list[tuple[datetime, str]] = []
        self.boot()
        # book-keeping for KPIs
        self.claimed = 0.0
        self.replan_ms: list[float] = []
        self.price_hours_applied = 0
        self.plan_violations: list[str] = []

    def boot(self) -> None:
        """In-memory state (lost on a restart)."""
        self.plans: dict[str, Plan] = {}
        self.price_hours: dict[datetime, float] = {}
        self.cache: dict[date, list] = {}
        self.outdoor: dict[datetime, float] = {}
        self.solar: dict[datetime, float] = {}
        self.outdoor_fetched: datetime | None = None
        self.away = False
        self.applied: dict[str, dict[str, Any]] = {}
        self.safety: dict[str, bool] = {}
        self.shed: dict[str, datetime] = {}
        self.meter = HourMeter()
        self.guard_at: datetime | None = None
        self.house_kwh: dict[datetime, float] = {}
        self.house_fetched: datetime | None = None
        self.peak_cap: float | None = None
        self.peak_base: dict[int, float] = {}
        self.up = True

    # -- config
    def zone(self, zid: str, now: datetime) -> ZoneConfig:
        st, spec = self.state["zones"][zid], self.specs[zid]
        base: dict[str, Any]
        if spec.mode == "percent":
            base = dict(comfort=0.0, min_value=0.0, max_value=100.0, away_offset=-20.0, price_shift=15.0, preheat=10.0)
        else:
            base = dict(comfort=spec.comfort)
        base["heater_kw"] = spec.kw
        if "warmup" in ZoneConfig.__dataclass_fields__:
            base["warmup"] = True  # as the coordinator
        settings = {k: v for k, v in st["settings"].items() if k in TUNABLE}
        zc = ZoneConfig(zone_id=zid, name=zid, mode=spec.mode, thermal=st["thermal"], **{**base, **settings})
        if zc.away_until_hour == AUTO_HOUR:
            from custom_components.heat_planner.coordinator import HeatPlannerCoordinator

            arrivals = self.state.get("arrivals")
            zc = ZoneConfig(**{**zc.__dict__, "away_until_hour": warm_from_hour(
                expected_arrival(arrivals, now.date()), HeatPlannerCoordinator.lead_hours(zc), default=13)})
        return zc

    def schedule(self, zid: str) -> dict[int, list[float]]:
        return {int(k): v for k, v in self.state["zones"][zid]["schedule"].items()}

    def manual_until(self, zid: str, now: datetime) -> datetime | None:
        val = self.state["zones"][zid].get("manual_until")
        if val and val > now:
            return val
        self.state["zones"][zid].pop("manual_until", None)
        return None

    # -- inputs
    def room_temperature(self, zid: str, now: datetime) -> float | None:
        spec = self.specs[zid]
        if spec.sensor:
            v = self.w.sensor(zid, now, self.rooms)
            if v is not None:
                return v
        if spec.mode == "setpoint" and not self.sc.fault("sensor_down", now, zid):
            return round(self.rooms[zid].temp, 1)  # thermostat's own reading
        return None

    def device(self, zid: str, now: datetime) -> tuple[bool, bool, float | None, float]:
        d = self.devices[zid]
        if not d.available or self.sc.fault("device_down", now, zid):
            return False, False, None, d.step
        if d.mode == "percent":
            return True, d.value <= 0, d.value, d.step
        if d.off:
            return True, True, None, d.step
        return True, False, d.setpoint, d.step

    def write(self, zid: str, off: bool, value: float | None, now: datetime) -> None:
        d = self.devices[zid]
        d.writes.append(now)
        if d.mode == "percent":
            d.value = 0 if off else value
            return
        d.off = off
        if not off:
            d.setpoint = value

    def outdoor_now(self, now: datetime) -> float:
        if self.sc.fault("weather_down", now):
            return 5.0
        return round(self.w.weather.outdoor(now), 1)

    def meter_kw(self, now: datetime) -> float | None:
        if self.sc.fault("meter_down", now):
            return None
        return self.w.house_kw

    # -- data refresh
    def fetch_prices(self, now: datetime) -> None:
        today = now.date()
        for old in [d for d in self.cache if d < today - timedelta(days=1)]:
            self.cache.pop(old)
        for day in (today, today + timedelta(days=1)):
            if day in self.cache or self.sc.fault("prices_down", now):
                continue
            published = day <= today or now >= datetime(today.year, today.month, today.day, 13, tzinfo=TZ)
            if published:
                self.cache[day] = self.w.spot(day)
        points = [p for d in sorted(self.cache) for p in self.cache[d]]
        spot_hours = hourly_prices(points)
        tariff = tariff_from_options(self.options)
        if tariff.mode == "fixed" and not spot_hours:
            if NEW:
                from custom_components.heat_planner.planner import hours_from

                self.price_hours = fixed_prices(hours_from(now, PLAN_HOURS), tariff)
            else:
                start = now.replace(minute=0, second=0, microsecond=0)
                self.price_hours = fixed_prices(_hours_from(start, PLAN_HOURS), tariff)
        else:
            self.price_hours = effective_prices(spot_hours, tariff)

    def fetch_outdoor(self, now: datetime) -> None:
        if self.outdoor_fetched and now - self.outdoor_fetched < timedelta(hours=1):
            return
        self.outdoor_fetched = now
        if self.sc.fault("forecast_down", now) or self.sc.fault("weather_down", now):
            return  # the service call fails: keep what we had
        self.solar = self.w.weather.forecast_solar(now)
        out = self.w.weather.forecast(now, error=self.sc.forecast_error)
        if out:
            self.outdoor = out

    def house_history(self, now: datetime) -> dict[datetime, float]:
        if self.house_fetched is None or now - self.house_fetched > timedelta(minutes=30):
            self.house_fetched = now
            self.house_kwh = dict(self.w.house_hourly)
        return self.house_kwh

    # -- planning
    def off_run(self, zid: str, now: datetime) -> int:
        last = self.applied.get(zid)
        if not NEW:
            if not last or not last.get("off"):
                return 0
            return off_run_hours(last.get("off_since"), now)
        if last is not None:
            since = last.get("off_since") if last.get("off") else None
        else:
            since = self.state["zones"][zid].get("off_since")  # persisted across restarts
        return off_run_hours(since, now)

    def plan_zone(self, zid: str, now: datetime, *, zone=None, hours=PLAN_HOURS, start=None, adjust=None, method=None,
                  kw_cap=None) -> Plan:
        st = self.state["zones"][zid]
        extra = {"kw_cap": kw_cap} if kw_cap else {}
        return build_plan(
            zone or self.zone(zid, now), self.schedule(zid), st["overrides"], self.price_hours, PriceRules(),
            start or now, hours, away_now=self.away, room_temp=self.room_temperature(zid, now),
            outdoor=self.outdoor, outdoor_default=self.outdoor_now(now),
            method=method or ("optimize" if self.state.get("optimize") else "rules"),
            initial_off_run=self.off_run(zid, now), solar=self.solar or None, adjust=adjust, **extra,
        )

    def limit(self, now: datetime) -> None:
        if not self.sc.peak_limit or not self.plans:
            self.peak_cap = None
            return
        hours = sorted({h.start for p in self.plans.values() for h in p.hours})
        hourly = self.house_history(now)
        if not hours or not hourly:
            self.peak_cap = None
            return
        zones = {zid: self.zone(zid, now) for zid in self.plans}
        heating_avg = sum(sum(h.kwh for h in p.hours) / max(len(p.hours), 1) for p in self.plans.values())
        base = {h: max(0.0, v - heating_avg) for h, v in base_load(hourly, now=now).items()}
        self.peak_base = base
        priority = {z.zone_id: z.priority for z in self.sc.zones}
        joint = NEW and self.state.get("optimize") and hasattr(_control, "plan_peaks")
        if joint:
            # mirror of the coordinator: aim for a lower step when reachable, and plan the
            # heating against the house budget (optimiser), see control.plan_peaks
            high = _energy.rest_of_house(hourly, self.state.setdefault("heat_log", {}), heating_avg, now)
            self.peak_high = high
            typical = _energy.rest_of_house(hourly, self.state["heat_log"], heating_avg, now, q=0.75)
            self.peak_dbg = (round(max(typical.values(), default=0), 2), round(heating_avg, 2))
            floor = _energy.peak_floor(typical, heating_avg, _energy.peak_storage(list(zones.values())))
            caps, state = plan_caps(PeakConfig(), hourly, hours, now, floor_kw=floor)

            def replan_cap(zid, kw_cap):
                return self.plan_zone(zid, now, zone=zones[zid], kw_cap=kw_cap)

            self.plans = _control.plan_peaks(zones, dict(self.plans), caps, high, priority, replan_cap)
            self.peak_cap = state.cap_kw
            return
        caps, state = plan_caps(PeakConfig(), hourly, hours, now)

        def replan(zid, cuts, pred):
            zone = zones[zid]
            return self.plan_zone(zid, now, zone=zone, adjust=lambda slots: apply_cuts(zone, slots, cuts, pred))

        if NEW:
            high = {h: max(0.0, v - heating_avg) for h, v in base_load(hourly, now=now, high=True).items()}
            self.plans = limit_peaks(zones, dict(self.plans), caps, base, priority, replan, check_base=high)
        else:
            self.plans = limit_peaks(zones, dict(self.plans), caps, base, priority, replan)
        self.peak_cap = state.cap_kw

    def guard(self, now: datetime) -> None:
        kw = self.meter_kw(now)
        if self.peak_cap is None or kw is None:
            self.shed.clear()
            return
        self.meter.add(kw, now)
        projected = self.meter.projected(now)
        if projected is None:
            projected = kw
        zones = {zid: self.zone(zid, now) for zid in self.plans}
        priority = {z.zone_id: z.priority for z in self.sc.zones}
        if NEW:
            from custom_components.heat_planner.control import too_cold

            holding = {zid for zid, hold in self.safety.items() if hold} | {
                zid for zid, p in self.plans.items()
                if p.hours and too_cold(zones[zid], p.hours[0], self.room_temperature(zid, now))
            }
            smooth = self.meter.projected(now, smooth=True)
            self.shed = live_shed(projected if smooth is None else smooth, self.peak_cap, self.plans, zones,
                                  priority, self.shed, now, holding)
        else:
            self.shed = live_shed(projected, self.peak_cap, self.plans, zones, priority, self.shed, now)

    def meter_changed(self, now: datetime) -> None:
        if not self.up:
            return
        if self.guard_at and now - self.guard_at < timedelta(seconds=60):
            kw = self.meter_kw(now)
            if kw is not None:
                self.meter.add(kw, now)
            return
        self.guard_at = now
        before = set(self.shed)
        self.guard(now)
        if set(self.shed) != before:
            for zid in before ^ set(self.shed):
                self.applied.pop(zid, None)
            for zid, plan in self.plans.items():
                self.apply(zid, plan, now)

    def refresh(self, now: datetime) -> None:
        if not self.up:
            return
        self.fetch_prices(now)
        self.fetch_outdoor(now)
        self.away = not self.w.home(now)
        t0 = _time.process_time()
        self.plans = {}
        for z in self.sc.zones:
            self.plans[z.zone_id] = self.plan_zone(z.zone_id, now)
        self.replan_ms.append((_time.process_time() - t0) * 1000)
        self.limit(now)
        if hasattr(_energy, "log_heating"):
            _energy.log_heating(self.state.setdefault("heat_log", {}), self.plans, now)
        self.guard(now)
        for zid, plan in self.plans.items():
            self.apply(zid, plan, now)
        self.update_savings(now)
        self.check_plans(now)

    # -- control
    def restore(self, zid: str, now: datetime) -> None:
        last = self.applied.get(zid)
        if not last or not last.get("off"):
            return
        available, dev_off, _, step = self.device(zid, now)
        if not available:
            return
        zone = self.zone(zid, now)
        value, _ = scheduled_value(zone, self.schedule(zid), self.state["zones"][zid]["overrides"], now)
        value = round(round(max(zone.min_value, min(zone.max_value, value)) / step) * step, 2)
        self.write(zid, False, value, now)
        self.applied[zid] = {"value": value, "off": False, "at": now}

    def apply(self, zid: str, plan: Plan, now: datetime) -> None:
        st = self.state["zones"][zid]
        if not st.get("auto", True) or not plan.hours:
            return
        available, dev_off, current, step = self.device(zid, now)
        if not available:
            return
        last = self.applied.get(zid)
        paused = self.manual_until(zid, now)
        if not paused and manual_changed(last, dev_off, current, step, now):
            paused = pause_end(plan, now)
            st["manual_until"] = paused
        if paused:
            self.applied[zid] = {"value": current, "off": dev_off, "at": last["at"] if last else now}
            return
        zone = self.zone(zid, now)
        hour = plan.hours[0]
        hold = safety_hold(self.room_temperature(zid, now), zone, self.safety.get(zid, False))
        self.safety[zid] = hold
        off, value = decide(zone, hour, step, shed=zid in self.shed, hold=hold)
        if NEW:
            from custom_components.heat_planner.control import settle

            value = settle(zone, last, hour.start, off, value, hold=hold)
        needs = needs_write(zone, off, value, dev_off, current, step)
        if needs:
            self.write(zid, off, value, now)
        if NEW:
            since = None
            if off:
                since = (last.get("off_since") if last.get("off") else None) if last else st.get("off_since")
                since = since or now
            if since:
                st["off_since"] = since
            else:
                st.pop("off_since", None)
        else:
            since = (last.get("off_since") if last and last.get("off") else now) if off else None
        self.applied[zid] = {
            "value": None if off and not zone.is_percent else (0 if off else value),
            "off": off,
            "at": now if needs or not last else last["at"],
            "off_since": since,
            "hour": hour.start,
        }

    def shutdown(self, now: datetime) -> None:
        for zid in list(self.applied):
            self.restore(zid, now)
        self.up = False

    def start(self, now: datetime) -> None:
        self.boot()
        self.refresh(now)

    # -- savings and plan checks
    def hour_savings(self, hour: datetime, now: datetime) -> float | None:
        if NEW:
            return self.hour_savings_shadow(hour, now)
        total, any_zone = 0.0, False
        for zid, plan in self.plans.items():
            if not plan.hours or plan.hours[0].start != hour or plan.hours[0].cost is None:
                continue
            if self.manual_until(zid, now):
                continue
            base = self.plan_zone(zid, now, hours=1, method="rules", start=hour)
            if base.baseline_cost is None:
                continue
            total += base.baseline_cost - plan.hours[0].cost
            any_zone = True
        return total if any_zone else None

    def hour_savings_shadow(self, hour: datetime, now: datetime) -> float | None:
        """Mirror of the coordinator's _hour_savings (two shadow rooms)."""
        from custom_components.heat_planner.planner import baseline_hour, hour_key, simulate_hour

        hour = hour_key(hour)
        shadow = self.state["savings"].setdefault("shadow", {})
        total, any_zone = 0.0, False
        for zid, plan in self.plans.items():
            if not plan.hours or plan.hours[0].start != hour or plan.hours[0].cost is None:
                continue
            if self.manual_until(zid, now):
                shadow.pop(zid, None)
                continue
            prev = shadow.get(zid) or {}
            if prev.get("hour") == hour and prev.get("base") is not None:
                base_t, base_q, plan_t, plan_q = prev["base"], prev["base_queue"], prev["plan"], prev["plan_queue"]
            else:
                base_t = plan_t = self.room_temperature(zid, now)
                base_q = plan_q = None
            zone = self.zone(zid, now)
            kw = dict(outdoor=self.outdoor, outdoor_default=self.outdoor_now(now), solar=self.solar or None)
            if "warmup" in PlanHour.__dataclass_fields__:
                kw["warmup"] = plan.hours[0].warmup
            base, base_q = baseline_hour(zone, self.schedule(zid), self.state["zones"][zid]["overrides"],
                                         self.price_hours, hour, room_temp=base_t, queue=base_q, **kw)
            kw.pop("warmup", None)
            ours, plan_q = simulate_hour(zone, plan.hours[0], room_temp=plan_t, queue=plan_q, **kw)
            if base.cost is None or ours.cost is None:
                shadow.pop(zid, None)
                continue
            shadow[zid] = {"hour": hour + timedelta(hours=1), "base": base.predicted, "base_queue": base_q,
                           "plan": ours.predicted, "plan_queue": plan_q}
            total += base.cost - ours.cost
            any_zone = True
        return total if any_zone else None

    def update_savings(self, now: datetime) -> None:
        hour = now.replace(minute=0, second=0, microsecond=0)
        if NEW:
            from custom_components.heat_planner.planner import hour_key

            hour = hour_key(now)
        sv = self.state["savings"]
        pending = sv.get("pending")
        roll_savings(sv, hour, lambda: self.hour_savings(hour, now))
        if pending and sv.get("pending") is not pending:
            self.claimed += float(pending.get("amount") or 0.0)

    def check_plans(self, now: datetime) -> None:
        for zid, plan in self.plans.items():
            zone = self.zone(zid, now)
            for h in plan.hours:
                ref = comfort_reference(zone, h)
                if is_protected(zone, h.start) and h.target is not None and h.target < ref - 0.26 and R_PEAK not in h.reasons:
                    self.plan_violations.append(f"{zid} {h.start.isoformat()} protected lowered {h.target} < {ref}")
                if is_protected(zone, h.start) and h.target is None:
                    self.plan_violations.append(f"{zid} {h.start.isoformat()} protected off")
        if self.sc.contract == "fixed":
            for plan in self.plans.values():
                if plan.hours and PRICE_REASONS & set(plan.hours[0].reasons):
                    self.price_hours_applied += 1


def bd_mode(world: World, zid: str) -> str:
    return world.base_devices[zid].mode


def _hours_from(start: datetime, n: int) -> list[datetime]:
    """Exactly what the coordinator does for fixed-price hours."""
    return [start + timedelta(hours=i) for i in range(n)]


# --------------------------------------------------------------------------- running


@dataclass
class Result:
    scenario: str
    days: float
    kpi: dict[str, Any]
    violations: list[str]
    trace: list[tuple] = field(default_factory=list)
    hourly: dict[datetime, float] = field(default_factory=dict)  # true house kWh per hour (UTC)

    def ok(self) -> bool:
        return not self.violations


def _prehistory(world: World, start: datetime) -> None:
    """Hourly house energy from the 1st of last month up to the start (HA statistics)."""
    first = (start.replace(day=1) - timedelta(days=1)).replace(day=1, hour=0, minute=0)
    t = first.astimezone(UTC)
    end = start.astimezone(UTC)
    rnd = random.Random(world.sc.seed + 5)
    while t < end:
        lt = t.astimezone(TZ)
        heat = 1.6 + (0.9 if lt.hour in (5, 6, 7) else 0.0) + rnd.uniform(-0.3, 0.3)
        world.house_hourly[lt.replace(minute=0, second=0, microsecond=0)] = round(
            base_load_kw(t, True, world.sc.seed, world.sc.cooking) + heat, 3)
        t += timedelta(hours=1)


def run(sc: Scenario) -> Result:
    world = World(sc)
    _prehistory(world, sc.start)
    ctl = Controller(sc, world)
    ref = Controller(
        replace(sc, aggressiveness=0.0, peak_limit=False, optimize=False, faults=[], restarts=[], feedback=[], manual=[]),
        world, rooms=world.away_rooms, devices=world.away_devices,
    )

    def ref_refresh(now: datetime) -> None:
        for zid, st in ctl.state["zones"].items():
            rst = ref.state["zones"][zid]
            rst["settings"] = {**st["settings"], "price_aggressiveness": 0.0}
            rst["schedule"] = st["schedule"]
        ref.refresh(now)
    tariff = tariff_from_options(ctl.options)

    t = sc.start.astimezone(UTC)
    end = t + timedelta(days=sc.days)
    restarts = sorted(sc.restarts)
    down_until: datetime | None = None
    feedback = sorted(sc.feedback)
    manual = sorted(sc.manual)
    was_home = None
    last_refresh: datetime | None = None
    hour_kwh = 0.0
    cur_hour: datetime | None = None
    true_hourly: dict[datetime, float] = {}  # UTC hour -> kWh (house)

    zones = [z.zone_id for z in sc.zones]
    setpoint_zones = [z.zone_id for z in sc.zones if z.mode == "setpoint"]
    cost = {"ctl": 0.0, "base": 0.0, "ref": 0.0}
    deficit = {z: {"ctl": 0.0, "base": 0.0} for z in zones}
    min_temp = {z: 99.0 for z in zones}
    safety_minutes = {z: 0 for z in zones}
    off_minutes = {z: 0 for z in zones}
    max_off = {z: 0 for z in zones}
    protected_bad = 0
    first_below: dict[str, Any] = {}
    trace: list[tuple] = []
    floors: dict[str, list[float]] = {}
    ref_below = {z: 0 for z in zones}
    caused = {z: 0 for z in zones}
    lowered_at: dict[str, datetime] = {}
    violations: list[str] = []

    world.house_kw = 0.0
    while t < end:
        now = t.astimezone(TZ)
        home = world.home(now)

        # ---- scripted events
        while restarts and restarts[0][0] <= now and ctl.up:
            when, graceful = restarts.pop(0)
            if graceful:
                ctl.shutdown(now)
            ctl.up = False
            down_until = now + sc.restart_downtime
        if not ctl.up and down_until and now >= down_until:
            ctl.up = True
            ctl.start(now)
            last_refresh = now
        while manual and manual[0][0] <= now:
            _, zid, value = manual.pop(0)
            d = world.devices[zid]
            if d.mode == "percent":
                d.value = value
            else:
                d.off, d.setpoint = False, value
        while feedback and feedback[0][0] <= now:
            _, zid, kind = feedback.pop(0)
            if ctl.up:
                zone = ctl.zone(zid, now)
                st = ctl.state["zones"][zid]
                before = {k: getattr(zone, k) for k in TUNABLE}
                if before.get("away_until_hour") != st["settings"].get("away_until_hour", before["away_until_hour"]):
                    before["away_until_hour"] = st["settings"]["away_until_hour"]
                plan = ctl.plans.get(zid)
                mode = plan.hours[0].mode if plan and plan.hours else "normal"
                record_feedback(st, before, kind, mode, today=now.date().isoformat(), percent=zone.is_percent,
                                room_temp=ctl.room_temperature(zid, now), safety_min_temp=zone.safety_min_temp, now=now)
                ctl.applied.pop(zid, None)
                ctl.refresh(now)
                last_refresh = now

        # ---- reference house (schedule + away): hourly, on presence changes, applies every 5 min
        if was_home is None or home != was_home or now.minute == 0:
            ref_refresh(now)
        elif now.minute % 5 == 0:
            for zid, plan in ref.plans.items():
                ref.apply(zid, plan, now)

        # ---- controller
        if ctl.up:
            if was_home is not None and home != was_home:
                ctl.presence_log.append((now, "home" if home else "not_home"))
                ctl.refresh(now)
                last_refresh = now
            elif last_refresh is None or (now.minute == 0 and sc.replan_minutes <= 60) or (
                now - last_refresh >= timedelta(minutes=sc.replan_minutes)
            ):
                ctl.refresh(now)
                last_refresh = now
            elif now.minute % 5 == 0:
                for zid, plan in ctl.plans.items():
                    ctl.apply(zid, plan, now)
            ctl.meter_changed(now)
            if sc.auto_arrival and now.hour == 3 and now.minute == 17:
                series = {"person.a": [(w, s) for w, s in ctl.presence_log]}
                ctl.state["arrivals"] = learn_arrivals(series, TZ, now=now, previous=ctl.state.get("arrivals"))
        was_home = home

        # ---- baseline thermostats follow the schedule (no prices, no away)
        for zid in zones:
            zone = ctl.zone(zid, now)
            v, _ = scheduled_value(zone, ctl.schedule(zid), {}, now)
            # the schedule's own optimal start (the reference house plans it; no price logic)
            rp = ref.plans.get(zid)
            if rp and rp.hours and getattr(rp.hours[0], "warmup", None) is not None and bd_mode(world, zid) == "setpoint":
                v = max(v, rp.hours[0].warmup)
            bd = world.base_devices[zid]
            if ctl.up and ctl.manual_until(zid, now):
                # someone set this thermostat by hand: the schedule-only house gets the same
                # change for as long as it holds (realised savings compare like with like)
                v = world.devices[zid].value if bd.mode == "percent" else world.devices[zid].setpoint
            if bd.mode == "percent":
                bd.value = v
            else:
                bd.setpoint = max(zone.min_value, min(zone.max_value, v))

        # ---- physics, one minute
        t_out = world.weather.outdoor(now)
        sol = world.weather.solar(now)
        price = effective_price(now.replace(minute=0, second=0, microsecond=0), world.true_spot(now), tariff)
        heat_kw = base_kw = 0.0
        for zid in zones:
            room, dev = world.rooms[zid], world.devices[zid]
            duty = dev.duty(room.sensed(t_out))
            room.step(duty, t_out, sol, home)
            heat_kw += duty * dev.kw
            broom, bdev = world.base_rooms[zid], world.base_devices[zid]
            bduty = bdev.duty(broom.sensed(t_out))
            broom.step(bduty, t_out, sol, home)
            base_kw += bduty * bdev.kw
            aroom, adev = world.away_rooms[zid], world.away_devices[zid]
            aduty = adev.duty(aroom.sensed(t_out))
            aroom.step(aduty, t_out, sol, home)
            cost["ref"] += aduty * adev.kw / 60 * price
            cost["ctl"] += duty * dev.kw / 60 * price
            cost["base"] += bduty * bdev.kw / 60 * price
            # comfort while someone is home, against the current schedule
            if home:
                cref = scheduled_value(ctl.zone(zid, now), ctl.schedule(zid), {}, now)[0]
                if dev.mode == "setpoint":
                    deficit[zid]["ctl"] += max(0.0, cref - room.temp) / 60
                    deficit[zid]["base"] += max(0.0, cref - aroom.temp) / 60
            min_temp[zid] = min(min_temp[zid], room.temp)
            floor = ctl.zone(zid, now).safety_min_temp - 0.3
            if ref.plans.get(zid) and ref.plans[zid].hours:
                # the user's own schedule/away floor may be lower than the safety floor,
                # and a room needs a while to climb back from it (3 h window)
                own = comfort_reference(ref.zone(zid, now), ref.plans[zid].hours[0]) - 0.3
                recent = floors.setdefault(zid, [])
                recent.append(own)
                del recent[:-180]
                floor = min(floor, min(recent))
            # attributable to the controller: it held this room lower than the
            # reference house (or off) in the last 3 hours (the floor's dead time)
            if dev.mode == "setpoint" and (dev.off and not adev.off or not dev.off and dev.setpoint < adev.setpoint - 0.01):
                lowered_at[zid] = now
            recently = zid in lowered_at and now - lowered_at[zid] <= timedelta(hours=3)
            if dev.mode == "setpoint" and room.temp < floor:
                safety_minutes[zid] += 1
                if recently and aroom.temp >= floor:
                    caused[zid] += 1
                    first_below.setdefault(zid, (now.isoformat(), round(room.temp, 2), dev.off, dev.setpoint))
            if dev.mode == "setpoint" and aroom.temp < floor:
                ref_below[zid] += 1
            if dev.off or (dev.mode == "percent" and dev.value <= 0):
                off_minutes[zid] += 1
                max_off[zid] = max(max_off[zid], off_minutes[zid])
            else:
                off_minutes[zid] = 0
            if zid == sc.trace and now.minute % 15 == 0:
                ph = ctl.plans[zid].hours[0] if ctl.plans.get(zid) and ctl.plans[zid].hours else None
                trace.append((now.isoformat()[5:16], "off" if dev.off else dev.setpoint, round(room.temp, 2),
                              adev.setpoint, round(aroom.temp, 2), ph and ph.target, ph and ",".join(ph.reasons[1:]),
                              ph and ph.predicted))
            # protected window: never price-lowered on the device
            if ctl.up and dev.mode == "setpoint" and now.minute % 5 == 0 and zid in ctl.plans and not ctl.manual_until(zid, now):
                zone = ctl.zone(zid, now)
                if is_protected(zone, now) and ctl.plans[zid].hours:
                    cref = comfort_reference(zone, ctl.plans[zid].hours[0])
                    if dev.off or dev.setpoint < cref - 0.26:
                        protected_bad += 1
        world.house_kw = base_load_kw(now, home, sc.seed, sc.cooking) + heat_kw
        # true house energy per local clock hour (what the grid company bills)
        h_utc = t.replace(minute=0, second=0, microsecond=0)
        if cur_hour != h_utc:
            if cur_hour is not None:
                true_hourly[cur_hour] = hour_kwh
                world.house_hourly[cur_hour.astimezone(TZ)] = round(hour_kwh, 3)
            cur_hour, hour_kwh = h_utc, 0.0
        hour_kwh += world.house_kw / 60
        t += timedelta(minutes=1)

    # ------------------------------------------------------------------ KPIs
    days = sc.days
    writes = {}
    for zid in zones:
        per_day: dict[date, int] = {}
        for w in world.devices[zid].writes:
            per_day[local(w).date()] = per_day.get(local(w).date(), 0) + 1
        writes[zid] = max(per_day.values(), default=0)
    months: dict[tuple[int, int], dict[date, float]] = {}
    for h, v in true_hourly.items():
        lt = h.astimezone(TZ)
        m = months.setdefault((lt.year, lt.month), {})
        m[lt.date()] = max(m.get(lt.date(), 0.0), v)
    top3 = {f"{y}-{m:02d}": round(top3_average(list(d.values())), 2) for (y, m), d in months.items()}
    extra_def = max(
        ((deficit[z]["ctl"] - deficit[z]["base"]) / days for z in setpoint_zones), default=0.0)
    realised = cost["base"] - cost["ctl"]
    kpi = {
        "min_temp": round(min(min_temp[z] for z in setpoint_zones), 2),
        "safety_min": min(ctl.zone(z, sc.start).safety_min_temp for z in setpoint_zones),
        # minutes below min(safety floor, the user's own schedule/away floor) − 0.3,
        # beyond what the same house without price/peak control had
        "below_safety_min": sum(caused.values()),
        "protected_bad": protected_bad + len(ctl.plan_violations),
        "max_off_h": round(max(max_off.values()) / 60, 2),
        "writes_day": max(writes.values()),
        "deficit_extra": round(extra_def, 2),
        "cost_ctl": round(cost["ctl"], 2),
        "cost_base": round(cost["base"], 2),
        "cost_ref": round(cost["ref"], 2),
        "realised": round(realised, 2),
        "claimed": round(ctl.claimed, 2),
        "top3": top3,
        "day_peaks": {d.isoformat(): round(v, 2) for m in months.values() for d, v in sorted(m.items())},
        "norgespris_shift": ctl.price_hours_applied,
        "replan_ms_max": round(max(ctl.replan_ms, default=0.0), 1),
        "zones": {z: {"min": round(min_temp[z], 2), "below": safety_minutes[z], "ref_below": ref_below[z], "caused": caused[z],
                      "dDef": round((deficit[z]["ctl"] - deficit[z]["base"]) / days, 2),
                      "first_below": first_below.get(z)} for z in zones},
        "replan_ms_p95": round(statistics.quantiles(ctl.replan_ms, n=20)[-1], 1) if len(ctl.replan_ms) >= 20 else None,
    }
    # ---------------------------------------------------------------- invariants
    if kpi["below_safety_min"] > 0:
        violations.append(f"room below safety-0.3 for {kpi['below_safety_min']} min after being lowered (reference house was not)")
    if kpi["protected_bad"]:
        violations.append(f"protected window lowered {kpi['protected_bad']}× {ctl.plan_violations[:2]}")
    max_allowed = max(ctl.zone(z, sc.start).max_off_hours for z in zones)
    if kpi["max_off_h"] > max_allowed + 0.1:
        violations.append(f"off for {kpi['max_off_h']} h > max_off_hours {max_allowed}")
    if kpi["writes_day"] > 30:
        violations.append(f"{kpi['writes_day']} thermostat writes in a day")
    if sc.aggressiveness <= 1.0 and kpi["deficit_extra"] > (1.5 if sc.optimize else 3.0):
        violations.append(f"comfort deficit +{kpi['deficit_extra']} degree-hours/day vs baseline")
    if realised < -0.01 and ctl.claimed > 0.01:
        violations.append(f"claims {ctl.claimed:.2f} kr saved while realised cost is {-realised:.2f} kr higher")
    if sc.contract == "fixed" and ctl.price_hours_applied:
        violations.append(f"price-driven shifting under Norgespris in {ctl.price_hours_applied} zone-hours")
    # shared CI runners are 2–4× slower and noisy; the budget is for a home server
    budget = 200 * (len(zones) / 4) * (3 if os.environ.get("CI") else 1)
    if kpi["replan_ms_max"] > budget:
        violations.append(f"replan took {kpi['replan_ms_max']} ms")
    return Result(sc.name, days, kpi, violations, trace, true_hourly)
