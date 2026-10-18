"""Tests for comfort feedback (feedback.py)."""

from datetime import UTC, datetime, timedelta

from custom_components.heat_planner.feedback import (
    HISTORY_MAX,
    SCHEDULE_DELTA,
    apply_feedback,
    changed_keys,
    shift_schedule,
)

NOW = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)
BASE = {
    "comfort": 21.0, "min_value": 5.0, "max_value": 28.0, "away_offset": -4.0, "away_until_hour": 13,
    "price_shift": 1.0, "preheat": 1.0, "safety_min_temp": 18.0, "extreme_off": True,
}


def test_too_cold_in_saving_hour_softens_price_shift():
    hist: list = []
    new, msg = apply_feedback(BASE, "too_cold", "save", hist, lang="nb", room_temp=20.5, now=NOW)
    assert changed_keys(BASE, new) == {"price_shift": 0.5}
    assert "dyre timer" in msg
    assert len(hist) == 1 and hist[0]["kind"] == "too_cold" and hist[0]["mode"] == "save"
    assert BASE["price_shift"] == 1.0  # input untouched


def test_too_cold_off_near_safety_raises_floor():
    new, msg = apply_feedback(BASE, "too_cold", "off", [], lang="en", room_temp=18.3, now=NOW)
    assert new["price_shift"] == 0.5 and new["safety_min_temp"] == 18.5
    assert "never turned off below 18.5°" in msg


def test_too_cold_away_softens_setback_with_floor_zero():
    new, _ = apply_feedback(BASE, "too_cold", "away", [], now=NOW)
    assert new["away_offset"] == -3.5
    s = {**BASE, "away_offset": 0.0, "price_shift": 0.0}
    new, msg = apply_feedback(s, "too_cold", "away", [], now=NOW)
    assert changed_keys(s, new) == {}
    assert "limit" in msg


def test_too_cold_normal_raises_comfort_with_cap():
    new, msg = apply_feedback(BASE, "too_cold", "normal", [], lang="nb", now=NOW)
    assert new["comfort"] == 21.5 and "21,5°" in msg
    new, _ = apply_feedback({**BASE, "comfort": 28.0}, "too_cold", "comfort", [], now=NOW)
    assert new["comfort"] == 28.0


def test_too_warm_lowers_comfort_or_preheat():
    new, _ = apply_feedback(BASE, "too_warm", "normal", [], now=NOW)
    assert new["comfort"] == 20.5
    new, msg = apply_feedback(BASE, "too_warm", "preheat", [], now=NOW)
    assert changed_keys(BASE, new) == {"preheat": 0.5}
    assert "Pre-heating less" in msg


def test_percent_zone_steps_in_percent():
    pct = {**BASE, "comfort": 0.0, "max_value": 100.0, "min_value": 0.0, "price_shift": 15.0, "away_offset": -20.0}
    new, _ = apply_feedback(pct, "too_cold", "save", [], percent=True, now=NOW)
    assert new["price_shift"] == 10.0
    new, _ = apply_feedback(pct, "too_warm", "normal", [], percent=True, now=NOW)
    assert new[SCHEDULE_DELTA] == -5.0 and new["comfort"] == 0.0
    assert shift_schedule({0: [2.0, 50.0, 99.0]}, 5) == {0: [7.0, 55.0, 100.0]}
    assert shift_schedule({0: [2.0]}, -5) == {0: [0.0]}


def test_debounce_and_bounded_history():
    hist: list = []
    apply_feedback(BASE, "too_cold", "normal", hist, now=NOW)
    new, msg = apply_feedback(BASE, "too_cold", "normal", hist, now=NOW + timedelta(minutes=4))
    assert changed_keys(BASE, new) == {} and "Already noted" in msg
    assert len(hist) == 1
    # a different kind is not debounced
    new, _ = apply_feedback(BASE, "too_warm", "normal", hist, now=NOW + timedelta(minutes=5))
    assert new["comfort"] == 20.5
    # after ten minutes the same kind counts again
    new, _ = apply_feedback(BASE, "too_cold", "normal", hist, now=NOW + timedelta(minutes=16))
    assert new["comfort"] == 21.5
    for i in range(30):
        apply_feedback(BASE, "too_cold" if i % 2 else "too_warm", "normal", hist, now=NOW + timedelta(hours=i + 1))
    assert len(hist) == HISTORY_MAX
    assert hist[-1]["ts"] == (NOW + timedelta(hours=30)).isoformat()


def test_unknown_kind():
    new, msg = apply_feedback(BASE, "meh", "normal", [], now=NOW)
    assert new == BASE and msg


def test_message_says_when_the_daily_limit_stops_a_change() -> None:
    from datetime import datetime, timedelta, timezone

    from custom_components.heat_planner.feedback import record_feedback

    st = {"settings": {}, "schedule": {}}
    before = {"comfort": 21.0, "price_shift": 0.0, "preheat": 0.0, "away_offset": -4.0,
              "safety_min_temp": 18.0, "max_value": 28.0, "min_value": 5.0}
    msg = ""
    t = datetime(2026, 10, 19, 9, tzinfo=timezone.utc)
    for i in range(6):
        msg = record_feedback(st, before, "too_cold", "normal", today="2026-10-19", lang="en", now=t + timedelta(minutes=15 * i))
        before = {**before, "comfort": st["settings"].get("comfort", before["comfort"])}
    assert before["comfort"] == 23.0
    assert "allowed today" in msg
