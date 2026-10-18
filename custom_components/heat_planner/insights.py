"""Pure helpers for the home-level sensors, alerts and the card's load row.

* ``roll_savings`` keeps the realised savings estimate (per completed hour) in a
  plain dict that lives in the coordinator's Store, so it survives restarts.
* ``alert_message`` gives the short Norwegian/English text for each alert kind.
* ``load_series`` builds the house load for the card (measured + planned).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .planner import hour_key

EVENT_ALERT = "heat_planner_alert"

ALERT_PEAK_RISK = "peak_risk"
ALERT_EXTREME_TOMORROW = "extreme_price_tomorrow"
ALERT_MODEL_POOR = "model_poor"
ALERT_KINDS = [ALERT_PEAK_RISK, ALERT_EXTREME_TOMORROW, ALERT_MODEL_POOR]

CONF_ALERTS = "alerts"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_ALERT_KINDS = "kinds"

PEAK_RISK_MARGIN_KW = 0.3




# --------------------------------------------------------------------------- savings


def _month(d: datetime) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _day(d: datetime) -> str:
    return d.date().isoformat()


def roll_savings(sv: dict[str, Any], hour: datetime, current: Callable[[], float | None]) -> bool:
    """Advance the savings book-keeping to ``hour`` (start of the current clock hour).

    ``sv["pending"]`` holds the estimate for the hour in progress, taken from the
    plans as they were when that hour started. Once a later hour is seen it is
    added to the day and month totals (of the hour it belongs to). ``current`` is
    only called when a new hour needs its estimate. Returns True if ``sv`` changed.
    """
    changed = False
    pending = sv.get("pending")
    if pending:
        started = dt_util.parse_datetime(str(pending.get("hour")))
        if started is None:
            sv["pending"] = None
            changed = True
        elif started < hour:
            amount = float(pending.get("amount") or 0.0)
            if sv.get("month") != _month(started):
                if (sv.get("month") or "") < _month(started):
                    sv["month"], sv["month_total"] = _month(started), 0.0
            if sv.get("month") == _month(started):
                sv["month_total"] = round(float(sv.get("month_total") or 0.0) + amount, 4)
            if sv.get("day") != _day(started):
                if (sv.get("day") or "") < _day(started):
                    sv["day"], sv["day_total"] = _day(started), 0.0
            if sv.get("day") == _day(started):
                sv["day_total"] = round(float(sv.get("day_total") or 0.0) + amount, 4)
            sv["hours"] = int(sv.get("hours") or 0) + 1
            sv["pending"] = None
            changed = True
    if sv.get("month") != _month(hour):
        sv["month"], sv["month_total"] = _month(hour), 0.0
        changed = True
    if sv.get("day") != _day(hour):
        sv["day"], sv["day_total"] = _day(hour), 0.0
        changed = True
    if not sv.get("pending"):
        estimate = current()
        if estimate is not None:
            sv["pending"] = {"hour": hour.isoformat(), "amount": round(estimate, 4)}
            changed = True
    return changed


def savings_value(sv: dict[str, Any] | None, now: datetime, period: str) -> float | None:
    """Realised savings this month (``"month"``) or today (``"day"``)."""
    if not sv:
        return None
    key = _month(now) if period == "month" else _day(now)
    if sv.get(period) != key:
        return 0.0
    return round(float(sv.get(f"{period}_total") or 0.0), 2)


# --------------------------------------------------------------------------- alerts


def lang_of(language: str | None) -> str:
    return "nb" if (language or "").startswith(("nb", "no", "nn")) else "en"


def _num(v: float, decimals: int = 1) -> str:
    return f"{v:.{decimals}f}"


def alert_message(kind: str, lang: str, **data: Any) -> str:
    """Short notification text for an alert."""
    nb = lang == "nb"
    if kind == ALERT_PEAK_RISK:
        proj = _num(float(data.get("projected_kwh", 0.0)))
        cap = _num(float(data.get("cap_kw", 0.0)))
        if nb:
            return f"Strømforbruket denne timen ligger an til {proj} kWh – grensen er {cap} kW. Vurder å vente med store forbrukere."
        return f"This hour is heading for {proj} kWh – the limit is {cap} kW. Consider holding off on big loads."
    if kind == ALERT_EXTREME_TOMORROW:
        hours = data.get("hours") or []
        span = f"{hours[0]}–{hours[-1]}" if len(hours) > 1 else (hours[0] if hours else "")
        price = data.get("max_price")
        unit = data.get("unit") or ""
        p = f" ({_num(float(price), 2)} {unit})" if price is not None else ""
        if nb:
            return f"Ekstremt høye strømpriser i morgen kl. {span}{p}. Varmen planlegges rundt dem."
        return f"Extreme electricity prices tomorrow at {span}{p}. Heating is planned around them."
    if kind == ALERT_MODEL_POOR:
        name = data.get("zone_name") or data.get("zone") or ""
        if nb:
            return f"Varmemodellen for {name} passer dårlig med målingene. Sjekk temperatursensor og effekt."
        return f"The heating model for {name} fits the measurements poorly. Check its temperature sensor and power."
    return kind


# --------------------------------------------------------------------------- load row


def load_series(
    history: dict[datetime, float],
    planned: dict[datetime, float],
    now: datetime,
    *,
    past_hours: int = 12,
    live_kwh: float | None = None,
) -> list[dict[str, Any]]:
    """House load per hour: the last ``past_hours`` measured, then the plan horizon.

    ``planned`` is base load + heating per hour (kW ≈ kWh in one hour). The
    current hour uses ``live_kwh`` (projected from the meter) when known.
    """
    hour = hour_key(now)
    out: list[dict[str, Any]] = []
    for i in range(past_hours, 0, -1):
        t = hour - timedelta(hours=i)
        if t in history:
            out.append({"start": t.isoformat(), "kw": round(history[t], 2), "measured": True})
    for t in sorted(planned):
        if t < hour:
            continue
        if t == hour and live_kwh is not None:
            out.append({"start": t.isoformat(), "kw": round(live_kwh, 2), "live": True})
        else:
            out.append({"start": t.isoformat(), "kw": round(planned[t], 2)})
    return out
