"""What actually happened, hour by hour: the card's look-back view.

A rolling log (7 days, kept in its own Store so it survives restarts) with, per
zone and clock hour, the target and mode that were applied, why, the price, the
predicted and the *measured* room temperature, how much of the hour the heater
ran, flags (manual change, paused, capacity shedding, off) and an energy
estimate; per home the whole-house kWh and the capacity cap.

Live samples come from the coordinator after every update. After a first start
or a gap (Home Assistant was down) the last 48 hours are rebuilt from the
recorder, so the view has something to show at once.

Hours are keyed by :func:`planner.hour_key` – a fixed UTC offset – so the two
02:00 hours of the autumn DST change are two separate rows.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from functools import partial
import logging
import math
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_CLIMATE, CONF_MODE, CONF_NAME, CONF_NUMBER, CONF_TEMP_SENSOR, CONF_ZONE_ID, DOMAIN
from .planner import MODE_PERCENT, classify_prices, hour_key, hours_from

if TYPE_CHECKING:
    from .coordinator import HeatPlannerCoordinator

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
LOG_HOURS = 168  # 7 days
BACKFILL_HOURS = 48
SAVE_DELAY = 120  # seconds; flushed on shutdown too
MIN_COVER = timedelta(minutes=10)  # recorder hours with less data than this stay empty

FLAG_MANUAL = "manual"  # someone changed the thermostat by hand this hour
FLAG_PAUSED = "paused"  # the plan was paused after a manual change
FLAG_DISABLED = "disabled"  # automatic control switched off
FLAG_SHED = "shed"  # set back by the capacity guard
FLAG_OFF = "off"  # the heater was switched off
FLAG_SAFETY = "safety"  # held above the safety temperature

SRC_LIVE = "live"
SRC_RECORDER = "recorder"


def _float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def room_value(value: Any) -> float | None:
    """A room temperature, or None: ≤ 1 °C is a thermostat without a sensor (reports 0)."""
    f = _float(value)
    return f if f is not None and 1 < f < 60 else None


def hour_id(when: datetime) -> str:
    return hour_key(when).isoformat()


def _parse(key: str) -> datetime | None:
    try:
        return datetime.fromisoformat(key)
    except (TypeError, ValueError):
        return None


def window(now: datetime, hours: int) -> list[datetime]:
    """The ``hours`` completed hours before ``now`` plus the current one (real hours, DST-safe)."""
    tz = now.tzinfo if isinstance(now.tzinfo, ZoneInfo) else dt_util.get_default_time_zone()
    start = (dt_util.as_utc(now) - timedelta(hours=hours)).astimezone(tz)
    return hours_from(start, hours + 1, tz=tz)


# --------------------------------------------------------------------------- recorder helpers


def step_series(states: Iterable[Any], value: Callable[[Any], Any]) -> list[tuple[datetime, Any]]:
    """(time, value) points of a step function from recorder states (value None = unknown)."""
    out: list[tuple[datetime, Any]] = []
    for st in states:
        if not hasattr(st, "state"):
            continue
        when = getattr(st, "last_updated", None) or getattr(st, "last_changed", None)
        if when is None:
            continue
        v = None if st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) else value(st)
        out.append((dt_util.as_utc(when), v))
    out.sort(key=lambda p: p[0])
    return out


def _segments(series: list[tuple[datetime, Any]], start: datetime, end: datetime) -> list[tuple[float, Any]]:
    """(seconds, value) pieces of the step function inside [start, end)."""
    start, end = dt_util.as_utc(start), dt_util.as_utc(end)
    out: list[tuple[float, Any]] = []
    for i, (t, v) in enumerate(series):
        t_next = series[i + 1][0] if i + 1 < len(series) else end
        a, b = max(t, start), min(t_next, end)
        if b > a:
            out.append(((b - a).total_seconds(), v))
    return out


def weighted_mean(series: list[tuple[datetime, Any]], start: datetime, end: datetime) -> float | None:
    """Time-weighted mean over [start, end) of the known (numeric) values."""
    total = weight = 0.0
    for secs, v in _segments(series, start, end):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            total += secs * float(v)
            weight += secs
    return total / weight if weight >= MIN_COVER.total_seconds() else None


def last_value(series: list[tuple[datetime, Any]], before: datetime) -> Any:
    """The value in effect just before ``before`` (the last state of the hour)."""
    before = dt_util.as_utc(before)
    val = None
    for t, v in series:
        if t >= before:
            break
        val = v
    return val


def values_in(series: list[tuple[datetime, Any]], start: datetime, end: datetime) -> list[Any]:
    """Every value in effect at some point in [start, end)."""
    return [v for _, v in _segments(series, start, end)]


# --------------------------------------------------------------------------- the log


class HistoryLog:
    """Per-zone, per-hour records of what happened (see module docstring)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.history")
        self.zones: dict[str, dict[str, dict[str, Any]]] = {}
        self.home: dict[str, dict[str, Any]] = {}
        self._paused: dict[str, bool] = {}
        self._backfill_started = False

    # ---------------------------------------------------------------- storage

    async def async_load(self) -> None:
        try:
            data = await self.store.async_load()
        except Exception:
            _LOGGER.warning("Could not read the Heat Planner history, starting a new one", exc_info=True)
            data = None
        if data:
            self.zones = {z: dict(rows) for z, rows in (data.get("zones") or {}).items()}
            self.home = dict(data.get("home") or {})
        self.prune(dt_util.now())

    def _data(self) -> dict[str, Any]:
        return {"zones": self.zones, "home": self.home}

    def schedule_save(self) -> None:
        self.store.async_delay_save(self._data, SAVE_DELAY)

    async def async_save(self) -> None:
        await self.store.async_save(self._data())

    async def async_remove(self) -> None:
        await self.store.async_remove()

    def prune(self, now: datetime, keep: Iterable[str] | None = None) -> None:
        """Drop hours older than :data:`LOG_HOURS` (and zones that no longer exist)."""
        cutoff = dt_util.as_utc(now) - timedelta(hours=LOG_HOURS + 1)
        if keep is not None:
            wanted = set(keep)
            for zid in [z for z in self.zones if z not in wanted]:
                self.zones.pop(zid)
        for rows in (*self.zones.values(), self.home):
            for k in [k for k in rows if (t := _parse(k)) is None or dt_util.as_utc(t) < cutoff]:
                rows.pop(k)

    # ---------------------------------------------------------------- recording

    def record_zone(
        self,
        zone_id: str,
        when: datetime,
        *,
        target: float | None,
        mode: str | None,
        reasons: list[str] | None = None,
        price: float | None = None,
        level: str | None = None,
        predicted: float | None = None,
        temp: float | None = None,
        heating: float | None = None,
        flags: Iterable[str] = (),
        kwh: float | None = None,
        kw: float | None = None,
    ) -> dict[str, Any]:
        """Add one live sample to the zone's current hour.

        Target, mode and reasons: the last one of the hour wins. The prediction is
        the one made at the start of the hour. Temperature and heating share are
        means over the hour's samples; flags accumulate.
        """
        rows = self.zones.setdefault(zone_id, {})
        rec = rows.setdefault(hour_id(when), {})
        if rec.get("src") == SRC_RECORDER:
            rec.clear()  # a live hour replaces a reconstructed one
        rec["src"] = SRC_LIVE
        rec["target"] = None if target is None else round(float(target), 2)
        rec["mode"] = mode
        rec["reasons"] = list(reasons or [])
        if price is not None:
            rec["price"] = round(float(price), 4)
        if level is not None:
            rec["level"] = level
        if "predicted" not in rec and predicted is not None:
            rec["predicted"] = round(float(predicted), 2)
        t = room_value(temp)
        if t is not None:
            n = int(rec.get("temp_n") or 0)
            rec["temp"] = round(((rec.get("temp") or 0.0) * n + t) / (n + 1), 3)
            rec["temp_n"] = n + 1
        if heating is not None:
            n = int(rec.get("heat_n") or 0)
            rec["heating"] = round(((rec.get("heating") or 0.0) * n + float(heating)) / (n + 1), 3)
            rec["heat_n"] = n + 1
        rec["flags"] = sorted(set(rec.get("flags") or []) | set(flags))
        if kwh is not None:
            rec["kwh"] = round(float(kwh), 3)
        if kw is not None:
            rec["kw"] = round(float(kw), 3)
        return rec

    def record_home(
        self, when: datetime, *, house_kwh: float | None = None, cap_kw: float | None = None, live: bool = False
    ) -> None:
        rec = self.home.setdefault(hour_id(when), {})
        if house_kwh is not None and (live or not rec.get("final")):
            rec["house_kwh"] = round(float(house_kwh), 3)
            rec["final"] = not live
        if cap_kw is not None:
            rec["cap_kw"] = round(float(cap_kw), 2)

    def was_paused(self, zone_id: str, now: datetime) -> bool:
        if zone_id in self._paused:
            return self._paused[zone_id]
        rows = self.zones.get(zone_id, {})
        for h in (hour_key(now), hour_key(dt_util.as_local(dt_util.as_utc(now) - timedelta(hours=1)))):
            rec = rows.get(h.isoformat())
            if rec is not None:
                return FLAG_PAUSED in (rec.get("flags") or [])
        return False

    def after_update(self, coord: HeatPlannerCoordinator) -> None:
        """Coordinator hook: sample now; the first time, rebuild missing hours from the recorder."""
        try:
            self.sample(coord)
        except Exception:
            _LOGGER.exception("Could not record the look-back history")
        if not self._backfill_started:
            self._backfill_started = True
            coord.config_entry.async_create_background_task(
                self.hass, self._safe_backfill(coord), f"{DOMAIN}_history_backfill"
            )

    async def _safe_backfill(self, coord: HeatPlannerCoordinator) -> None:
        try:
            await self.async_backfill(coord)
        except Exception:
            _LOGGER.exception("Could not rebuild the look-back history from the recorder")

    def sample(self, coord: HeatPlannerCoordinator, now: datetime | None = None) -> None:
        """Record what the coordinator just did (called after every update)."""
        now = now or dt_util.now()
        hour = hour_key(now)
        for cfg in coord.zone_configs:
            zid = cfg[CONF_ZONE_ID]
            try:
                self._sample_zone(coord, cfg, hour, now)
            except Exception:
                _LOGGER.debug("History sample failed for %s", zid, exc_info=True)
        cap = coord.peak_state.cap_kw if coord.peak_state is not None else None
        self.record_home(hour, house_kwh=coord.projected_hour_kwh(), cap_kw=cap, live=True)
        self.fill_house(coord._house_kwh, now)
        self.prune(now, keep=[z[CONF_ZONE_ID] for z in coord.zone_configs])
        self.schedule_save()

    def fill_house(self, hourly: dict[datetime, float], now: datetime) -> None:
        """Completed hours from the meter's history (statistics) replace the live estimates."""
        cutoff = dt_util.as_utc(now) - timedelta(hours=LOG_HOURS)
        current = hour_key(now)
        for when, kwh in hourly.items():
            if dt_util.as_utc(when) >= cutoff and hour_key(when) != current:
                rec = self.home.setdefault(hour_id(when), {})
                rec["house_kwh"] = round(float(kwh), 3)
                rec["final"] = True

    def _sample_zone(self, coord: HeatPlannerCoordinator, cfg: dict[str, Any], hour: datetime, now: datetime) -> None:
        zid = cfg[CONF_ZONE_ID]
        percent = cfg.get(CONF_MODE) == MODE_PERCENT
        plan = coord.plans.get(zid)
        h0 = plan.hours[0] if plan and plan.hours and plan.hours[0].start == hour else None
        st = coord.zone_state(zid)
        available, dev_off, current, _ = coord._device(cfg)
        flags: set[str] = set()
        paused = coord.manual_until(zid) is not None
        device_value = (0.0 if percent else None) if dev_off else current
        target: float | None
        if not st.get("auto", True):
            flags.add(FLAG_DISABLED)
            mode: str | None = "disabled"
            target = device_value if available else None
        elif paused:
            flags.add(FLAG_PAUSED)
            mode = "manual"
            target = device_value if available else None
        else:
            mode = h0.mode if h0 else None
            applied = coord._applied.get(zid)
            if applied and applied.get("hour") == hour and "value" in applied:
                target = applied["value"]
            else:
                target = h0.target if h0 else None
        if paused and not self.was_paused(zid, now):
            flags.add(FLAG_MANUAL)
        self._paused[zid] = paused
        if zid in coord._shed:
            flags.add(FLAG_SHED)
        if coord._safety.get(zid):
            flags.add(FLAG_SAFETY)
        if available and dev_off:
            flags.add(FLAG_OFF)
        heating = None
        climate = cfg.get(CONF_CLIMATE)
        cst = coord.hass.states.get(climate) if climate else None
        if cst is not None and cst.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            action = cst.attributes.get("hvac_action")
            if action is not None:
                heating = 1.0 if str(action) == "heating" else 0.0
        kw = coord.heater_kw(zid)
        if available and dev_off:
            kwh: float | None = 0.0
        else:
            kwh = h0.kwh if h0 else None
        price = h0.price if h0 else coord.price_hours.get(hour)
        self.record_zone(
            zid, hour, target=target, mode=mode, reasons=list(h0.reasons) if h0 else [],
            price=price, level=h0.level if h0 else None, predicted=h0.predicted if h0 else None,
            temp=self.room_temp(coord, cfg), heating=heating, flags=flags, kwh=kwh, kw=kw,
        )

    @staticmethod
    def room_temp(coord: HeatPlannerCoordinator, cfg: dict[str, Any]) -> float | None:
        """The room sensor, else the thermostat's own reading – ignoring ≤ 1 °C."""
        for entity_id, attr in ((cfg.get(CONF_TEMP_SENSOR), None), (cfg.get(CONF_CLIMATE), "current_temperature")):
            st = coord.hass.states.get(entity_id) if entity_id else None
            if st is None:
                continue
            val = room_value(st.attributes.get(attr) if attr else st.state)
            if val is not None:
                return val
        return None

    # ---------------------------------------------------------------- backfill

    def missing_hours(self, zone_ids: Iterable[str], now: datetime, hours: int = BACKFILL_HOURS) -> list[datetime]:
        """Completed hours in the last ``hours`` that some zone has no record for."""
        ids = list(zone_ids)
        return [
            h for h in window(now, hours)[:-1]
            if any(h.isoformat() not in self.zones.get(z, {}) for z in ids)
        ]

    async def async_backfill(self, coord: HeatPlannerCoordinator, now: datetime | None = None) -> int:
        """Rebuild missing hours of the last 48 h from the recorder. Returns hours filled."""
        if "recorder" not in self.hass.config.components:
            return 0
        now = now or dt_util.now()
        zone_ids = [z[CONF_ZONE_ID] for z in coord.zone_configs]
        missing = self.missing_hours(zone_ids, now)
        if not missing:
            return 0
        try:
            from homeassistant.components.recorder import get_instance, history
        except ImportError:
            return 0
        reg = er.async_get(self.hass)
        eid = self.entry_id

        def own(unique: str) -> str | None:
            return reg.async_get_entity_id("sensor", DOMAIN, f"{eid}_{unique}")

        ids_by_zone: dict[str, dict[str, str]] = {}
        for cfg in coord.zone_configs:
            zid = cfg[CONF_ZONE_ID]
            ids = {
                "climate": cfg.get(CONF_CLIMATE), "sensor": cfg.get(CONF_TEMP_SENSOR), "number": cfg.get(CONF_NUMBER),
                "target": own(f"{zid}_target"), "mode": own(f"{zid}_mode"), "predicted": own(f"{zid}_predicted"),
            }
            ids_by_zone[zid] = {k: v for k, v in ids.items() if v}
        home = {k: v for k, v in {
            "price": own("home_effective_price"), "level": own("home_price_level"),
            "step": own("home_capacity_step"), "headroom": own("home_peak_headroom"),
        }.items() if v}
        entity_ids = sorted({e for ids in ids_by_zone.values() for e in ids.values()} | set(home.values()))
        if not entity_ids:
            return 0
        start = dt_util.as_utc(missing[0]) - timedelta(hours=1)  # the predicted sensor looks one hour back
        end = dt_util.as_utc(hour_key(now))
        try:
            hist = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.get_significant_states, self.hass, start, end, entity_ids,
                    include_start_time_state=True, significant_changes_only=False,
                    minimal_response=False, no_attributes=False,
                )
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("No recorder history for the look-back view: %s", err)
            return 0
        try:
            await coord._house_history()
        except Exception:
            _LOGGER.debug("No house energy history", exc_info=True)
        levels = classify_prices(coord.price_hours, coord.rules()) if coord.price_hours else {}
        filled = 0
        home_series = {k: hist.get(v, []) for k, v in home.items()}
        for cfg in coord.zone_configs:
            zid = cfg[CONF_ZONE_ID]
            rows = rows_from_history(
                {k: hist.get(v, []) for k, v in ids_by_zone[zid].items()}, home_series,
                [h for h in missing if h.isoformat() not in self.zones.get(zid, {})],
                percent=cfg.get(CONF_MODE) == MODE_PERCENT, heater_kw=coord.heater_kw(zid), zone_id=zid,
            )
            for key, rec in rows.items():
                hour = _parse(key)
                if hour is not None and rec.get("price") is None and hour in coord.price_hours:
                    rec["price"] = round(coord.price_hours[hour], 4)
                    rec["level"] = rec.get("level") or levels.get(hour)
                self.zones.setdefault(zid, {}).setdefault(key, rec)
                filled += 1
        cap_series = step_series(home_series.get("step", []), lambda s: _float(s.attributes.get("cap_kw_today")))
        for h in missing:
            cap = last_value(cap_series, h + timedelta(hours=1))
            if cap is not None:
                self.record_home(h, cap_kw=cap)
        self.fill_house(coord._house_kwh, now)
        if filled:
            _LOGGER.debug("Look-back view: rebuilt %s zone-hours from the recorder", filled)
            self.schedule_save()
        return filled

    # ---------------------------------------------------------------- export

    def export(self, coord: HeatPlannerCoordinator, hours: int, now: datetime | None = None) -> dict[str, Any]:
        """The websocket answer: ``hours`` completed hours plus the current one, oldest first."""
        now = now or dt_util.now()
        span = window(now, hours)
        current = hour_key(now)
        zones = []
        for cfg in coord.zone_configs:
            zid = cfg[CONF_ZONE_ID]
            rows = self.zones.get(zid, {})
            out = []
            for h in span:
                rec = rows.get(h.isoformat()) or {}
                out.append(export_row(h, rec, partial_hour=h == current))
            percent = cfg.get(CONF_MODE) == MODE_PERCENT
            zones.append({
                "zone_id": zid, "name": cfg.get(CONF_NAME, zid), "mode": cfg.get(CONF_MODE),
                "has_temperature": bool(cfg.get(CONF_TEMP_SENSOR)) or not percent or any(r["temp"] is not None for r in out),
                "heater_kw": coord.heater_kw(zid),
                "hours": out,
            })
        home = []
        for h in span:
            rec = self.home.get(h.isoformat()) or {}
            home.append({
                "start": h.isoformat(), "house_kwh": rec.get("house_kwh"), "cap_kw": rec.get("cap_kw"),
                "live": h == current or not rec.get("final", True),
            })
        return {
            "entry_id": coord.config_entry.entry_id,
            "hours": hours,
            "now": now.isoformat(),
            "start": span[0].isoformat(),
            "end": (span[-1] + timedelta(hours=1)).isoformat(),
            "currency": coord.hass.config.currency,
            "price_unit": coord.prices.unit,
            "metered": coord.peak_state is not None,
            "zones": zones,
            "home": home,
        }


def export_row(hour: datetime, rec: dict[str, Any], *, partial_hour: bool = False) -> dict[str, Any]:
    heating = rec.get("heating")
    kw = rec.get("kw")
    kwh = rec.get("kwh")
    if heating is not None and kw:
        kwh = round(heating * kw, 3)  # measured run time beats the plan's estimate
    price = rec.get("price")
    return {
        "start": hour.isoformat(),
        "target": rec.get("target"),
        "mode": rec.get("mode"),
        "reasons": list(rec.get("reasons") or []),
        "price": price,
        "level": rec.get("level"),
        "predicted": rec.get("predicted"),
        "temp": None if rec.get("temp") is None else round(rec["temp"], 2),
        "heating": heating,
        "flags": list(rec.get("flags") or []),
        "kwh": kwh,
        "cost": round(kwh * price, 4) if kwh is not None and price is not None else None,
        "src": rec.get("src"),
        "partial": partial_hour,
    }


def rows_from_history(
    zone: dict[str, list[Any]],
    home: dict[str, list[Any]],
    hours: list[datetime],
    *,
    percent: bool,
    heater_kw: float,
    zone_id: str,
) -> dict[str, dict[str, Any]]:
    """Hourly records rebuilt from recorder states (pure; see :meth:`HistoryLog.async_backfill`).

    ``zone`` maps roles (climate, sensor, number, target, mode, predicted) to state lists,
    ``home`` (price, level, headroom) likewise.
    """
    climate = zone.get("climate", [])
    temp = step_series(zone.get("sensor", []), lambda s: room_value(s.state)) if zone.get("sensor") else []
    if not any(v is not None for _, v in temp):
        temp = step_series(climate, lambda s: room_value(s.attributes.get("current_temperature")))
    setpoint = step_series(
        climate, lambda s: ("off", None) if s.state == "off" else ("on", _float(s.attributes.get("temperature")))
    )
    action = step_series(climate, lambda s: s.attributes.get("hvac_action"))
    number = step_series(zone.get("number", []), lambda s: _float(s.state))
    target_s = step_series(zone.get("target", []), lambda s: _float(s.state))
    reasons_s = step_series(zone.get("target", []), lambda s: s.attributes.get("reasons"))
    mode_s = step_series(zone.get("mode", []), lambda s: s.state)
    mode_reasons = step_series(zone.get("mode", []), lambda s: s.attributes.get("reasons"))
    pred_s = step_series(zone.get("predicted", []), lambda s: _float(s.state))
    price_s = step_series(home.get("price", []), lambda s: _float(s.state))
    level_s = step_series(home.get("level", []), lambda s: s.state)
    shed_s = step_series(home.get("headroom", []), lambda s: s.attributes.get("shed") or [])
    has_action = any(v is not None for _, v in action)

    out: dict[str, dict[str, Any]] = {}
    for h in hours:
        end = h + timedelta(hours=1)
        rec: dict[str, Any] = {"src": SRC_RECORDER}
        t = weighted_mean(temp, h, end)
        if t is not None:
            rec["temp"] = round(t, 3)
        if has_action:
            share = weighted_mean(
                [(w, None if v is None else (1.0 if v == "heating" else 0.0)) for w, v in action], h, end
            )
            if share is not None:
                rec["heating"] = round(share, 3)
        modes = [m for m in values_in(mode_s, h, end) if m]
        mode = last_value(mode_s, end)
        flags: set[str] = set()
        if "manual" in modes:
            flags.add(FLAG_PAUSED)
            if last_value(mode_s, h) != "manual":
                flags.add(FLAG_MANUAL)
        if "disabled" in modes:
            flags.add(FLAG_DISABLED)
        # the device itself: what was really set
        if percent:
            target = last_value(number, end)
            if target is None:
                target = last_value(target_s, end)
            if any(v is not None and v <= 0 for v in values_in(number, h, end)):
                flags.add(FLAG_OFF)
        else:
            sp = last_value(setpoint, end)
            target = sp[1] if sp and sp[0] == "on" else None
            if sp is None:
                target = last_value(target_s, end)
            if any(v and v[0] == "off" for v in values_in(setpoint, h, end)):
                flags.add(FLAG_OFF)
        if "off" in modes:
            flags.add(FLAG_OFF)
        if any(zone_id in (v or []) for v in values_in(shed_s, h, end)):
            flags.add(FLAG_SHED)
        reasons = last_value(reasons_s, end) or last_value(mode_reasons, end) or []
        pred = last_value(pred_s, h + timedelta(minutes=1))
        price = last_value(price_s, end)
        level = last_value(level_s, end)
        if rec.get("heating") is not None:
            kwh: float | None = rec["heating"] * heater_kw
        elif percent and target is not None:
            kwh = max(0.0, min(100.0, target)) / 100 * heater_kw
        else:
            kwh = None
        has_any = (
            t is not None or target is not None or mode is not None or rec.get("heating") is not None
        )
        if not has_any:
            continue
        rec.update({
            "target": None if target is None else round(float(target), 2),
            "mode": mode if mode not in (STATE_UNAVAILABLE, STATE_UNKNOWN) else None,
            "reasons": list(reasons) if isinstance(reasons, (list, tuple)) else [],
            "flags": sorted(flags),
            "kw": round(heater_kw, 3),
        })
        if kwh is not None:
            rec["kwh"] = round(kwh, 3)
        if pred is not None:
            rec["predicted"] = round(pred, 2)
        if price is not None:
            rec["price"] = round(price, 4)
        if level not in (None, STATE_UNAVAILABLE, STATE_UNKNOWN):
            rec["level"] = level
        out[h.isoformat()] = rec
    return out
