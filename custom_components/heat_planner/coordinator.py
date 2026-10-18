"""Coordinator: prices, presence, planning, control and learning."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, fields
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
from .learning import samples_from_history
from .solar import forecast_solar
from .planner import (
    model_quality,
    MODE_PERCENT,
    MODE_SETPOINT,
    Plan,
    PriceRules,
    ThermalModel,
    ZoneConfig,
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
        return ZoneConfig(
            zone_id=zone_id,
            name=cfg.get(CONF_NAME, zone_id),
            mode=mode,
            thermal=thermal,
            **{**base, **settings},
        )

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

    def plan_zone(
        self,
        zone_id: str,
        *,
        zone: ZoneConfig | None = None,
        schedule: dict[int, list[float]] | None = None,
        overrides: dict[str, list[float]] | None = None,
        hours: int = PLAN_HOURS,
        start: datetime | None = None,
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
        )

    async def _async_update_data(self) -> dict[str, Any]:
        points = await self.prices.async_get()
        self.price_hours = hourly_prices(points)
        await self._fetch_outdoor()
        self.away = self._is_away()
        self.plans = {}
        for cfg in self.zone_configs:
            zid = cfg[CONF_ZONE_ID]
            try:
                self.plans[zid] = self.plan_zone(zid)
            except Exception:  # noqa: BLE001 - one broken zone must not stop the rest
                _LOGGER.exception("Planning failed for zone %s", zid)
                await self._restore(zid)
                continue
            await self._apply(zid, self.plans[zid])
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
        changed = False
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

    def export(self) -> dict[str, Any]:
        """Everything the card needs, JSON serialisable."""
        levels = classify_prices(self.price_hours, self.rules())
        extra = _float(self.options.get(CONF_PRICE_EXTRA)) or 0.0
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
                    "settings": {k: getattr(zc, k) for k in TUNABLE_ZONE},
                    "thermal": {**asdict(zc.thermal), "quality": model_quality(zc.thermal)},
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
                    "plan": plan.as_dict() if plan else None,
                }
            )
        return {
            "entry_id": self.config_entry.entry_id,
            "title": self.config_entry.title,
            "currency": self.hass.config.currency,
            "price_unit": self.prices.unit,
            "price_source": self.prices.description,
            "price_extra": extra,
            "rules": asdict(self.rules()),
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
