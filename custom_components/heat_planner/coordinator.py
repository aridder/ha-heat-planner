"""Coordinator: prices, presence, planning, control and learning."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime, timedelta
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
from .planner import (
    MODE_PERCENT,
    MODE_SETPOINT,
    Plan,
    PriceRules,
    Sample,
    ThermalModel,
    ZoneConfig,
    build_plan,
    classify_prices,
    default_schedule,
    fit_thermal,
    hourly_prices,
)
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
        self._outdoor_fetched: datetime | None = None
        self._applied: dict[str, dict[str, Any]] = {}
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
            zones[zone_id] = {
                "schedule": {str(k): v for k, v in default_schedule(mode).items()},
                "overrides": {},
                "settings": {},
                "thermal": asdict(ThermalModel()),
                "auto": True,
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
        base["heater_kw"] = float(cfg.get(CONF_HEATER_KW, 1.0))
        settings = {k: v for k, v in st.get("settings", {}).items() if k in TUNABLE_ZONE}
        thermal = ThermalModel(**{
            k: v for k, v in st.get("thermal", {}).items()
            if k in {f.name for f in fields(ThermalModel)}
        })
        return ZoneConfig(
            zone_id=zone_id,
            name=cfg.get(CONF_NAME, zone_id),
            mode=mode,
            thermal=thermal,
            **{**base, **settings},
        )

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
        for cfg in self.zone_configs:
            self.zone_state(cfg[CONF_ZONE_ID])
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
        for entity_id in ids:
            st = self.hass.states.get(entity_id)
            if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                return False  # unknown presence -> assume someone is home
            if entity_id.startswith("zone."):
                if (_float(st.state) or 0) > 0:
                    return False
            elif st.state in (STATE_HOME, STATE_ON):
                return False
        return True

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
        for item in (resp or {}).get(weather, {}).get("forecast", []):
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

    async def _apply(self, zone_id: str, plan: Plan) -> None:
        st = self.zone_state(zone_id)
        if not st.get("auto", True) or not plan.hours:
            return
        cfg = next(z for z in self.zone_configs if z[CONF_ZONE_ID] == zone_id)
        now_hour = plan.hours[0]
        target = now_hour.target
        last = self._applied.get(zone_id)

        current = self._current_value(cfg)
        # Detect manual changes: the device differs from what we last applied.
        if (
            last
            and current is not None
            and last.get("value") is not None
            and abs(current - last["value"]) > 0.25
            and dt_util.now() - last["at"] > MANUAL_GRACE
            and not self.manual_until(zone_id)
        ):
            nxt = next(
                (h.start for h in plan.hours[1:] if h.scheduled != now_hour.scheduled),
                dt_util.now() + timedelta(hours=3),
            )
            st["manual_until"] = nxt.isoformat()
            await self._save()
            _LOGGER.info("Manual change on %s, pausing until %s", zone_id, nxt)
        if self.manual_until(zone_id):
            return

        try:
            if cfg.get(CONF_MODE) == MODE_PERCENT:
                number = cfg.get(CONF_NUMBER)
                if number and target is not None and current != target:
                    await self.hass.services.async_call(
                        "number", "set_value", {"entity_id": number, "value": target},
                        blocking=True,
                    )
                elif number and target is None and current != 0:
                    await self.hass.services.async_call(
                        "number", "set_value", {"entity_id": number, "value": 0},
                        blocking=True,
                    )
                self._applied[zone_id] = {
                    "value": 0 if target is None else target, "at": dt_util.now()
                }
                return

            climate = cfg.get(CONF_CLIMATE)
            st_climate = self.hass.states.get(climate) if climate else None
            if st_climate is None or st_climate.state == STATE_UNAVAILABLE:
                return
            room = self.room_temperature(zone_id)
            zone = self.zone(zone_id)
            if target is None:
                if room is not None and room < zone.safety_min_temp:
                    target = zone.safety_min_temp
                elif st_climate.state != "off":
                    await self.hass.services.async_call(
                        "climate", "set_hvac_mode",
                        {"entity_id": climate, "hvac_mode": "off"}, blocking=True,
                    )
                    self._applied[zone_id] = {"value": None, "at": dt_util.now(), "off": True}
                    return
                else:
                    return
            if st_climate.state == "off":
                await self.hass.services.async_call(
                    "climate", "set_hvac_mode",
                    {"entity_id": climate, "hvac_mode": "heat"}, blocking=True,
                )
            if current is None or abs(current - target) > 0.05:
                await self.hass.services.async_call(
                    "climate", "set_temperature",
                    {"entity_id": climate, "temperature": target}, blocking=True,
                )
            self._applied[zone_id] = {"value": target, "at": dt_util.now()}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not apply plan for %s", zone_id)

    def _current_value(self, cfg: dict[str, Any]) -> float | None:
        if cfg.get(CONF_MODE) == MODE_PERCENT:
            st = self.hass.states.get(cfg.get(CONF_NUMBER) or "")
            return _float(st.state) if st else None
        st = self.hass.states.get(cfg.get(CONF_CLIMATE) or "")
        if st is None:
            return None
        if st.state == "off":
            return None
        return _float(st.attributes.get("temperature"))

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
        await self._save()
        await self.async_request_refresh()

    async def async_resume(self, zone_id: str) -> None:
        self.zone_state(zone_id).pop("manual_until", None)
        self._applied.pop(zone_id, None)
        await self._save()
        await self.async_request_refresh()

    # ------------------------------------------------------------------ learning

    async def async_learn(self) -> None:
        """Fit the thermal model of each setpoint zone from the last 14 days."""
        try:
            from homeassistant.components.recorder import get_instance, history
        except ImportError:  # recorder not loaded
            return
        end = dt_util.utcnow()
        start = end - timedelta(days=14)
        weather = self.options.get(CONF_WEATHER)
        changed = False
        for cfg in self.zone_configs:
            zid = cfg[CONF_ZONE_ID]
            climate = cfg.get(CONF_CLIMATE)
            if not climate:
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
            samples = _samples_from_history(hist, climate, sensor, weather, start, end)
            prior = self.zone(zid).thermal
            model = fit_thermal(samples, prior)
            self.zone_state(zid)["thermal"] = asdict(model)
            changed = True
            _LOGGER.debug("Thermal model for %s: %s", zid, model)
        if changed:
            await self._save()
            await self.async_request_refresh()

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
                    "thermal": asdict(zc.thermal),
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
            "outdoor_now": self.outdoor_now(),
            "prices": [
                {"start": h.isoformat(), "price": round(p, 4), "level": levels.get(h)}
                for h, p in self.price_hours.items()
            ],
            "zones": zones,
            "updated": dt_util.now().isoformat(),
        }


def _samples_from_history(
    hist: dict[str, list],
    climate: str,
    sensor: str | None,
    weather: str | None,
    start: datetime,
    end: datetime,
) -> list[Sample]:
    """Resample history to a 30-minute grid of (room, outdoor, heating)."""

    def series(entity_id: str | None, getter) -> list[tuple[datetime, Any]]:
        if not entity_id:
            return []
        out = []
        for st in hist.get(entity_id, []):
            val = getter(st)
            if val is not None:
                out.append((st.last_updated, val))
        return out

    def at(series_: list[tuple[datetime, Any]], t: datetime) -> Any:
        val = None
        for when, v in series_:
            if when > t:
                break
            val = v
        return val

    room_s = series(sensor, lambda s: _float(s.state)) if sensor else []
    if not room_s:
        room_s = series(
            climate,
            lambda s: (lambda v: v if v is not None and v > 1 else None)(
                _float(s.attributes.get("current_temperature"))
            ),
        )
    heat_s = series(climate, lambda s: s.attributes.get("hvac_action") == "heating")
    out_s = series(weather, lambda s: _float(s.attributes.get("temperature")))
    samples: list[Sample] = []
    t = start
    while t <= end:
        room = at(room_s, t)
        heating = at(heat_s, t)
        outdoor = at(out_s, t)
        if room is not None and heating is not None:
            samples.append(Sample(t, room, outdoor if outdoor is not None else 5.0, bool(heating)))
        t += timedelta(minutes=30)
    return samples
