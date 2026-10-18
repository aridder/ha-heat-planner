"""Sensors: target, predicted temperature, cost, savings and price level."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HeatPlannerConfigEntry
from .energy import peak_from_options
from .entity import HeatPlannerEntity
from .insights import savings_value
from .planner import LEVEL_CHEAP, LEVEL_EXPENSIVE, LEVEL_EXTREME, LEVEL_NORMAL, MODE_PERCENT

MODES = ["comfort", "setback", "preheat", "save", "off", "away", "normal", "manual", "disabled"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatPlannerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coord = entry.runtime_data
    entities: list[SensorEntity] = [
        PriceLevelSensor(coord),
        EffectivePriceSensor(coord),
        RealisedSavingsSensor(coord, "month"),
        RealisedSavingsSensor(coord, "day"),
    ]
    if peak_from_options(coord.options) is not None:
        entities += [CapacityStepSensor(coord), MonthPeakAverageSensor(coord), PeakHeadroomSensor(coord)]
    for z in coord.zone_configs:
        zid = z["zone_id"]
        percent = z.get("mode") == MODE_PERCENT
        entities += [
            TargetSensor(coord, zid, percent),
            ModeSensor(coord, zid),
            CostSensor(coord, zid),
            SavingsSensor(coord, zid),
        ]
        if not percent or z.get("temperature_sensor"):
            entities.append(PredictedSensor(coord, zid))
    async_add_entities(entities)


class TargetSensor(HeatPlannerEntity, SensorEntity):
    def __init__(self, coord, zone_id: str, percent: bool) -> None:
        super().__init__(coord, zone_id, "target")
        self._percent = percent
        if percent:
            self._attr_native_unit_of_measurement = PERCENTAGE
        else:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        plan = self.plan
        if not plan or not plan.hours:
            return None
        t = plan.hours[0].target
        return 0 if t is None and self._percent else t

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self.plan
        if not plan or not plan.hours:
            return {}
        now = plan.hours[0]
        nxt = next((h for h in plan.hours[1:] if h.target != now.target), None)
        return {
            "reasons": now.reasons,
            "next_change": nxt.start.isoformat() if nxt else None,
            "next_target": nxt.target if nxt else None,
            "forecast": [
                {"start": h.start.isoformat(), "target": h.target, "mode": h.mode}
                for h in plan.hours
            ],
        }


class ModeSensor(HeatPlannerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = MODES

    def __init__(self, coord, zone_id: str) -> None:
        super().__init__(coord, zone_id, "mode")

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.zone_state(self.zone_id).get("auto", True):
            return "disabled"
        if self.coordinator.manual_until(self.zone_id):
            return "manual"
        plan = self.plan
        return plan.hours[0].mode if plan and plan.hours else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        mu = self.coordinator.manual_until(self.zone_id)
        plan = self.plan
        return {
            "manual_until": mu.isoformat() if mu else None,
            "reasons": plan.hours[0].reasons if plan and plan.hours else [],
        }


class PredictedSensor(HeatPlannerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, coord, zone_id: str) -> None:
        super().__init__(coord, zone_id, "predicted")

    @property
    def native_value(self) -> float | None:
        plan = self.plan
        if not plan or len(plan.hours) < 2:
            return None
        return plan.hours[1].predicted

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self.plan
        zone = self.coordinator.zone(self.zone_id)
        return {
            "forecast": [
                {"start": h.start.isoformat(), "temperature": h.predicted} for h in (plan.hours if plan else [])
            ],
            "model_learned": zone.thermal.learned,
            "heat_rate": zone.thermal.gross_rate,
            "loss_time_constant_h": zone.thermal.tau_hours,
        }


class _CostBase(HeatPlannerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self.coordinator.hass.config.currency

    def _extra(self) -> float:
        try:
            return float(self.coordinator.options.get("price_extra") or 0)
        except (TypeError, ValueError):
            return 0.0


class CostSensor(_CostBase):
    def __init__(self, coord, zone_id: str) -> None:
        super().__init__(coord, zone_id, "cost_24h")

    @property
    def native_value(self) -> float | None:
        plan = self.plan
        if not plan or plan.cost is None:
            return None
        hours = plan.hours[:24]
        if any(h.cost is None for h in hours):
            return None
        return round(sum(h.cost for h in hours) + sum(h.kwh for h in hours) * self._extra(), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self.plan
        hours = plan.hours[:24] if plan else []
        return {"kwh": round(sum(h.kwh for h in hours), 2)}


class SavingsSensor(_CostBase):
    def __init__(self, coord, zone_id: str) -> None:
        super().__init__(coord, zone_id, "savings_24h")

    @property
    def native_value(self) -> float | None:
        plan = self.plan
        if not plan or plan.savings is None:
            return None
        extra = self._extra() * (plan.baseline_kwh - plan.kwh)
        return round(plan.savings + extra, 2)


class PriceLevelSensor(HeatPlannerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [LEVEL_CHEAP, LEVEL_NORMAL, LEVEL_EXPENSIVE, LEVEL_EXTREME]

    def __init__(self, coord) -> None:
        super().__init__(coord, None, "price_level")

    @property
    def native_value(self) -> str | None:
        from .planner import classify_prices

        now = dt_util.now().replace(minute=0, second=0, microsecond=0)
        levels = classify_prices(self.coordinator.price_hours, self.coordinator.rules())
        return levels.get(now)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = dt_util.now().replace(minute=0, second=0, microsecond=0)
        return {
            "price": self.coordinator.price_hours.get(now),
            "unit": self.coordinator.prices.unit,
            "source": self.coordinator.prices.description,
            "away": self.coordinator.away,
        }


# --------------------------------------------------------------------------- home level


class EffectivePriceSensor(HeatPlannerEntity, SensorEntity):
    """What this hour's kWh costs the household (contract + grid tariff)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coord) -> None:
        super().__init__(coord, None, "effective_price")

    @property
    def native_unit_of_measurement(self) -> str | None:
        return f"{self.coordinator.hass.config.currency}/kWh"

    @property
    def native_value(self) -> float | None:
        price = self.coordinator.effective_price_now()
        return None if price is None else round(price, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = dt_util.now().replace(minute=0, second=0, microsecond=0)
        tariff = self.coordinator._tariff_export()
        return {"tariff": tariff.get("label"), "spot": self.coordinator.spot_hours.get(now)}


class RealisedSavingsSensor(HeatPlannerEntity, SensorEntity):
    """Estimated savings of the hours already past (this month / today); survives restarts."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coord, period: str) -> None:
        super().__init__(coord, None, "savings_month" if period == "month" else "savings_today")
        self._period = period

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self.coordinator.hass.config.currency

    @property
    def last_reset(self):
        now = dt_util.now()
        start = dt_util.start_of_local_day(now)
        return start.replace(day=1) if self._period == "month" else start

    @property
    def native_value(self) -> float | None:
        return savings_value(self.coordinator.state.get("savings"), dt_util.now(), self._period)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sv = self.coordinator.state.get("savings") or {}
        pending = sv.get("pending") or {}
        return {"hours_counted": int(sv.get("hours") or 0), "current_hour_estimate": pending.get("amount")}


class _PeakBase(HeatPlannerEntity, SensorEntity):
    @property
    def available(self) -> bool:
        return super().available and self.coordinator.peak_state is not None


class CapacityStepSensor(_PeakBase):
    """The month's capacity-tariff step (e.g. "5–10 kW")."""

    def __init__(self, coord) -> None:
        super().__init__(coord, None, "capacity_step")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.peak_state.step_label if self.coordinator.peak_state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self.coordinator.peak_state
        if st is None:
            return {}
        return {
            "step_cost": st.step_cost,
            "next_step_kw": st.next_step_kw,
            "top3_kw": st.top3_kw,
            "month_avg_kw": st.month_avg_kw,
            "cap_kw_today": st.cap_kw,
        }


class MonthPeakAverageSensor(_PeakBase):
    """Average of the month's three highest daily peaks – what the capacity step is based on."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_suggested_display_precision = 2

    def __init__(self, coord) -> None:
        super().__init__(coord, None, "month_peak_average")

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.peak_state
        return st.month_avg_kw if st else None


class PeakHeadroomSensor(_PeakBase):
    """Today's cap minus this hour's projected energy (live from the house meter)."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_suggested_display_precision = 2

    def __init__(self, coord) -> None:
        super().__init__(coord, None, "peak_headroom")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.coordinator.live_signal, self._live)
        )

    @callback
    def _live(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self.coordinator.peak_headroom()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self.coordinator.peak_state
        projected = self.coordinator.projected_hour_kwh()
        return {
            "cap_kw": st.cap_kw if st else None,
            "projected_kwh": None if projected is None else round(projected, 2),
            "shed": sorted(self.coordinator._shed),
        }
