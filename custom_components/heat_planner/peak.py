"""Peak control for the Norwegian capacity tariff (pure Python, no HA imports).

The monthly grid "kapasitetsledd" is chosen from the average of the three
highest hourly consumption peaks (kWh/h = average kW) on three *different*
days of the month. For direct electric heating, keeping those peaks low is
worth far more than shifting load to cheap spot hours.

Building blocks:

* :class:`PeakConfig` – the step table (Elvia 2026 by default) and margins.
* :func:`monthly_peaks` / :func:`top3_average` – where the month stands.
* :func:`cap_for_hour` / :func:`caps_for_plan` – the kW cap to stay under so
  the month does not cross into the next step.
* :func:`limit_plans` – trim planned zone power so house total stays under the
  cap, cutting low priority zones first.
* :func:`realtime_shed` – which zones to set back right now from a live
  whole-house power reading (with hysteresis).
* :func:`estimated_monthly_saving`.

Conventions: priority – a higher number is more important; the lowest priority
is cut first. Energy per hour (kWh) equals average power (kW) for that hour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

# (upper bound kW, monthly cost) – Elvia 2026. Above the last step the table
# continues with +110 per 5 kW.
ELVIA_2026_STEPS: list[tuple[float, float]] = [
    (2.0, 125.0),
    (5.0, 190.0),
    (10.0, 300.0),
    (15.0, 410.0),
    (20.0, 520.0),
    (25.0, 630.0),
]
EXTRA_STEP_KW = 5.0
EXTRA_STEP_COST = 110.0


@dataclass(frozen=True)
class Step:
    index: int
    lower_kw: float
    upper_kw: float
    cost: float


@dataclass
class PeakConfig:
    """Capacity tariff settings."""

    enabled: bool = True
    steps: list[tuple[float, float]] = field(default_factory=lambda: list(ELVIA_2026_STEPS))
    margin_kw: float = 0.3  # stay this far below a step boundary
    min_cap_kw: float = 3.0  # never cap the house below this (comfort/safety)
    hysteresis_kw: float = 0.3  # realtime: restore only this far below the cap

    def step_for(self, kw: float) -> Step:
        """The step a top-3 average of ``kw`` falls in.

        A value exactly on a boundary counts in the higher step (conservative).
        """
        lower = 0.0
        steps = sorted(self.steps)
        for i, (upper, cost) in enumerate(steps):
            if kw < upper:
                return Step(i, lower, upper, cost)
            lower = upper
        last_upper, last_cost = steps[-1] if steps else (0.0, 0.0)
        n = int((kw - last_upper) // EXTRA_STEP_KW) + 1
        return Step(
            len(steps) - 1 + n,
            last_upper + (n - 1) * EXTRA_STEP_KW,
            last_upper + n * EXTRA_STEP_KW,
            last_cost + n * EXTRA_STEP_COST,
        )

    def cost_for(self, kw: float) -> float:
        return self.step_for(kw).cost

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "steps": [list(s) for s in self.steps],
            "margin_kw": self.margin_kw,
            "min_cap_kw": self.min_cap_kw,
            "hysteresis_kw": self.hysteresis_kw,
        }

    @classmethod
    def from_options(cls, data: dict[str, Any] | None) -> "PeakConfig":
        data = data or {}
        kw: dict[str, Any] = {}
        for key in ("enabled", "margin_kw", "min_cap_kw", "hysteresis_kw"):
            if data.get(key) is not None:
                kw[key] = data[key]
        if data.get("steps"):
            kw["steps"] = [(float(u), float(c)) for u, c in data["steps"]]
        return cls(**kw)


# --------------------------------------------------------------------------- month state


def monthly_peaks(
    hourly_kwh: dict[datetime, float], month: tuple[int, int] | None = None
) -> list[tuple[date, float]]:
    """Highest hourly kWh per day for one month, sorted by date.

    ``month`` is ``(year, month)``; default is the month of the latest hour.
    """
    if not hourly_kwh:
        return []
    if month is None:
        last = max(hourly_kwh)
        month = (last.year, last.month)
    peaks: dict[date, float] = {}
    for h, v in hourly_kwh.items():
        if (h.year, h.month) != month or v is None:
            continue
        d = h.date()
        peaks[d] = max(peaks.get(d, 0.0), float(v))
    return sorted(peaks.items())


def top3_average(daily_peaks: Iterable[tuple[date, float]] | Iterable[float]) -> float:
    """Average of the three highest daily peaks (fewer days: of those there are)."""
    vals = sorted((_val(p) for p in daily_peaks), reverse=True)[:3]
    return sum(vals) / len(vals) if vals else 0.0


def _val(p: Any) -> float:
    return float(p[1]) if isinstance(p, tuple) else float(p)


# --------------------------------------------------------------------------- caps


def cap_for_hour(
    month_daily_peaks: Iterable[tuple[date, float]],
    today_peak_so_far: float,
    cfg: PeakConfig,
    *,
    today: date | None = None,
    reference_kw: float | None = None,
    limit_kw: float | None = None,
) -> float:
    """kW (= kWh per hour) to stay under for the rest of today.

    ``limit_kw`` (see :func:`choose_limit`) replaces the current step's limit: the
    month's top-3 average to aim for.

    ``month_daily_peaks`` are the peaks of the *other* days of this month (an
    entry for ``today`` is ignored). The target is the current step – the step
    of the top-3 average including today so far, or of ``reference_kw`` (e.g.
    last month's average) if that is higher, so the first days of a month do
    not force the house into an unrealistically low step – minus ``margin_kw``.

    Today's peak only matters if it ends up among the top three:
    * up to the 3rd highest other day it changes nothing, and
    * above that, the average becomes (x + top1 + top2) / 3 ≤ limit,
    * and no single day is allowed above the limit itself.
    With fewer than three days so far, the missing days are assumed to end at
    the limit (so the first day of a month is capped at the limit itself).
    The cap is never below today's peak so far (that hour is already counted)
    nor below ``min_cap_kw``.
    """
    others = sorted(
        (v for d, v in month_daily_peaks if today is None or d != today), reverse=True
    )
    if limit_kw is not None:
        limit = limit_kw
    else:
        current = top3_average(others + ([today_peak_so_far] if today_peak_so_far > 0 else []))
        basis = max(current, reference_kw or 0.0)
        limit = cfg.step_for(basis).upper_kw - cfg.margin_kw

    k = min(3, len(others) + 1)
    # Never above the limit itself: spending the month's headroom on one day
    # would starve the following days.
    allowed = min(limit, k * limit - sum(others[: k - 1]))
    if len(others) >= 3:
        allowed = max(allowed, others[2])
    return round(max(allowed, today_peak_so_far, cfg.min_cap_kw), 3)


def choose_limit(
    cfg: PeakConfig,
    month_daily_peaks: Iterable[tuple[date, float]],
    today_peak_so_far: float,
    *,
    today: date,
    days_left: int,
    reference_kw: float | None = None,
    floor_kw: float | None = None,
) -> float:
    """The month's top-3 average to aim for (a step boundary minus ``margin_kw``).

    By default the current step (this month so far, or ``reference_kw`` – last
    month – if higher): it is defended, never given up. With ``floor_kw`` – an
    estimate of the lowest daily peak the house can reach (the rest of the house
    at a high estimate plus the heating that cannot be moved, see
    :func:`energy.peak_floor`) – a cheaper, lower step is aimed for when it is
    still reachable this month: if every remaining day (today included) ends at
    ``floor_kw``, the top-3 average must come out under that step (with half the
    margin). A day
    already above it does not end the attempt as long as the arithmetic still
    works; once it does not, the current step is defended again.
    """
    others = [v for d, v in month_daily_peaks if d != today]
    current = top3_average(others + ([today_peak_so_far] if today_peak_so_far > 0 else []))
    step = cfg.step_for(max(current, reference_kw or 0.0))
    default = step.upper_kw - cfg.margin_kw
    if floor_kw is None:
        return default
    rest = [max(today_peak_so_far, floor_kw)] + [floor_kw] * max(0, min(3, days_left - 1))
    best_case = top3_average(others + rest)
    for upper, cost in sorted(cfg.steps):
        if upper >= step.upper_kw - 1e-9:
            break
        lim = upper - cfg.margin_kw
        if cost >= step.cost or lim < max(floor_kw, cfg.min_cap_kw):
            continue
        # judged against the step itself (with half the margin): the margin is for
        # planning, and a day that ended a little over the limit must not end the attempt
        if best_case <= upper - cfg.margin_kw / 2 + 1e-9:
            return lim
    return default


def caps_for_plan(
    hours: Iterable[datetime],
    month_daily_peaks: Iterable[tuple[date, float]],
    today_peak_so_far: float,
    now: datetime,
    cfg: PeakConfig,
    *,
    reference_kw: float | None = None,
    limit_kw: float | None = None,
) -> dict[datetime, float]:
    """Caps for every plan hour (today and following days).

    Days after today assume each earlier day ends exactly at its cap (worst
    case). A new month starts empty, with the previous month's projected top-3
    average as ``reference_kw``. ``limit_kw`` (this month only): the top-3 average
    to aim for instead of the current step's (see :func:`choose_limit`).
    """
    today = now.date()
    peaks: dict[date, float] = {d: v for d, v in month_daily_peaks if d != today}
    day_caps: dict[date, float] = {}
    hours = sorted(hours)
    days = sorted({h.date() for h in hours if h.date() >= today} | {today})
    ref = reference_kw
    month = (today.year, today.month)
    lim = limit_kw
    for d in days:
        if (d.year, d.month) != month:
            month = (d.year, d.month)
            ref = max(ref or 0.0, top3_average(list(peaks.values())))
            peaks = {}
            lim = None
        so_far = today_peak_so_far if d == today else 0.0
        cap = cap_for_hour(list(peaks.items()), so_far, cfg, reference_kw=ref, limit_kw=lim)
        day_caps[d] = cap
        peaks[d] = cap
    return {h: day_caps.get(h.date(), day_caps[today]) for h in hours}


# --------------------------------------------------------------------------- planning


def limit_plans(
    zone_hours: dict[str, list[dict[str, Any]]],
    caps: dict[datetime, float],
    base_kw: dict[datetime, float],
    priority: dict[str, int],
) -> dict[str, dict[datetime, float]]:
    """kW to cut per zone and hour so that sum(zones) + base ≤ cap.

    ``zone_hours[zone_id]`` is a list of ``{"start": datetime, "kw": planned
    average kW (duty × heater_kw), "protected": bool, "can_cut_kw": float}``.
    ``can_cut_kw`` is how much may be removed without breaking the safety floor
    (default: all of ``kw``); protected hours are never cut.

    Zones are cut by priority (lowest first). Zones sharing a priority share the
    cut in proportion to what each can give, so the cut is spread rather than
    hitting one room hard. Hours without a cap are untouched. If the excess
    cannot be covered, everything cuttable is cut. Only non-zero cuts are
    returned; divide by the zone's heater kW for a duty reduction.
    """
    by_hour: dict[datetime, list[tuple[str, float, float]]] = {}
    for zid, items in zone_hours.items():
        for it in items:
            kw = max(0.0, float(it.get("kw") or 0.0))
            can = 0.0 if it.get("protected") else float(it.get("can_cut_kw", kw) or 0.0)
            by_hour.setdefault(it["start"], []).append((zid, kw, _clamp(can, 0.0, kw)))

    cuts: dict[str, dict[datetime, float]] = {}
    for h in sorted(by_hour):
        cap = caps.get(h)
        if cap is None:
            continue
        entries = by_hour[h]
        excess = sum(kw for _, kw, _ in entries) + base_kw.get(h, 0.0) - cap
        if excess <= 1e-9:
            continue
        for prio in sorted({priority.get(z, 0) for z, _, _ in entries}):
            group = sorted((e for e in entries if priority.get(e[0], 0) == prio))
            avail = sum(c for _, _, c in group)
            if avail <= 0:
                continue
            share = min(1.0, excess / avail)
            for zid, _, can in group:
                cut = round(can * share, 3)
                if cut > 0:
                    cuts.setdefault(zid, {})[h] = cut
            excess -= avail * share
            if excess <= 1e-9:
                break
    return cuts


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- realtime


def projected_hour_kwh(energy_so_far_kwh: float, current_kw: float, now: datetime) -> float:
    """Energy the current clock hour will end at if the present power continues.

    Use this (rather than instantaneous power) as ``current_house_kw`` for
    :func:`realtime_shed`: the tariff counts hourly energy, so a short spike
    early in the hour is harmless while a moderate load late in a busy hour is not.
    """
    elapsed = (now.minute * 60 + now.second) / 3600
    return energy_so_far_kwh + current_kw * (1 - elapsed)


def realtime_shed(
    current_house_kw: float,
    cap_kw: float,
    zones: list[dict[str, Any]],
    *,
    currently_shed: Iterable[str] = (),
    hysteresis_kw: float = 0.3,
) -> list[str]:
    """Zone ids that should be set back right now.

    ``zones``: ``{"zone_id", "priority": int, "kw": current/expected draw when
    heating, "protected": bool}``. ``current_house_kw`` is live whole-house
    power (or better, :func:`projected_hour_kwh`) and *includes* the zones that
    are still heating; shed zones are assumed to draw nothing now.

    * Above the cap: keep the zones already shed and add unprotected ones,
      lowest priority first (bigger loads first on ties), until the excess is
      covered.
    * Below the cap: restore shed zones, most important first, only while the
      house would stay at least ``hysteresis_kw`` below the cap – this avoids
      flapping. Callers should also enforce a minimum hold time.
    """
    shed = [z for z in currently_shed]
    info = {z["zone_id"]: z for z in zones}
    shed = [z for z in shed if z in info]

    def order(z: dict[str, Any]) -> tuple:
        return (z.get("priority", 0), -float(z.get("kw") or 0.0), z["zone_id"])

    if current_house_kw > cap_kw:
        need = current_house_kw - cap_kw
        for z in sorted(zones, key=order):
            if need <= 0:
                break
            kw = float(z.get("kw") or 0.0)
            if z["zone_id"] in shed or z.get("protected") or kw <= 0:
                continue
            shed.append(z["zone_id"])
            need -= kw
        return shed

    load = current_house_kw
    for zid in sorted(shed, key=lambda i: order(info[i]), reverse=True):
        kw = float(info[zid].get("kw") or 0.0)
        if info[zid].get("protected") or load + kw <= cap_kw - hysteresis_kw:
            shed.remove(zid)
            load += kw
    return shed


# --------------------------------------------------------------------------- reporting


def estimated_monthly_saving(top3_before: float, top3_after: float, cfg: PeakConfig) -> float:
    """Monthly capacity charge saved by bringing the top-3 average down."""
    return cfg.cost_for(top3_before) - cfg.cost_for(top3_after)
