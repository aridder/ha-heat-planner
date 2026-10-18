"""Coordinator: prices, presence, planning, control and learning."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta
from functools import partial
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE,
    CONF_COMFORT,
    CONF_HEATER_KW,
    CONF_HOUSE,
    CONF_POWER_SENSOR,
    CONF_USE_HISTORY,
    HISTORY_DAYS,
    CONF_MODE,
    CONF_NAME,
    CONF_NUMBER,
    CONF_PRESENCE,
    CONF_PRICE_EXTRA,
    CONF_TEMP_SENSOR,
    CONF_WEATHER,
    CONF_ZONE_ID,
    CONF_ZONES,
    DOMAIN,
    PLAN_HOURS,
    STORAGE_VERSION,
    TUNABLE_RULES,
    TUNABLE_ZONE,
    UPDATE_INTERVAL,
)
from .energy import (
    async_house_hourly_kwh,
    apply_cuts,
    base_load,
        house_meter,
    limit,
    peak_from_options,
    plan_caps,
    tariff_from_options,
)
from .learning import samples_from_history
from .feedback import apply_feedback, changed_keys, shift_schedule
from .occupancy import AUTO_HOUR, ArrivalModel, expected_arrival, learn_arrivals, summary as arrival_summary, warm_from_hour
from .peak import realtime_shed
from .tariff import describe as describe_tariff, effective_prices, fixed_prices
from .solar import forecast_solar
from .planner import (
    explain_savings,
    model_quality,
    MODE_PERCENT,
    MODE_SETPOINT,
    Plan,
    PriceRules,
    ThermalModel,
    ZoneConfig,
    is_protected,
    build_plan,
    classify_prices,
    default_schedule,
    fit_thermal,
    hourly_prices,
    scheduled_value,
)
from .discovery import default_heater_kw, thermal_prior
from .prices import PriceSource

_LOGGER = logging.getLogger(__name__)

MANUAL_GRACE = timedelta(minutes=3)


def _float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


class HeatPlannerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Owns the plan for every zone of one config entry."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=UPDATE_INTERVAL,
        )
        self.store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.prices = PriceSource(hass, {**entry.data, **entry.options})
        self.state: dict[str, Any] = {"zones": {}, "rules": {}}
        self.plans: dict[str, Plan] = {}
        self.price_hours: dict[datetime, float] = {}
        self.away = False
        self.outdoor: dict[datetime, float] = {}
        self.solar: dict[datetime, float] = {}
        self.spot_hours: dict[datetime, float] = {}
        self.peak_state = None
        self._house_kwh: dict[datetime, float] = {}
        self._house_fetched: datetime | None = None
        self._shed: dict[str, datetime] = {}
        self._meter_last: tuple[datetime, float] | None = None
        self._meter_hour: datetime | None = None
        self._hour_kwh = 0.0
        self._guard_at: datetime | None = None
        self._outdoor_fetched: datetime | None = None
        self._applied: dict[str, dict[str, Any]] = {}
        self._safety: dict[str, bool] = {}
        self._unsubs: list = []

    # ------------------------------------------------------------------ config

    @property
    def options(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    @property
    def zone_configs(self) -> list[dict[str, Any]]:
        return list(self.options.get(CONF_ZONES, []))

    def zone_state(self, zone_id: str) -> dict[str, Any]:
        zones = self.state.setdefault("zones", {})
        if zone_id not in zones:
            cfg = next((z for z in self.zone_configs if z[CONF_ZONE_ID] == zone_id), {})
            mode = cfg.get(CONF_MODE, MODE_SETPOINT)
            # an accepted AI proposal seeds the schedule and settings of new zones
            seed = (self.options.get("seeds") or {}).get(zone_id, {})
            schedule = {str(k): v for k, v in default_schedule(mode).items()}
            schedule.update({str(k): list(v) for k, v in (seed.get("schedule") or {}).items()})
            zones[zone_id] = {
                "schedule": schedule,
                "overrides": {},
                "settings": dict(seed.get("settings") or {}),
                "thermal": asdict(ThermalModel()),
                "auto": True,
                "mode": mode,
                "seed_version": int(self.options.get("seed_version", 0)),
            }
        return zones[zone_id]

    def zone(self, zone_id: str) -> ZoneConfig:
        cfg = next(z for z in self.zone_configs if z[CONF_ZONE_ID] == zone_id)
        st = self.zone_state(zone_id)
        mode = cfg.get(CONF_MODE, MODE_SETPOINT)
        base: dict[str, Any] = {}
        if mode == MODE_PERCENT:
            base = dict(
                comfort=0.0, min_value=0.0, max_value=100.0, away_offset=-20.0,
                price_shift=15.0, preheat=10.0,
            )
        else:
            base = dict(comfort=float(cfg.get(CONF_COMFORT, 21.0)))
        base["heater_kw"] = self.heater_kw(zone_id)
        settings = {k: v for k, v in st.get("settings", {}).items() if k in TUNABLE_ZONE}
        stored = ThermalModel(**{
            k: v for k, v in st.get("thermal", {}).items()
            if k in {f.name for f in fields(ThermalModel)}
        })
        # until something is learned, start from what the house description suggests
        thermal = stored if stored.learned else thermal_prior(self.options.get(CONF_HOUSE))
        zc = ZoneConfig(
            zone_id=zone_id,
            name=cfg.get(CONF_NAME, zone_id),
            mode=mode,
            thermal=thermal,
            **{**base, **settings},
        )
        if zc.away_until_hour == AUTO_HOUR:
            # warm again in time for when the household usually gets home
            zc = replace(zc, away_until_hour=warm_from_hour(
                expected_arrival(self.arrivals, dt_util.now().date()), self.lead_hours(zc), default=13
            ))
        return zc

    @property
    def arrivals(self) -> ArrivalModel | None:
        data = self.state.get("arrivals")
        return ArrivalModel.from_dict(data) if data else None

    @staticmethod
    def lead_hours(zone: ZoneConfig) -> float:
        """Roughly how long the room needs to recover from the away setback."""
        if zone.is_percent:
            return 2.0
        net = max(zone.thermal.gross_rate - 0.3, 0.2)
        return max(0.5, min(6.0, abs(zone.away_offset) / net + zone.thermal.lag_hours))

    def heater_kw(self, zone_id: str) -> float:
        """Configured power, else measured, else a guess from the house description."""
        cfg = next((z for z in self.zone_configs if z[CONF_ZONE_ID] == zone_id), {})
        kw = _float(cfg.get(CONF_HEATER_KW)) or 0.0
        if kw > 0:
            return kw
        learned = _float(self.zone_state(zone_id).get("learned_kw"))
        if learned:
            return learned
        return default_heater_kw(self.options.get(CONF_HOUSE))

    def rules(self) -> PriceRules:
        r = {k: v for k, v in self.state.get("rules", {}).items() if k in TUNABLE_RULES}
        return PriceRules(**r)

    def schedule(self, zone_id: str) -> dict[int, list[float]]:
        return {int(k): v for k, v in self.zone_state(zone_id)["schedule"].items()}

    def overrides(self, zone_id: str) -> dict[str, list[float]]:
        today = dt_util.now().date().isoformat()
        ov = self.zone_state(zone_id)["overrides"]
        for d in [d for d in ov if d < today]:
            ov.pop(d)
        return ov

    # ------------------------------------------------------------------ lifecycle

    async def async_setup(self) -> None:
        stored = await self.store.async_load()
        if stored:
            self.state = stored
        zones = self.state.setdefault("zones", {})
        for cfg in self.zone_configs:
            zid = cfg[CONF_ZONE_ID]
            mode = cfg.get(CONF_MODE, MODE_SETPOINT)
            if zid in zones and zones[zid].get("mode", mode) != mode:
                # schedules mean different things per mode (offsets vs percent)
                _LOGGER.info("Zone %s changed control type, resetting its schedule", zid)
                zones.pop(zid)
            seed_version = int(self.options.get("seed_version", 0))
            if (
                zid in zones
                and zid in (self.options.get("seeds") or {})
                and zones[zid].get("seed_version", 0) < seed_version
            ):
                # a new AI proposal was accepted: start this zone from it, keep what was learned
                thermal = zones[zid].get("thermal")
                learned_kw = zones[zid].get("learned_kw")
                zones.pop(zid)
                fresh = self.zone_state(zid)
                fresh["thermal"] = thermal or fresh["thermal"]
                if learned_kw:
                    fresh["learned_kw"] = learned_kw
            self.zone_state(zid)["mode"] = mode
        await self._save()

        watch = list(self.options.get(CONF_PRESENCE, []))
        if watch:
            self._unsubs.append(
                async_track_state_change_event(self.hass, watch, self._presence_changed)
            )
        meter = house_meter(self.options)
        if meter and peak_from_options(self.options):
            self._unsubs.append(
                async_track_state_change_event(self.hass, [meter], self._meter_changed)
            )
        # learn the thermal model every night
        self._unsubs.append(
            async_track_time_change(self.hass, self._nightly, hour=3, minute=17, second=0)
        )
        # apply exactly on the hour too (prices change on the hour)
        self._unsubs.append(
            async_track_time_change(self.hass, self._on_hour, minute=0, second=5)
        )
        self.hass.async_create_background_task(self.async_learn(), f"{DOMAIN}_learn")

    async def async_shutdown(self) -> None:
        for zone_id in list(self._applied):
            await self._restore(zone_id)
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await super().async_shutdown()

    async def _save(self) -> None:
        await self.store.async_save(self.state)

    @callback
    def _presence_changed(self, event: Event) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _meter_changed(self, event: Event) -> None:
        now = dt_util.now()
        if self._guard_at and now - self._guard_at < timedelta(seconds=60):
            kw = self._meter_kw()
            if kw is not None:
                self._track_energy(kw, now)
            return
        self._guard_at = now
        before = set(self._shed)
        self._realtime_guard()
        if set(self._shed) != before:
            _LOGGER.info("Capacity guard: setting back %s", sorted(self._shed) or "nothing")
            for zid in before ^ set(self._shed):
                self._applied.pop(zid, None)
            self.hass.async_create_task(self._reapply())

    async def _reapply(self) -> None:
        for zid, plan in self.plans.items():
            await self._apply(zid, plan)
        if self.peak_state is not None:
            self.peak_state.shed = sorted(self._shed)
        self.async_update_listeners()

    async def _on_hour(self, now: datetime) -> None:
        await self.async_request_refresh()

    async def _nightly(self, now: datetime) -> None:
        await self.async_learn()

    # ------------------------------------------------------------------ inputs

    def _is_away(self) -> bool:
        ids = list(self.options.get(CONF_PRESENCE, []))
        if not ids:
            return False
        known = 0
        for entity_id in ids:
            st = self.hass.states.get(entity_id)
            if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue  # a person without a working tracker says nothing
            known += 1
            if entity_id.startswith("zone."):
                if (_float(st.state) or 0) > 0:
                    return False
            elif st.state in (STATE_HOME, STATE_ON):
                return False
        return known > 0  # nothing known -> assume someone is home

    def room_temperature(self, zone_id: str) -> float | None:
        cfg = next(z for z in self.zone_configs if z[CONF_ZONE_ID] == zone_id)
        sensor = cfg.get(CONF_TEMP_SENSOR)
        if sensor:
            st = self.hass.states.get(sensor)
            val = _float(st.state) if st else None
            if val is not None:
                return val
        climate = cfg.get(CONF_CLIMATE)
        if climate:
            st = self.hass.states.get(climate)
            val = _float(st.attributes.get("current_temperature")) if st else None
            # thermostats without a sensor (regulator mode) often report 0
            if val is not None and val > 1:
                return val
        return None

    async def _fetch_outdoor(self) -> None:
        weather = self.options.get(CONF_WEATHER)
        if not weather:
            return
        now = dt_util.now()
        if self._outdoor_fetched and now - self._outdoor_fetched < timedelta(hours=1):
            return
        self._outdoor_fetched = now
        try:
            resp = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("No hourly forecast from %s: %s", weather, err)
            return
        out: dict[datetime, float] = {}
        items = (resp or {}).get(weather, {}).get("forecast", [])
        try:
            self.solar = forecast_solar(items, self.hass.config.latitude, self.hass.config.longitude)
        except Exception:  # noqa: BLE001 - solar is a refinement, never fatal
            _LOGGER.debug("Could not derive solar factors", exc_info=True)
        for item in items:
            t = dt_util.parse_datetime(str(item.get("datetime")))
            temp = _float(item.get("temperature"))
            if t and temp is not None:
                out[dt_util.as_local(t).replace(minute=0, second=0, microsecond=0)] = temp
        if out:
            self.outdoor = out

    def outdoor_now(self) -> float:
        weather = self.options.get(CONF_WEATHER)
        st = self.hass.states.get(weather) if weather else None
        val = _float(st.attributes.get("temperature")) if st else None
        return val if val is not None else 5.0

    # ------------------------------------------------------------------ planning

    @property
    def optimize(self) -> bool:
        """Use the optimiser (default) or the plain price rules."""
        # new setups (with the energy step) start with the optimiser; older ones keep the rules
        return bool(self.state.get("optimize", bool(self.options.get("energy"))))

    def plan_zone(
        self,
        zone_id: str,
        *,
        zone: ZoneConfig | None = None,
        schedule: dict[int, list[float]] | None = None,
        overrides: dict[str, list[float]] | None = None,
        hours: int = PLAN_HOURS,
        start: datetime | None = None,
        adjust=None,
    ) -> Plan:
        return build_plan(
            zone or self.zone(zone_id),
            schedule if schedule is not None else self.schedule(zone_id),
            overrides if overrides is not None else self.overrides(zone_id),
            self.price_hours,
            self.rules(),
            start or dt_util.now(),
            hours,
            away_now=self.away,
            room_temp=self.room_temperature(zone_id),
            outdoor=self.outdoor,
            outdoor_default=self.outdoor_now(),
            method="optimize" if self.optimize else "rules",
            solar=self.solar or None,
            adjust=adjust,
        )

    async def _prices(self) -> None:
        """Spot prices → what the household actually pays per kWh."""
        points = await self.prices.async_get()
        self.spot_hours = hourly_prices(points)
        tariff = tariff_from_options(self.options)
        if tariff.mode == "fixed" and not self.spot_hours:
            start = dt_util.now().replace(minute=0, second=0, microsecond=0)
            self.price_hours = fixed_prices([start + timedelta(hours=i) for i in range(PLAN_HOURS)], tariff)
        else:
            self.price_hours = effective_prices(self.spot_hours, tariff)

    async def _house_history(self) -> dict[datetime, float]:
        meter = house_meter(self.options)
        if not meter:
            return {}
        now = dt_util.now()
        if self._house_fetched is None or now - self._house_fetched > timedelta(minutes=30):
            self._house_fetched = now
            self._house_kwh = await async_house_hourly_kwh(self.hass, meter)
        return self._house_kwh

    async def _limit_peaks(self) -> None:
        """Spread heating between rooms so the month's capacity step holds.

        Needs a whole-house meter. Cuts are re-planned (recovery hours included)
        and repeated a few rounds; if they do not lower the house peak they are
        dropped again, so comfort is never traded for nothing.
        """
        cfg = peak_from_options(self.options)
        if cfg is None or not self.plans:
            self.peak_state = None
            return
        hours = sorted({h.start for p in self.plans.values() for h in p.hours})
        hourly = await self._house_history()
        if not hours or not hourly:
            self.peak_state = None
            return
        caps, state = plan_caps(cfg, hourly, hours, dt_util.now())
        zones = {zid: self.zone(zid) for zid in self.plans}
        # rest of the house: low estimate of total use minus a stable estimate of heating
        heating_avg = sum(
            sum(h.kwh for h in p.hours) / max(len(p.hours), 1) for p in self.plans.values()
        )
        base = {h: max(0.0, v - heating_avg) for h, v in base_load(hourly).items()}
        priority = {z[CONF_ZONE_ID]: int(z.get("priority", 0) or 0) for z in self.zone_configs}

        def day_peaks(plans: dict[str, Plan]) -> dict[Any, float]:
            """Highest house load per day relative to that hour's cap (what the tariff counts)."""
            load: dict[datetime, float] = {}
            for p in plans.values():
                for h in p.hours:
                    load[h.start] = load.get(h.start, 0.0) + h.kwh
            out: dict[Any, float] = {}
            for h, v in load.items():
                over = v + base.get(h.hour, 0.0) - caps.get(h, 1e9)
                out[h.date()] = max(out.get(h.date(), -1e9), over)
            return out

        def replan(cuts_by_zone: dict[str, dict[datetime, float]]) -> dict[str, Plan]:
            out = dict(original)
            for zid, acc in cuts_by_zone.items():
                if not acc:
                    continue
                zone = zones[zid]
                pred = {h.start: h.predicted for h in original[zid].hours if h.predicted is not None}
                try:
                    out[zid] = self.plan_zone(
                        zid, zone=zone,
                        adjust=lambda slots, z=zone, c=dict(acc), p=pred: apply_cuts(z, slots, c, p),
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Could not apply peak limits to %s", zid)
            return out

        original = dict(self.plans)
        current = dict(self.plans)
        total: dict[str, dict[datetime, float]] = {}
        for _ in range(4):
            cuts = limit(zones, current, caps, base, priority)
            if not cuts:
                break
            for zid, zc in cuts.items():
                acc = total.setdefault(zid, {})
                for h, v in zc.items():
                    acc[h] = acc.get(h, 0.0) + v
            current = replan(total)
        if total:
            # keep cuts only on days where they actually lower that day's peak –
            # cutting hours on a day whose peak is set elsewhere costs comfort for nothing
            before, after = day_peaks(original), day_peaks(current)
            useful = {d for d in after if after[d] < before.get(d, 0.0) - 0.05 and before.get(d, 0.0) > 0}
            kept = {z: {h: v for h, v in acc.items() if h.date() in useful} for z, acc in total.items()}
            if kept != total:
                current = replan(kept) if any(kept.values()) else original
        self.plans = current
        state.shed = sorted(self._shed)
        self.peak_state = state

    def _track_energy(self, kw: float, now: datetime) -> None:
        """Integrate live whole-house power into energy for the current clock hour."""
        hour = now.replace(minute=0, second=0, microsecond=0)
        last = self._meter_last
        if self._meter_hour != hour:
            self._meter_hour = hour
            self._hour_kwh = 0.0
            if last is not None and now - last[0] < timedelta(minutes=10):
                self._hour_kwh = last[1] * (now - hour).total_seconds() / 3600
        elif last is not None:
            self._hour_kwh += last[1] * (now - last[0]).total_seconds() / 3600
        self._meter_last = (now, kw)

    def _meter_kw(self) -> float | None:
        meter = house_meter(self.options)
        st = self.hass.states.get(meter) if meter else None
        kw = _float(st.state) if st else None
        if kw is None:
            return None
        return kw / 1000 if (st.attributes.get("unit_of_measurement") or "W") == "W" else kw

    def _realtime_guard(self) -> None:
        """This hour heading over the cap → set back low-priority rooms until the hour ends."""
        state = self.peak_state
        kw = self._meter_kw()
        if state is None or state.cap_kw is None or kw is None:
            self._shed.clear()
            return
        now = dt_util.now()
        self._track_energy(kw, now)
        left = 1 - (now.minute * 60 + now.second) / 3600
        projected = self._hour_kwh + kw * left
        end_of_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        zones = []
        for zid, p in self.plans.items():
            if not p.hours:
                continue
            zone = self.zone(zid)
            draw = p.hours[0].duty * zone.heater_kw  # what lowering it actually removes
            zones.append({
                "zone_id": zid,
                "priority": int(next((z.get("priority", 0) for z in self.zone_configs if z[CONF_ZONE_ID] == zid), 0) or 0),
                "kw": draw * left,  # relief for the rest of this hour (kWh)
                "protected": is_protected(zone, p.hours[0].start) or draw < 0.05,
            })
        self._shed = {z: until for z, until in self._shed.items() if until > now}
        shed = realtime_shed(projected, state.cap_kw, zones, currently_shed=list(self._shed))
        # once shed, a room stays set back until the hour ends (no flapping)
        self._shed = {z: self._shed.get(z, end_of_hour) for z in set(shed) | set(self._shed)}

    async def _async_update_data(self) -> dict[str, Any]:
        await self._prices()
        await self._fetch_outdoor()
        self.away = self._is_away()
        self.plans = {}
        failed: set[str] = set()
        for cfg in self.zone_configs:
            zid = cfg[CONF_ZONE_ID]
            try:
                self.plans[zid] = self.plan_zone(zid)
            except Exception:  # noqa: BLE001 - one broken zone must not stop the rest
                _LOGGER.exception("Planning failed for zone %s", zid)
                failed.add(zid)
        try:
            await self._limit_peaks()
            self._realtime_guard()
        except Exception:  # noqa: BLE001 - peak limiting is a refinement
            _LOGGER.exception("Capacity-tariff limiting failed")
        for zid in failed:
            await self._restore(zid)
        for zid, plan in self.plans.items():
            await self._apply(zid, plan)
        return {"updated": dt_util.now().isoformat()}

    # ------------------------------------------------------------------ control

    def manual_until(self, zone_id: str) -> datetime | None:
        val = self.zone_state(zone_id).get("manual_until")
        if not val:
            return None
        dt = dt_util.parse_datetime(val)
        if dt and dt > dt_util.now():
            return dt
        self.zone_state(zone_id).pop("manual_until", None)
        return None

    def _cfg(self, zone_id: str) -> dict[str, Any]:
        return next(z for z in self.zone_configs if z[CONF_ZONE_ID] == zone_id)

    def _device(self, cfg: dict[str, Any]) -> tuple[bool, bool, float | None, float]:
        """(available, is_off, value, step) for the controlled entity."""
        if cfg.get(CONF_MODE) == MODE_PERCENT:
            st = self.hass.states.get(cfg.get(CONF_NUMBER) or "")
            if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                return False, False, None, 1.0
            val = _float(st.state)
            step = _float(st.attributes.get("step")) or 1.0
            return True, val is not None and val <= 0, val, step
        st = self.hass.states.get(cfg.get(CONF_CLIMATE) or "")
        if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False, False, None, 0.5
        step = _float(st.attributes.get("target_temp_step")) or 0.5
        if st.state == "off":
            return True, True, None, step
        return True, False, _float(st.attributes.get("temperature")), step

    @staticmethod
    def _round(value: float, step: float) -> float:
        return round(round(value / step) * step, 2) if step > 0 else value

    def _scheduled_now(self, zone_id: str) -> float:
        zone = self.zone(zone_id)
        value, _ = scheduled_value(zone, self.schedule(zone_id), self.overrides(zone_id), dt_util.now())
        return max(zone.min_value, min(zone.max_value, value))

    async def _write(self, cfg: dict[str, Any], off: bool, value: float | None, dev_off: bool) -> None:
        if cfg.get(CONF_MODE) == MODE_PERCENT:
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": cfg[CONF_NUMBER], "value": 0 if off else value}, blocking=True,
            )
            return
        climate = cfg[CONF_CLIMATE]
        if off:
            await self.hass.services.async_call(
                "climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": "off"}, blocking=True
            )
            return
        if dev_off:
            await self.hass.services.async_call(
                "climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": "heat"}, blocking=True
            )
        await self.hass.services.async_call(
            "climate", "set_temperature", {"entity_id": climate, "temperature": value}, blocking=True
        )

    async def _restore(self, zone_id: str) -> None:
        """If we turned a zone off, give it back its scheduled heat."""
        last = self._applied.get(zone_id)
        if not last or not last.get("off"):
            return
        try:
            cfg = self._cfg(zone_id)
            available, dev_off, _, step = self._device(cfg)
            if not available:
                return
            value = self._round(self._scheduled_now(zone_id), step)
            await self._write(cfg, False, value, dev_off)
            self._applied[zone_id] = {"value": value, "off": False, "at": dt_util.now()}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not restore heating for %s", zone_id)

    async def _apply(self, zone_id: str, plan: Plan) -> None:
        st = self.zone_state(zone_id)
        if not st.get("auto", True) or not plan.hours:
            return
        cfg = self._cfg(zone_id)
        available, dev_off, current, step = self._device(cfg)
        if not available:
            return
        now = dt_util.now()
        last = self._applied.get(zone_id)
        paused = self.manual_until(zone_id)

        # Someone changed the device by hand (setpoint, or on/off) -> step back.
        if last and not paused and now - last["at"] > MANUAL_GRACE:
            changed = dev_off != bool(last.get("off")) or (
                not dev_off
                and current is not None
                and last.get("value") is not None
                and abs(current - last["value"]) > step / 2 + 0.01
            )
            if changed:
                now_sched = plan.hours[0].scheduled
                nxt = next(
                    (h.start for h in plan.hours[1:] if h.scheduled != now_sched),
                    now + timedelta(hours=3),
                )
                paused = nxt
                st["manual_until"] = nxt.isoformat()
                await self._save()
                _LOGGER.info("Manual change on %s, pausing until %s", zone_id, nxt)
        if paused:
            # follow the device so the pause ends cleanly when it expires
            self._applied[zone_id] = {
                "value": current, "off": dev_off, "at": last["at"] if last else now
            }
            return

        zone = self.zone(zone_id)
        hour = plan.hours[0]
        off = hour.target is None or (zone.is_percent and hour.target <= 0)
        value = None if hour.target is None else self._round(hour.target, step)
        if zone_id in self._shed and value is not None and not off:
            # live capacity guard: lower this room for the rest of the hour
            value = self._round(max(zone.min_value, value - (20 if zone.is_percent else 2)), step)
        if value is not None and not off and not zone.is_percent and value < zone.safety_min_temp:
            # whatever stacked up (away, price, peak, feedback): never ask for less than the floor
            value = self._round(zone.safety_min_temp, step)

        # Safety floor with hysteresis (needs a room temperature).
        room = self.room_temperature(zone_id)
        hold = self._safety.get(zone_id, False)
        if room is not None:
            if room < zone.safety_min_temp:
                hold = True
            elif room >= zone.safety_min_temp + 0.5:
                hold = False
        self._safety[zone_id] = hold
        if off and hold:
            off = False
            value = self._round(hour.scheduled, step)

        needs = (
            (off and not dev_off)
            or (not off and dev_off)
            or (not off and (current is None or abs(current - (value or 0)) > step / 2 + 0.01))
        )
        if zone.is_percent:
            # for percent entities "off" is simply 0
            needs = current is None or abs(current - (0 if off else value or 0)) > step / 2 + 0.01
        try:
            if needs:
                await self._write(cfg, off, value, dev_off)
            self._applied[zone_id] = {
                "value": None if off and not zone.is_percent else (0 if off else value),
                "off": off,
                "at": now if needs or not last else last["at"],
            }
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not apply plan for %s", zone_id)

    # ------------------------------------------------------------------ editing API

    async def async_set_schedule(
        self, zone_id: str, weekdays: list[int], values: list[float]
    ) -> None:
        st = self.zone_state(zone_id)
        for wd in weekdays:
            st["schedule"][str(wd)] = [float(v) for v in values]
        # a new standing schedule replaces today's one-off edits on those days
        today = dt_util.now().date()
        for d in list(st["overrides"]):
            day = dt_util.parse_date(d)
            if day and day.weekday() in weekdays and day >= today:
                st["overrides"].pop(d)
        st.pop("manual_until", None)
        await self._save()
        await self.async_request_refresh()

    async def async_set_override(
        self, zone_id: str, day: str, values: list[float] | None
    ) -> None:
        st = self.zone_state(zone_id)
        if values is None:
            st["overrides"].pop(day, None)
        else:
            st["overrides"][day] = [float(v) for v in values]
        st.pop("manual_until", None)
        await self._save()
        await self.async_request_refresh()

    async def async_set_settings(self, zone_id: str | None, settings: dict[str, Any]) -> None:
        if zone_id is None:
            rules = self.state.setdefault("rules", {})
            rules.update({k: v for k, v in settings.items() if k in TUNABLE_RULES})
            if "optimize" in settings:
                self.state["optimize"] = bool(settings["optimize"])
        else:
            st = self.zone_state(zone_id)
            st["settings"].update({k: v for k, v in settings.items() if k in TUNABLE_ZONE})
            if "auto" in settings:
                st["auto"] = bool(settings["auto"])
                st.pop("manual_until", None)
                if not st["auto"]:
                    await self._restore(zone_id)
                self._applied.pop(zone_id, None)
        await self._save()
        await self.async_request_refresh()

    async def async_resume(self, zone_id: str) -> None:
        self.zone_state(zone_id).pop("manual_until", None)
        self._applied.pop(zone_id, None)
        await self._save()
        await self.async_request_refresh()

    async def async_feedback(self, zone_id: str, kind: str) -> str:
        """'Too cold' / 'too warm' from the card: nudge the zone's settings."""
        zone = self.zone(zone_id)
        st = self.zone_state(zone_id)
        before = {k: getattr(zone, k) for k in TUNABLE_ZONE}
        if before.get("away_until_hour") != (st["settings"].get("away_until_hour", before["away_until_hour"])):
            before["away_until_hour"] = st["settings"]["away_until_hour"]
        plan = self.plans.get(zone_id)
        mode = plan.hours[0].mode if plan and plan.hours else "normal"
        history = st.setdefault("feedback", [])
        lang = "nb" if (self.hass.config.language or "").startswith(("nb", "no", "nn")) else "en"
        after, message = apply_feedback(
            before, kind, mode, history, lang=lang, percent=zone.is_percent,
            room_temp=self.room_temperature(zone_id),
        )
        changes = changed_keys(before, after)
        # Anyone in the house may press these, so keep the effect bounded: at most
        # 8 nudges a day, comfort within ±2 °C of where it stood that morning and
        # never at or below the safety floor.
        today = dt_util.now().date().isoformat()
        anchor = st.get("feedback_anchor") or {}
        if anchor.get("date") != today:
            anchor = {"date": today, "comfort": before.get("comfort"), "count": 0}
        anchor["count"] = int(anchor.get("count", 0)) + 1
        st["feedback_anchor"] = anchor
        if anchor["count"] > 8:
            await self._save()
            return (
                "Det er justert mange ganger i dag – endre innstillingene i planen i stedet."
                if lang == "nb" else "Adjusted many times today – change the settings in the plan instead."
            )
        if "comfort" in changes and not zone.is_percent and anchor.get("comfort") is not None:
            lo = max(anchor["comfort"] - 2.0, zone.safety_min_temp + 0.5)
            changes["comfort"] = max(lo, min(anchor["comfort"] + 2.0, float(changes["comfort"])))
            if changes["comfort"] == before.get("comfort"):
                changes.pop("comfort")
        delta = changes.pop("schedule_delta", None)
        if delta:
            st["schedule"] = {str(k): v for k, v in shift_schedule(st["schedule"], delta).items()}
        st["settings"].update({k: v for k, v in changes.items() if k in TUNABLE_ZONE})
        self._applied.pop(zone_id, None)
        await self._save()
        await self.async_request_refresh()
        return message

    # ------------------------------------------------------------------ learning

    async def async_learn(self) -> None:
        """Fit the thermal model and heater power of each zone from recorder history."""
        if not self.options.get(CONF_USE_HISTORY, False):
            return
        try:
            from homeassistant.components.recorder import get_instance, history
        except ImportError:  # recorder not loaded
            return
        end = dt_util.utcnow()
        start = end - timedelta(days=HISTORY_DAYS)
        weather = self.options.get(CONF_WEATHER)
        changed = await self._learn_arrivals(end)
        for cfg in self.zone_configs:
            zid = cfg[CONF_ZONE_ID]
            power = cfg.get(CONF_POWER_SENSOR)
            if power:
                kw = await self._learn_power(power, start, end)
                if kw:
                    self.zone_state(zid)["learned_kw"] = kw
                    changed = True
            climate = cfg.get(CONF_CLIMATE)
            if not climate or cfg.get(CONF_MODE) == MODE_PERCENT and not cfg.get(CONF_TEMP_SENSOR):
                continue
            ids = [climate] + ([weather] if weather else [])
            sensor = cfg.get(CONF_TEMP_SENSOR)
            if sensor:
                ids.append(sensor)
            try:
                hist = await get_instance(self.hass).async_add_executor_job(
                    lambda: history.get_significant_states(
                        self.hass, start, end, ids, significant_changes_only=False
                    )
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("History unavailable for %s: %s", zid, err)
                continue
            samples = await self.hass.async_add_executor_job(
                partial(
                    samples_from_history, hist, climate, sensor, weather,
                    self.hass.config.latitude, self.hass.config.longitude, start=start, end=end,
                )
            )
            prior = thermal_prior(self.options.get(CONF_HOUSE))
            model = fit_thermal(samples, prior)
            self.zone_state(zid)["thermal"] = asdict(model)
            changed = True
            _LOGGER.debug("Thermal model for %s: %s", zid, model)
        if changed:
            await self._save()
            await self.async_request_refresh()

    async def _learn_arrivals(self, end: datetime) -> bool:
        """When does the household usually get home? (from the presence history)"""
        from homeassistant.components.recorder import get_instance, history

        ids = [e for e in self.options.get(CONF_PRESENCE, []) if e.startswith(("person.", "device_tracker."))]
        if not ids:
            return False
        start = end - timedelta(days=42)
        try:
            hist = await get_instance(self.hass).async_add_executor_job(
                lambda: history.get_significant_states(self.hass, start, end, ids, no_attributes=True)
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("No presence history: %s", err)
            return False
        series = {
            eid: [(dt_util.as_local(s.last_changed), s.state) for s in hist.get(eid, []) if hasattr(s, "state")]
            for eid in ids
        }
        model = learn_arrivals(series, dt_util.get_default_time_zone(), now=dt_util.now(), previous=self.arrivals)
        self.state["arrivals"] = model.as_dict()
        return True

    async def _learn_power(self, entity_id: str, start: datetime, end: datetime) -> float | None:
        """Heater power = the typical 'on' level seen on its power sensor (kW)."""
        from homeassistant.components.recorder import get_instance, history

        try:
            hist = await get_instance(self.hass).async_add_executor_job(
                lambda: history.state_changes_during_period(
                    self.hass, start, end, entity_id, no_attributes=False
                )
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("No power history for %s: %s", entity_id, err)
            return None
        states = hist.get(entity_id, [])
        vals = sorted(v for v in (_float(x.state) for x in states) if v and v > 5)
        if len(vals) < 10:
            return None
        unit = (states[-1].attributes.get("unit_of_measurement") or "W").lower()
        p90 = vals[int(len(vals) * 0.9)]
        kw = p90 / 1000 if unit == "w" else p90
        return round(kw, 2) if 0.02 <= kw <= 50 else None

    # ------------------------------------------------------------------ export

    def _arrival_export(self) -> dict[str, Any] | None:
        zones = [self.zone(z[CONF_ZONE_ID]) for z in self.zone_configs]
        lead = max((self.lead_hours(z) for z in zones if not z.is_percent), default=2.0)
        return arrival_summary(self.arrivals, dt_util.now().date(), lead)

    def _tariff_export(self) -> dict[str, Any]:
        tariff = tariff_from_options(self.options)
        lang = "nb" if (self.hass.config.language or "").startswith(("nb", "no", "nn")) else "en"
        return {"mode": tariff.mode, "label": describe_tariff(tariff, lang), **tariff.as_dict()}

    def export(self) -> dict[str, Any]:
        """Everything the card needs, JSON serialisable."""
        levels = classify_prices(self.price_hours, self.rules())
        extra = _float(self.options.get(CONF_PRICE_EXTRA)) or 0.0
        lang = "nb" if (self.hass.config.language or "").startswith(("nb", "no", "nn")) else "en"
        cur = "kr" if self.hass.config.currency in ("NOK", "SEK", "DKK") else self.hass.config.currency
        zones = []
        for cfg in self.zone_configs:
            zid = cfg[CONF_ZONE_ID]
            try:
                zc = self.zone(zid)
            except StopIteration:
                continue
            st = self.zone_state(zid)
            plan = self.plans.get(zid)
            mu = self.manual_until(zid)
            zones.append(
                {
                    "zone_id": zid,
                    "name": zc.name,
                    "mode": zc.mode,
                    "climate_entity": cfg.get(CONF_CLIMATE),
                    "number_entity": cfg.get(CONF_NUMBER),
                    "settings": {
                        **{k: getattr(zc, k) for k in TUNABLE_ZONE},
                        **({"away_until_hour": AUTO_HOUR}
                           if st.get("settings", {}).get("away_until_hour") == AUTO_HOUR else {}),
                    },
                    "thermal": {**asdict(zc.thermal), "quality": {"poor": "uncertain", "unknown": None}.get(model_quality(zc.thermal), model_quality(zc.thermal))},
                    "heater_kw": zc.heater_kw,
                    "heater_kw_source": (
                        "config" if (_float(cfg.get(CONF_HEATER_KW)) or 0) > 0
                        else "measured" if st.get("learned_kw") else "estimate"
                    ),
                    "schedule": {str(k): v for k, v in self.schedule(zid).items()},
                    "overrides": deepcopy(self.overrides(zid)),
                    "auto": st.get("auto", True),
                    "manual_until": mu.isoformat() if mu else None,
                    "room_temperature": self.room_temperature(zid),
                    "plan": {**plan.as_dict(), "explanation": explain_savings(plan, lang, cur)} if plan else None,
                    "feedback": list(st.get("feedback", []))[-5:],
                }
            )
        return {
            "entry_id": self.config_entry.entry_id,
            "title": self.config_entry.title,
            "currency": self.hass.config.currency,
            "price_unit": self.prices.unit,
            "price_source": self.prices.description,
            "price_extra": 0.0,  # already part of the (effective) prices
            "price_extra_configured": extra,
            "rules": asdict(self.rules()),
            "optimize": self.optimize,
            "features": ["feedback"],
            **({"arrival": self._arrival_export()} if self.options.get(CONF_USE_HISTORY) else {}),
            "tariff": self._tariff_export(),
            "peak": self.peak_state.as_dict() if self.peak_state else None,
            "away": self.away,
            "history": bool(self.options.get(CONF_USE_HISTORY, False)),
            "house": self.options.get(CONF_HOUSE),
            "outdoor_now": self.outdoor_now(),
            "prices": [
                {"start": h.isoformat(), "price": round(p, 4), "level": levels.get(h)}
                for h, p in self.price_hours.items()
            ],
            "zones": zones,
            "updated": dt_util.now().isoformat(),
        }
