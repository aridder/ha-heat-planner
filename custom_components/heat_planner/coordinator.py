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
from homeassistant.helpers.dispatcher import async_dispatcher_send
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
from .control import (
    HourMeter,
    decide,
    limit_peaks,
    live_shed,
    manual_changed,
    needs_write,
    off_run_hours,
    pause_end,
    round_step,
    safety_hold,
    settle,
    too_cold,
)
from .energy import (
    PeakState,
    async_house_hourly_kwh,
    apply_cuts,
    base_load,
    house_meter,
    peak_from_options,
    plan_caps,
    tariff_from_options,
)
from .learning import samples_from_history
from .insights import (
    ALERT_EXTREME_TOMORROW,
    ALERT_KINDS,
    ALERT_MODEL_POOR,
    ALERT_PEAK_RISK,
    CONF_ALERT_KINDS,
    CONF_ALERTS,
    CONF_NOTIFY_SERVICE,
    EVENT_ALERT,
    PEAK_RISK_MARGIN_KW,
    alert_message,
    lang_of,
    load_series,
    roll_savings,
    savings_value,
)
from .feedback import record_feedback
from .occupancy import AUTO_HOUR, ArrivalModel, expected_arrival, learn_arrivals, summary as arrival_summary, warm_from_hour
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
    baseline_hour,
    build_plan,
    classify_prices,
    default_schedule,
    fit_thermal,
    hour_key,
    hourly_prices,
    hours_from,
    scheduled_value,
    simulate_hour,
)
from .discovery import default_heater_kw, thermal_prior
from .prices import PriceSource
from .repairs import async_check_issues

_LOGGER = logging.getLogger(__name__)


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
        self.peak_state: PeakState | None = None
        self._house_kwh: dict[datetime, float] = {}
        self._peak_base: dict[int, float] = {}
        self._house_fetched: datetime | None = None
        self._shed: dict[str, datetime] = {}
        self._meter = HourMeter()
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
        base["warmup"] = True  # optimal start before schedule steps (see planner.warmup_targets)
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
        await self._save()  # flush savings/alert book-keeping before a reload
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
        async_dispatcher_send(self.hass, self.live_signal)
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
        method: str | None = None,
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
            method=method or ("optimize" if self.optimize else "rules"),
            initial_off_run=self._off_run(zone_id),
            solar=self.solar or None,
            adjust=adjust,
        )

    def _off_run(self, zone_id: str) -> int:
        """Whole hours this zone has already been switched off by us (for max-off)."""
        last = self._applied.get(zone_id)
        if last is not None:
            since = last.get("off_since") if last.get("off") else None
        else:
            # after a restart: how long it had been off is in the Store
            since = dt_util.parse_datetime(str(self.zone_state(zone_id).get("off_since") or ""))
        return off_run_hours(since, dt_util.now())

    async def _prices(self) -> None:
        """Spot prices → what the household actually pays per kWh."""
        points = await self.prices.async_get()
        self.spot_hours = hourly_prices(points)
        tariff = tariff_from_options(self.options)
        if tariff.mode == "fixed" and not self.spot_hours:
            self.price_hours = fixed_prices(hours_from(dt_util.now(), PLAN_HOURS), tariff)
        else:
            self.price_hours = effective_prices(self.spot_hours, tariff)

    async def _house_history(self) -> dict[datetime, float]:
        meter = house_meter(self.options)
        if not meter:
            return {}
        now = dt_util.now()
        if self._house_fetched is None or now - self._house_fetched > timedelta(minutes=30):
            self._house_fetched = now
            self._house_kwh = await async_house_hourly_kwh(
                self.hass, meter, energy_statistic=(self.options.get("energy") or {}).get("house_energy_statistic")
            )
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
        self._peak_base = base
        priority = {z[CONF_ZONE_ID]: int(z.get("priority", 0) or 0) for z in self.zone_configs}

        def replan(zid: str, cuts: dict[datetime, float], pred: dict[datetime, float]) -> Plan | None:
            zone = zones[zid]
            try:
                return self.plan_zone(
                    zid, zone=zone, adjust=lambda slots: apply_cuts(zone, slots, cuts, pred)
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Could not apply peak limits to %s", zid)
                return None

        high = {h: max(0.0, v - heating_avg) for h, v in base_load(hourly, high=True).items()}
        current = limit_peaks(zones, dict(self.plans), caps, base, priority, replan, check_base=high)
        self.plans = current
        state.shed = sorted(self._shed)
        self.peak_state = state

    def _track_energy(self, kw: float, now: datetime) -> None:
        """Integrate live whole-house power into energy for the current clock hour."""
        self._meter.add(kw, now)

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
        projected = self._meter.projected(now)
        if projected is None:
            projected = kw
        if projected > state.cap_kw - PEAK_RISK_MARGIN_KW:
            hour = now.replace(minute=0, second=0, microsecond=0)
            self.alert(
                ALERT_PEAK_RISK, key=hour.isoformat(),
                projected_kwh=round(projected, 2), cap_kw=state.cap_kw,
            )
        zones = {zid: self.zone(zid) for zid in self.plans}
        priority = {z[CONF_ZONE_ID]: int(z.get("priority", 0) or 0) for z in self.zone_configs}
        holding = {zid for zid, hold in self._safety.items() if hold} | {
            zid for zid, p in self.plans.items()
            if p.hours and too_cold(zones[zid], p.hours[0], self.room_temperature(zid))
        }
        smooth = self._meter.projected(now, smooth=True)
        self._shed = live_shed(
            projected if smooth is None else smooth, state.cap_kw, self.plans, zones, priority,
            self._shed, now, holding,
        )

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
        try:
            self._check_extreme_tomorrow()
            self._update_savings()
        except Exception:  # noqa: BLE001 - book-keeping must never stop control
            _LOGGER.exception("Savings/alert book-keeping failed")
        async_check_issues(self)
        self._last_ok = dt_util.now()
        return {"updated": self._last_ok.isoformat()}

    # ------------------------------------------------------------------ insights

    @property
    def live_signal(self) -> str:
        return f"{DOMAIN}_{self.config_entry.entry_id}_live"

    @property
    def lang(self) -> str:
        return lang_of(self.hass.config.language)

    def projected_hour_kwh(self) -> float | None:
        """This clock hour's energy so far plus the latest meter reading to the end of the hour."""
        return self._meter.projected(dt_util.now())

    def peak_headroom(self) -> float | None:
        state = self.peak_state
        projected = self.projected_hour_kwh()
        if state is None or state.cap_kw is None or projected is None:
            return None
        return round(state.cap_kw - projected, 2)

    def effective_price_now(self) -> float | None:
        return self.price_hours.get(hour_key(dt_util.now()))

    def alert(self, kind: str, *, key: str, scope: str = "", **data: Any) -> bool:
        """Fire ``heat_planner_alert`` (and notify) unless this kind/scope already fired for ``key``."""
        sent = self.state.setdefault("alerts_sent", {})
        slot = f"{kind}:{scope}" if scope else kind
        if sent.get(slot) == key:
            return False
        sent[slot] = key
        self.store.async_delay_save(lambda: self.state, 5)
        message = alert_message(kind, self.lang, **data)
        self.hass.bus.async_fire(EVENT_ALERT, {
            **data, "kind": kind, "message": message, "entry_id": self.config_entry.entry_id,
        })
        cfg = self.options.get(CONF_ALERTS) or {}
        service = (cfg.get(CONF_NOTIFY_SERVICE) or "").strip()
        if service and kind in cfg.get(CONF_ALERT_KINDS, ALERT_KINDS):
            self.hass.async_create_task(self._notify(service, message))
        return True

    async def _notify(self, service: str, message: str) -> None:
        title = "Heat Planner"
        try:
            if service.startswith("notify.") and self.hass.states.get(service) is not None:
                # a notify entity (new style)
                await self.hass.services.async_call(
                    "notify", "send_message", {"entity_id": service, "title": title, "message": message},
                )
                return
            domain, _, name = service.partition(".")
            if not name:
                domain, name = "notify", domain
            await self.hass.services.async_call(domain, name, {"title": title, "message": message})
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not send Heat Planner alert via %s: %s", service, err)

    def _check_extreme_tomorrow(self) -> None:
        """Once per day, when tomorrow's prices are in and contain an extreme hour."""
        tomorrow = dt_util.now().date() + timedelta(days=1)
        levels = classify_prices(self.price_hours, self.rules())
        hours = sorted(h for h, lv in levels.items() if lv == "extreme" and h.date() == tomorrow)
        if not hours:
            return
        span = [f"{hours[0].hour:02d}:00", f"{(hours[-1].hour + 1) % 24:02d}:00"]
        self.alert(
            ALERT_EXTREME_TOMORROW, key=tomorrow.isoformat(),
            date=tomorrow.isoformat(),
            hours=span,
            extreme_hours=[h.isoformat() for h in hours],
            max_price=round(max(self.price_hours[h] for h in hours), 4),
            unit=self.prices.unit,
        )

    def _hour_savings(self, hour: datetime) -> float | None:
        """Baseline minus planned cost for ``hour`` across automatically controlled zones.

        Two "shadow" rooms are carried from hour to hour on the zone's model (state
        kept in the Store): one following the schedule alone (no price logic,
        nobody away) and one following what was planned. Both start from the real
        room when they (re)start. Restarting the baseline from the real room every
        hour never charged the recovery after a setback (both reheated the same
        cold room); anchoring only the planned side to the real room every hour
        paid for the thermostat's ±0.3 °C cycling as if it were heat-up. Either
        way the sum claimed savings the house did not make.
        """
        hour = hour_key(hour)
        shadow = self.state.setdefault("savings", {}).setdefault("shadow", {})
        total = 0.0
        any_zone = False
        for zid, plan in self.plans.items():
            if not plan.hours or plan.hours[0].start != hour or plan.hours[0].cost is None:
                continue
            st = self.zone_state(zid)
            if not st.get("auto", True) or self.manual_until(zid):
                shadow.pop(zid, None)
                continue
            prev = shadow.get(zid) or {}
            if dt_util.parse_datetime(str(prev.get("hour"))) == hour and _float(prev.get("base")) is not None:
                base_t, base_q = _float(prev.get("base")), prev.get("base_queue")
                plan_t, plan_q = _float(prev.get("plan")), prev.get("plan_queue")
            else:
                base_t = plan_t = self.room_temperature(zid)
                base_q = plan_q = None
            try:
                zone = self.zone(zid)
                # one hour without price logic or absence = what this hour costs without us
                base, base_q = baseline_hour(
                    zone, self.schedule(zid), self.overrides(zid), self.price_hours, hour,
                    room_temp=base_t, queue=base_q, outdoor=self.outdoor,
                    outdoor_default=self.outdoor_now(), solar=self.solar or None,
                    warmup=plan.hours[0].warmup,
                )
                ours, plan_q = simulate_hour(
                    zone, plan.hours[0], room_temp=plan_t, queue=plan_q, outdoor=self.outdoor,
                    outdoor_default=self.outdoor_now(), solar=self.solar or None,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("No baseline for %s", zid, exc_info=True)
                continue
            if base.cost is None or ours.cost is None:
                shadow.pop(zid, None)
                continue
            shadow[zid] = {
                "hour": (hour + timedelta(hours=1)).isoformat(),
                "base": base.predicted, "base_queue": [round(q, 3) for q in base_q],
                "plan": ours.predicted, "plan_queue": [round(q, 3) for q in plan_q],
            }
            total += base.cost - ours.cost
            any_zone = True
        return total if any_zone else None

    def _update_savings(self) -> None:
        hour = hour_key(dt_util.now())
        sv = self.state.setdefault("savings", {})
        if roll_savings(sv, hour, lambda: self._hour_savings(hour)):
            self.store.async_delay_save(lambda: self.state, 5)

    def _load_export(self) -> list[dict[str, Any]]:
        planned: dict[datetime, float] = {}
        for p in self.plans.values():
            for h in p.hours:
                planned[h.start] = planned.get(h.start, 0.0) + h.kwh
        planned = {t: v + self._peak_base.get(t.hour, 0.0) for t, v in planned.items()}
        return load_series(self._house_kwh, planned, dt_util.now(), live_kwh=self.projected_hour_kwh())

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
        return round_step(value, step)

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
        if not paused and manual_changed(last, dev_off, current, step, now):
            nxt = pause_end(plan, now)
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
        # Safety floor with hysteresis (needs a room temperature).
        hold = safety_hold(self.room_temperature(zone_id), zone, self._safety.get(zone_id, False))
        self._safety[zone_id] = hold
        off, value = decide(zone, hour, step, shed=zone_id in self._shed, hold=hold)
        value = settle(zone, last, hour.start, off, value, hold=hold)
        needs = needs_write(zone, off, value, dev_off, current, step)
        try:
            if needs:
                await self._write(cfg, off, value, dev_off)
            since = None
            if off:
                if last:
                    since = last.get("off_since") if last.get("off") else None
                else:  # first apply after a restart
                    since = dt_util.parse_datetime(str(st.get("off_since") or ""))
                since = since or now
            self._applied[zone_id] = {
                "value": None if off and not zone.is_percent else (0 if off else value),
                "off": off,
                "at": now if needs or not last else last["at"],
                "off_since": since,
                "hour": hour.start,
            }
            stored = since.isoformat() if since else None
            if st.get("off_since") != stored:
                # persisted so max_off_hours also holds across a restart
                if stored:
                    st["off_since"] = stored
                else:
                    st.pop("off_since", None)
                self.store.async_delay_save(lambda: self.state, 5)
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
        lang = "nb" if (self.hass.config.language or "").startswith(("nb", "no", "nn")) else "en"
        message = record_feedback(
            st, before, kind, mode,
            today=dt_util.now().date().isoformat(), lang=lang, percent=zone.is_percent,
            room_temp=self.room_temperature(zone_id), safety_min_temp=zone.safety_min_temp,
        )
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
            self._check_model(zid, cfg.get(CONF_NAME, zid), model)
            changed = True
            _LOGGER.debug("Thermal model for %s: %s", zid, model)
        if changed:
            await self._save()
            await self.async_request_refresh()

    def _check_model(self, zone_id: str, name: str, model: ThermalModel) -> None:
        """Alert once when a zone's learned model turns out poor (again only after it recovered)."""
        quality = model_quality(model)
        if model.learned and quality == "poor":
            self.alert(ALERT_MODEL_POOR, key="poor", scope=zone_id, zone=zone_id, zone_name=name)
        elif quality in ("good", "fair"):
            self.state.setdefault("alerts_sent", {}).pop(f"{ALERT_MODEL_POOR}:{zone_id}", None)

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
                    "available": self._device(cfg)[0],
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
            "savings": {
                "month": savings_value(self.state.get("savings"), dt_util.now(), "month"),
                "today": savings_value(self.state.get("savings"), dt_util.now(), "day"),
            },
            "peak": {**self.peak_state.as_dict(), "load": self._load_export()} if self.peak_state else None,
            "away": self.away,
            "history": bool(self.options.get(CONF_USE_HISTORY, False)),
            "house": self.options.get(CONF_HOUSE),
            "outdoor_now": self.outdoor_now(),
            "prices": [
                {"start": h.isoformat(), "price": round(p, 4), "level": levels.get(h)}
                for h, p in self.price_hours.items()
            ],
            "zones": zones,
            # when planning last succeeded (the card warns if this gets old)
            "updated": (getattr(self, "_last_ok", None) or dt_util.now()).isoformat(),
        }
