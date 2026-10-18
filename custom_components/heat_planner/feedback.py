"""Comfort feedback: "too cold" / "too warm" from the people in the room.

Pure module (no Home Assistant imports). One tap nudges one setting by a small,
explainable step, chosen from what the plan was doing at that moment:

* too cold in a lowered hour (setback/save/away/off): the saving was too
  harsh, so the price reduction or the away setback is softened by 0.5°
  (5 % in percent zones), and the "never off below" floor is raised by 0.5°
  when the room was close to it;
* too cold in a normal/comfort/pre-heat hour: comfort goes up 0.5°;
* too warm: comfort goes down 0.5° (pre-heat is softened instead when the
  plan was pre-heating). Percent zones have no comfort temperature, so the
  week schedule is shifted by 5 percentage points (``schedule_delta``).

The caller stores ``history`` (bounded to 20 events) and applies the returned
settings. Identical feedback within ten minutes is ignored, so a double tap or
two people tapping at once does not count twice.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

TOO_COLD = "too_cold"
TOO_WARM = "too_warm"
KINDS = (TOO_COLD, TOO_WARM)

LOWERED_MODES = ("setback", "save", "away", "off")
HISTORY_MAX = 20
DEBOUNCE = timedelta(minutes=10)
STEP = 0.5
STEP_PCT = 5.0
NEAR_SAFETY = 0.5  # °C above the safety floor that counts as "near it"
SAFETY_MAX = 25.0
COMFORT_MIN = 10.0

# Key in the returned settings that is NOT a zone setting: percentage points to
# add to every hour of a percent zone's week schedule (see shift_schedule).
SCHEDULE_DELTA = "schedule_delta"

_TXT = {
    "nb": {
        "debounce": "Allerede notert – gi det litt tid.",
        "shift": "Senker mindre i dyre timer ({old} → {new}).",
        "away": "Senker mindre når ingen er hjemme ({old} → {new}).",
        "safety": "Varmen slås aldri av under {new} (var {old}).",
        "comfort_up": "Komfort {old} → {new}.",
        "comfort_down": "Komfort {old} → {new}.",
        "preheat": "Forvarmer mindre før dyre timer ({old} → {new}).",
        "sched_up": "Døgnkurven heves {d} prosentpoeng.",
        "sched_down": "Døgnkurven senkes {d} prosentpoeng.",
        "limit": "Allerede på grensen – endre døgnkurven under «Endre døgn».",
        "setback": "Natt eller senket time i døgnkurven – endre den under «Endre døgn» om den er for lav.",
        "unknown": "Ukjent tilbakemelding.",
    },
    "en": {
        "debounce": "Already noted – give it a little time.",
        "shift": "Lowering less in expensive hours ({old} → {new}).",
        "away": "Lowering less when nobody is home ({old} → {new}).",
        "safety": "Heating is never turned off below {new} (was {old}).",
        "comfort_up": "Comfort {old} → {new}.",
        "comfort_down": "Comfort {old} → {new}.",
        "preheat": "Pre-heating less before expensive hours ({old} → {new}).",
        "sched_up": "Day curve raised by {d} percentage points.",
        "sched_down": "Day curve lowered by {d} percentage points.",
        "limit": "Already at the limit – change the day curve with “Edit day”.",
        "setback": "A night or lowered hour in your day curve – change it with “Edit day” if it is too low.",
        "unknown": "Unknown feedback.",
    },
}


def _lang(lang: str | None) -> dict[str, str]:
    lang = (lang or "en").lower()
    return _TXT["nb"] if lang[:2] in ("nb", "no", "nn") else _TXT["en"]


def _fmt(v: float, percent: bool, lang: str | None) -> str:
    s = f"{v:.1f}".rstrip("0").rstrip(".") if not percent else f"{round(v)}"
    if (lang or "").lower()[:2] in ("nb", "no", "nn"):
        s = s.replace(".", ",")
    return f"{s} %" if percent else f"{s}°"


def _parse(ts: Any) -> datetime | None:
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def apply_feedback(
    settings: dict[str, Any],
    kind: str,
    now_mode: str,
    history: list[dict[str, Any]],
    *,
    lang: str = "en",
    percent: bool = False,
    room_temp: float | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], str]:
    """Return ``(new_settings, explanation)`` and append the event to ``history``.

    ``settings`` is the zone's tunable settings (as exported). The returned dict
    is a full copy with the change applied; for percent zones it may carry an
    extra ``schedule_delta`` key (not a zone setting – pop it and apply it with
    :func:`shift_schedule`). Use :func:`changed_keys` to see what changed. ``history`` is updated
    in place and trimmed to the last 20 events. ``now_mode`` is the plan mode
    of the current hour (normal/comfort/setback/preheat/save/off/away).
    """
    t = _lang(lang)
    if kind not in KINDS:
        return deepcopy(settings), t["unknown"]
    now = now or datetime.now(UTC)

    # Debounce: the same kind of feedback within ten minutes counts once.
    for ev in reversed(history):
        when = _parse(ev.get("ts"))
        if when is None:
            continue
        try:
            recent = abs(now - when) < DEBOUNCE
        except TypeError:  # naive vs aware: compare as naive
            recent = abs(now.replace(tzinfo=None) - when.replace(tzinfo=None)) < DEBOUNCE
        if recent and ev.get("kind") == kind:
            return deepcopy(settings), t["debounce"]
        break  # only the latest event matters

    s = deepcopy(settings)
    out: dict[str, Any] = {}
    msgs: list[str] = []
    step = STEP_PCT if percent else STEP
    fmt = lambda v: _fmt(v, percent, lang)  # noqa: E731
    fmt_t = lambda v: _fmt(v, False, lang)  # noqa: E731

    def change(key: str, new: float, msg_key: str, f: Callable[[float], str] = fmt) -> bool:
        old = float(s.get(key, 0) or 0)
        new = round(new, 2)
        if abs(new - old) < 1e-9:
            return False
        out[key] = new
        msgs.append(t[msg_key].format(old=f(old), new=f(new)))
        return True

    mode = now_mode or "normal"
    if kind == TOO_COLD and mode in LOWERED_MODES:
        if mode in ("save", "off"):
            change("price_shift", max(0.0, float(s.get("price_shift", 0)) - step), "shift")
        elif mode == "away":
            change("away_offset", min(0.0, float(s.get("away_offset", 0)) + step), "away")
        safety = s.get("safety_min_temp")
        if (
            not percent
            and safety is not None
            and room_temp is not None
            and room_temp <= float(safety) + NEAR_SAFETY
        ):
            cap = min(SAFETY_MAX, float(s.get("comfort", SAFETY_MAX)))
            change("safety_min_temp", min(cap, float(safety) + STEP), "safety", fmt_t)
        if not out:
            msgs.append(t["setback"] if mode == "setback" else t["limit"])
    elif kind == TOO_COLD:
        if percent:
            out[SCHEDULE_DELTA] = STEP_PCT
            msgs.append(t["sched_up"].format(d=round(STEP_PCT)))
        elif not change(
            "comfort",
            min(float(s.get("max_value", 28)), float(s.get("comfort", 21)) + STEP),
            "comfort_up",
        ):
            msgs.append(t["limit"])
    elif mode == "preheat" and float(s.get("preheat", 0) or 0) > 0:
        change("preheat", max(0.0, float(s["preheat"]) - step), "preheat")
    elif percent:
        out[SCHEDULE_DELTA] = -STEP_PCT
        msgs.append(t["sched_down"].format(d=round(STEP_PCT)))
    elif not change(
        "comfort",
        max(
            COMFORT_MIN,
            float(s.get("min_value", 5)),
            float(s.get("comfort", 21)) - STEP,
        ),
        "comfort_down",
    ):
        msgs.append(t["limit"])

    history.append(
        {
            "ts": now.isoformat(),
            "kind": kind,
            "mode": mode,
            "changed": dict(out),
        }
    )
    del history[:-HISTORY_MAX]
    s.update(out)
    return s, " ".join(msgs)


def changed_keys(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Keys in ``after`` that differ from ``before`` (incl. ``schedule_delta``)."""
    return {k: v for k, v in after.items() if before.get(k) != v}


def shift_schedule(
    schedule: dict[Any, list[float]], delta: float, lo: float = 0.0, hi: float = 100.0
) -> dict[Any, list[float]]:
    """Add ``delta`` to every hour of a (percent) week schedule, clamped."""
    return {
        k: [max(lo, min(hi, round(v + delta, 1))) for v in day]
        for k, day in schedule.items()
    }


DAILY_MAX = 8  # nudges per zone and day
DAILY_RANGE = 2.0  # °C comfort may move from where it stood that morning


def record_feedback(
    st: dict[str, Any],
    before: dict[str, Any],
    kind: str,
    now_mode: str,
    *,
    today: str,
    lang: str = "en",
    percent: bool = False,
    room_temp: float | None = None,
    safety_min_temp: float = 18.0,
    now: datetime | None = None,
) -> str:
    """Apply one press to a zone's stored state ``st`` (settings, schedule, history).

    Anyone in the house may press these, so the effect is bounded: at most
    ``DAILY_MAX`` nudges a day, comfort within ±``DAILY_RANGE`` °C of where it
    stood that morning and never at or below the safety floor. Returns the message.
    """
    history = st.setdefault("feedback", [])
    after, message = apply_feedback(
        before, kind, now_mode, history, lang=lang, percent=percent, room_temp=room_temp, now=now
    )
    changes = changed_keys(before, after)
    anchor = st.get("feedback_anchor") or {}
    if anchor.get("date") != today:
        anchor = {"date": today, "comfort": before.get("comfort"), "count": 0}
    anchor["count"] = int(anchor.get("count", 0)) + 1
    st["feedback_anchor"] = anchor
    if anchor["count"] > DAILY_MAX:
        return (
            "Det er justert mange ganger i dag – endre innstillingene i planen i stedet."
            if lang == "nb" else "Adjusted many times today – change the settings in the plan instead."
        )
    if "comfort" in changes and not percent and anchor.get("comfort") is not None:
        wanted = float(changes["comfort"])
        lo = max(anchor["comfort"] - DAILY_RANGE, safety_min_temp + 0.5)
        changes["comfort"] = max(lo, min(anchor["comfort"] + DAILY_RANGE, wanted))
        if changes["comfort"] == before.get("comfort"):
            changes.pop("comfort")
            # say what actually happened: nothing, because today's limit is reached
            message = (
                f"Komforten er allerede justert så mye som tillatt i dag (±{DAILY_RANGE:g}°). Endre den i innstillingene om du vil mer."
                if lang == "nb" else
                f"Comfort has already been adjusted as far as allowed today (±{DAILY_RANGE:g}°). Change it in the settings for more."
            )
        elif changes["comfort"] != wanted:
            val = f"{changes['comfort']:g}".replace(".", "," if lang == "nb" else ".")
            message = (
                f"Komforttemperatur satt til {val}° – det meste som tillates i dag."
                if lang == "nb" else f"Comfort temperature set to {val}° – the most allowed today."
            )
    delta = changes.pop(SCHEDULE_DELTA, None)
    if delta:
        st["schedule"] = {str(k): v for k, v in shift_schedule(st["schedule"], delta).items()}
    st.setdefault("settings", {}).update({k: v for k, v in changes.items() if k in before})
    return message
