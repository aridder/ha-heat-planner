"""Glue between Home Assistant and the pure tariff/peak modules.

* What the household pays per kWh (contract + grid tariff) → ``effective_prices``.
* Keeping the month's capacity-tariff step: caps per plan hour from the whole-house
  meter's history, cuts spread across rooms, and a live guard for the current hour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from statistics import quantiles
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .peak import PeakConfig, caps_for_plan, limit_plans, monthly_peaks, top3_average
from .planner import Plan, PlanHour, ZoneConfig, is_protected
from .tariff import TariffConfig

_LOGGER = logging.getLogger(__name__)

CONF_ENERGY = "energy"
R_PEAK = "peak_limit"


def tariff_from_options(options: dict[str, Any]) -> TariffConfig:
    """The contract and grid tariff from the setup step (legacy: spot + price_extra)."""
    legacy_extra = float(options.get("price_extra") or 0.0)
    energy = options.get(CONF_ENERGY)
    if not energy:
        return TariffConfig.from_options(None, legacy_extra=legacy_extra)
    contract = energy.get("contract", "spot")
    grid = energy.get("grid", "none")
    data: dict[str, Any] = {
        "mode": contract,
        "fixed_price": float(energy.get("fixed_price", 0.5) or 0.0),
        "extra": float(energy.get("extra", 0.0) or 0.0),
        # Tibber (and many HACS price sensors) already report the price incl. VAT
        "spot_includes_vat": bool(energy.get("price_includes_vat", options.get("price_type") == "tibber")),
    }
    if options.get("nordpool_area") == "NO4" or energy.get("vat_exempt"):
        data["vat"] = 0.0  # Northern Norway: no VAT on electricity for households
    if grid == "elvia_2026":
        data["preset"] = "elvia_2026"
    else:
        data["preset"] = "custom"
        if grid == "custom":
            data["grid_day"] = float(energy.get("grid_day", 0.0) or 0.0)
            data["grid_night"] = float(energy.get("grid_night", 0.0) or 0.0)
        else:
            data["grid_day"] = data["grid_night"] = 0.0
            if contract == "spot":
                data["vat"] = 0.0  # plain spot, nothing modelled: like before
    return TariffConfig.from_options(data)


def peak_from_options(options: dict[str, Any]) -> PeakConfig | None:
    """Capacity-tariff limiting – only with a whole-house meter (it needs the real peaks)."""
    energy = options.get(CONF_ENERGY) or {}
    if not energy.get("peak_limit") or not energy.get("house_power_sensor"):
        return None
    return PeakConfig.from_options({"enabled": True, **(options.get("peak") or {})})


def house_meter(options: dict[str, Any]) -> str | None:
    return (options.get(CONF_ENERGY) or {}).get("house_power_sensor") or None


# --------------------------------------------------------------------------- history


async def async_house_hourly_kwh(hass: HomeAssistant, entity_id: str, days: int = 45) -> dict[datetime, float]:
    """Hourly energy (kWh) from the long-term statistics of a power sensor (W or kW)."""
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period
    except ImportError:
        return {}
    st = hass.states.get(entity_id)
    unit = (st.attributes.get("unit_of_measurement") if st else None) or "W"
    scale = 1 / 1000 if unit == "W" else 1.0
    start = dt_util.start_of_local_day() - timedelta(days=days)
    try:
        stats = await get_instance(hass).async_add_executor_job(
            statistics_during_period, hass, start, None, {entity_id}, "hour", None, {"mean"}
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("No statistics for %s: %s", entity_id, err)
        return {}
    out: dict[datetime, float] = {}
    for row in stats.get(entity_id, []):
        mean = row.get("mean")
        ts = row.get("start")
        if mean is None or ts is None:
            continue
        when = dt_util.as_local(dt_util.utc_from_timestamp(ts) if isinstance(ts, (int, float)) else ts)
        out[when.replace(minute=0, second=0, microsecond=0)] = max(0.0, float(mean) * scale)
    return out


def base_load(hourly: dict[datetime, float], heating_share: float = 0.0) -> dict[int, float]:
    """A low estimate of the rest of the house per hour of day (25th percentile, last 14 days)."""
    cutoff = dt_util.now() - timedelta(days=14)
    by_hour: dict[int, list[float]] = {}
    for when, kwh in hourly.items():
        if when >= cutoff:
            by_hour.setdefault(when.hour, []).append(kwh)
    out: dict[int, float] = {}
    for hour, vals in by_hour.items():
        low = quantiles(vals, n=4)[0] if len(vals) >= 4 else min(vals)
        out[hour] = max(0.0, low * (1 - heating_share))
    return out


# --------------------------------------------------------------------------- planning


@dataclass
class PeakState:
    """What the card shows about the capacity tariff."""

    cap_kw: float | None = None
    top3_kw: list[float] = field(default_factory=list)
    month_avg_kw: float | None = None
    today_kw: float | None = None
    step_label: str | None = None
    step_cost: float | None = None
    next_step_kw: float | None = None
    metered: bool = False
    shed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cap_kw": self.cap_kw, "top3_kw": self.top3_kw, "month_avg_kw": self.month_avg_kw,
            "today_kw": self.today_kw, "step_label": self.step_label, "step_cost": self.step_cost,
            "next_step_kw": self.next_step_kw, "metered": self.metered, "shed": self.shed,
        }


def _step_label(lo: float, hi: float) -> str:
    def f(v: float) -> str:
        return f"{v:g}".replace(".", ",")
    return f"{f(lo)}–{f(hi)} kW"


def plan_caps(
    cfg: PeakConfig, hourly: dict[datetime, float], hours: list[datetime], now: datetime
) -> tuple[dict[datetime, float], PeakState]:
    """Caps for each plan hour plus a summary for the card."""
    this_month = {k: v for k, v in hourly.items() if (k.year, k.month) == (now.year, now.month)}
    prev = now.replace(day=1) - timedelta(days=1)
    last_month = {k: v for k, v in hourly.items() if (k.year, k.month) == (prev.year, prev.month)}
    peaks = monthly_peaks(this_month)
    today = now.date()
    today_so_far = max((v for k, v in this_month.items() if k.date() == today), default=0.0)
    ref = top3_average(monthly_peaks(last_month)) if last_month else None
    caps = caps_for_plan(hours, peaks, today_so_far, now, cfg, reference_kw=ref)
    top = sorted((v for d, v in peaks), reverse=True)[:3]
    avg = top3_average(peaks) if peaks else None
    step = cfg.step_for(max(avg or 0.0, ref or 0.0))
    state = PeakState(
        cap_kw=caps.get(hours[0]) if hours else None,
        top3_kw=[round(v, 2) for v in top],
        month_avg_kw=None if avg is None else round(avg, 2),
        today_kw=round(today_so_far, 2),
        step_label=_step_label(step.lower_kw, step.upper_kw),
        step_cost=step.cost,
        next_step_kw=step.upper_kw,
        metered=bool(hourly),
    )
    return caps, state


def heaters_only_caps(cfg: PeakConfig, hours: list[datetime]) -> tuple[dict[datetime, float], PeakState]:
    """Without a whole-house meter: keep the heaters together under the lowest useful cap."""
    cap = cfg.min_cap_kw
    return {h: cap for h in hours}, PeakState(cap_kw=cap, metered=False)


def limit(
    zones: dict[str, ZoneConfig],
    plans: dict[str, Plan],
    caps: dict[datetime, float],
    base: dict[int, float],
    priority: dict[str, int],
) -> dict[str, dict[datetime, float]]:
    """Duty to remove per zone and hour so the house stays under the caps."""
    zone_hours: dict[str, list[dict[str, Any]]] = {}
    all_hours: set[datetime] = set()
    for zid, plan in plans.items():
        z = zones[zid]
        items = []
        for h in plan.hours:
            kw = h.kwh  # one hour → kWh equals average kW
            near_floor = h.predicted is not None and h.predicted <= z.safety_min_temp + 0.5
            items.append({
                "start": h.start, "kw": kw,
                "protected": is_protected(z, h.start) or h.target is None,
                "can_cut_kw": 0.0 if near_floor else kw,
            })
            all_hours.add(h.start)
        zone_hours[zid] = items
    base_kw = {h: base.get(h.hour, 0.0) for h in all_hours}
    cuts = limit_plans(zone_hours, caps, base_kw, priority)
    return {zid: {h: kw / max(zones[zid].heater_kw, 0.05) for h, kw in hc.items()} for zid, hc in cuts.items()}


def apply_cuts(
    zone: ZoneConfig,
    slots: list[PlanHour],
    duty_cuts: dict[datetime, float],
    predicted: dict[datetime, float] | None = None,
) -> bool:
    """Turn duty reductions into lower targets. Returns True if anything changed.

    A floor thermostat keeps heating at full power until the room reaches its
    setpoint, so a cut only removes power if the setpoint goes below where the
    room would otherwise be. ``predicted`` (from the plan before the cut) is used
    for that; the safety floor always wins.
    """
    changed = False
    for s in slots:
        cut = duty_cuts.get(s.start)
        if not cut or s.target is None:
            continue
        if zone.is_percent:
            new = max(zone.min_value, s.target - round(cut * 100))
        else:
            floor = max(zone.min_value, zone.safety_min_temp)
            new = s.target - (1.0 if cut < 0.5 else 2.0)
            pred = (predicted or {}).get(s.start)
            if pred is not None:
                # just below where the room will be: the heater idles this hour
                new = min(new, round((pred - 0.5) * 2) / 2)
            new = max(floor, new)
        if new < s.target:
            s.target = new
            if s.mode not in ("off",):
                s.mode = "save"
            if R_PEAK not in s.reasons:
                s.reasons.append(R_PEAK)
            changed = True
    return changed
