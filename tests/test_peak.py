"""Tests for the capacity-tariff peak control module."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.heat_planner.peak import (
    PeakConfig,
    cap_for_hour,
    caps_for_plan,
    estimated_monthly_saving,
    limit_plans,
    monthly_peaks,
    projected_hour_kwh,
    realtime_shed,
    top3_average,
)

TZ = ZoneInfo("Europe/Oslo")
H = datetime(2026, 10, 19, 17, 0, tzinfo=TZ)


# --------------------------------------------------------------------------- steps


@pytest.mark.parametrize(
    "kw,index,cost,upper",
    [
        (0.0, 0, 125, 2),
        (1.99, 0, 125, 2),
        (2.0, 1, 190, 5),  # boundary counts in the higher step
        (4.99, 1, 190, 5),
        (7.0, 2, 300, 10),
        (24.0, 5, 630, 25),
        (25.0, 6, 740, 30),
        (31.0, 7, 850, 35),
    ],
)
def test_step_for(kw, index, cost, upper):
    s = PeakConfig().step_for(kw)
    assert (s.index, s.cost, s.upper_kw) == (index, cost, upper)


def test_peak_config_options_roundtrip():
    cfg = PeakConfig(enabled=False, margin_kw=0.5, steps=[(3.0, 100.0), (6.0, 200.0)])
    again = PeakConfig.from_options(cfg.as_dict())
    assert again == cfg
    assert PeakConfig.from_options(None) == PeakConfig()
    assert again.step_for(7).cost == 310  # +110 per 5 kW beyond the table


# --------------------------------------------------------------------------- month state


def test_monthly_peaks_and_top3():
    data = {
        datetime(2026, 9, 30, 18, tzinfo=TZ): 9.0,  # previous month, ignored
        datetime(2026, 10, 1, 7, tzinfo=TZ): 3.0,
        datetime(2026, 10, 1, 18, tzinfo=TZ): 4.5,
        datetime(2026, 10, 2, 8, tzinfo=TZ): 5.5,
        datetime(2026, 10, 3, 8, tzinfo=TZ): 2.0,
        datetime(2026, 10, 4, 8, tzinfo=TZ): 4.0,
    }
    peaks = monthly_peaks(data)
    assert peaks == [
        (date(2026, 10, 1), 4.5),
        (date(2026, 10, 2), 5.5),
        (date(2026, 10, 3), 2.0),
        (date(2026, 10, 4), 4.0),
    ]
    assert top3_average(peaks) == pytest.approx((5.5 + 4.5 + 4.0) / 3)
    assert monthly_peaks(data, (2026, 9)) == [(date(2026, 9, 30), 9.0)]
    assert monthly_peaks({}) == []
    assert top3_average([]) == 0.0
    assert top3_average([6.0]) == 6.0


# --------------------------------------------------------------------------- caps


def days(*vals):
    return [(date(2026, 10, i + 1), v) for i, v in enumerate(vals)]


def test_cap_stays_below_current_step():
    # top-3 average 4.17 -> step 2–5 kW; stay under 5 - 0.3
    assert cap_for_hour(days(4.0, 4.0, 4.5), 0.0, PeakConfig()) == 4.7


def test_cap_when_top_days_already_above_limit():
    # (x + 4.9 + 4.9) / 3 <= 4.7  ->  x <= 4.3
    assert cap_for_hour(days(4.9, 4.9, 4.0), 0.0, PeakConfig()) == pytest.approx(4.3)


def test_cap_never_below_third_peak_or_today_or_minimum():
    # third highest day is 4.6: going up to it changes nothing
    assert cap_for_hour(days(5.0 - 0.01, 4.95, 4.6), 0.0, PeakConfig()) == pytest.approx(4.6)
    # today already peaked at 4.5: lower caps cannot undo it
    assert cap_for_hour(days(4.9, 4.9, 4.0), 4.5, PeakConfig()) == pytest.approx(4.5)
    # a tiny house would be capped at 1.7 for step 0–2, but never below min_cap_kw
    assert cap_for_hour(days(1.0, 1.0, 1.0), 0.0, PeakConfig()) == 3.0
    assert cap_for_hour(days(1.0, 1.0, 1.0), 0.0, PeakConfig(min_cap_kw=0)) == pytest.approx(1.7)


def test_cap_in_higher_step():
    assert cap_for_hour(days(6.0, 6.5, 7.0), 0.0, PeakConfig()) == pytest.approx(9.7)


def test_cap_ignores_today_entry_and_uses_reference_early_in_month():
    today = date(2026, 10, 3)
    peaks = days(4.9, 4.9, 8.0)  # includes today's partial value
    assert cap_for_hour(peaks, 0.0, PeakConfig(), today=today) == pytest.approx(4.3)
    # first day of the month: limit of last month's step, not the lowest step
    assert cap_for_hour([], 0.0, PeakConfig(), reference_kw=6.2) == pytest.approx(9.7)
    assert cap_for_hour([], 0.0, PeakConfig()) == 3.0  # min_cap
    # one day so far: (x + 4.0) / 2 <= 4.7 and x <= 4.7
    assert cap_for_hour(days(4.0), 0.0, PeakConfig()) == pytest.approx(4.7)
    assert cap_for_hour(days(4.9), 0.0, PeakConfig()) == pytest.approx(4.5)


def test_caps_for_plan_rolls_forward_and_into_next_month():
    now = datetime(2026, 10, 30, 10, tzinfo=TZ)
    hours = [now + timedelta(hours=i) for i in range(62)]  # into 1 November
    peaks = days(4.9, 4.9, 4.0)
    caps = caps_for_plan(hours, peaks, 0.0, now, PeakConfig())
    assert caps[now] == pytest.approx(4.3)
    assert caps[datetime(2026, 10, 31, 12, tzinfo=TZ)] == pytest.approx(4.3)
    # new month: empty, reference = Oct average 4.7 -> step 2–5
    assert caps[datetime(2026, 11, 1, 12, tzinfo=TZ)] == pytest.approx(4.7)
    assert len(caps) == len(hours)


# --------------------------------------------------------------------------- limit_plans


def zh(kw, protected=False, can=None, start=H):
    d = {"start": start, "kw": kw, "protected": protected}
    if can is not None:
        d["can_cut_kw"] = can
    return [d]


def test_limit_plans_under_cap_is_untouched():
    out = limit_plans({"a": zh(2.0)}, {H: 5.0}, {H: 1.0}, {"a": 0})
    assert out == {}


def test_limit_plans_spreads_within_priority():
    zones = {"a": zh(2.0), "b": zh(2.0), "c": zh(2.0)}
    out = limit_plans(zones, {H: 5.0}, {H: 1.0}, {"a": 0, "b": 0, "c": 1})
    assert out == {"a": {H: 1.0}, "b": {H: 1.0}}


def test_limit_plans_moves_up_priorities_and_respects_protection():
    zones = {"a": zh(2.0), "b": zh(2.0, protected=True), "c": zh(2.0), "d": zh(2.0, can=0.5)}
    # total 8 + base 1 = 9, cap 5 -> excess 4
    out = limit_plans(zones, {H: 5.0}, {H: 1.0}, {"a": 0, "d": 0, "b": 0, "c": 2})
    assert "b" not in out
    assert out["a"][H] == 2.0 and out["d"][H] == 0.5
    assert out["c"][H] == pytest.approx(1.5)


def test_limit_plans_cuts_everything_possible_when_short():
    zones = {"a": zh(2.0, can=1.0), "b": zh(2.0, protected=True)}
    out = limit_plans(zones, {H: 1.0}, {H: 1.0}, {})
    assert out == {"a": {H: 1.0}}


def test_limit_plans_proportional_to_cuttable_and_hours_without_cap():
    h2 = H + timedelta(hours=1)
    zones = {
        "a": zh(3.0, can=3.0) + zh(3.0, start=h2),
        "b": zh(3.0, can=1.0) + zh(3.0, start=h2),
    }
    out = limit_plans(zones, {H: 4.0}, {}, {})
    assert out["a"][H] == pytest.approx(1.5) and out["b"][H] == pytest.approx(0.5)
    assert h2 not in out["a"]


# --------------------------------------------------------------------------- realtime


ZONES = [
    {"zone_id": "a", "priority": 0, "kw": 2.0},
    {"zone_id": "b", "priority": 0, "kw": 1.0},
    {"zone_id": "c", "priority": 1, "kw": 2.0},
    {"zone_id": "d", "priority": 0, "kw": 3.0, "protected": True},
]


def test_realtime_shed_lowest_priority_biggest_first():
    assert realtime_shed(6.5, 5.0, ZONES) == ["a"]
    assert realtime_shed(8.0, 5.0, ZONES) == ["a", "b"]
    assert realtime_shed(10.0, 5.0, ZONES) == ["a", "b", "c"]  # d is protected
    assert realtime_shed(4.0, 5.0, ZONES) == []


def test_realtime_shed_keeps_and_adds_when_still_over():
    assert realtime_shed(5.5, 5.0, ZONES, currently_shed=["a"]) == ["a", "b"]


def test_realtime_shed_hysteresis_and_restore_order():
    # 4.5 + 2.0 would exceed 5.0 - 0.3: keep shedding
    assert realtime_shed(4.5, 5.0, ZONES, currently_shed=["a"]) == ["a"]
    assert realtime_shed(2.0, 5.0, ZONES, currently_shed=["a"]) == []
    # most important (c) restored first, a stays shed
    assert realtime_shed(2.5, 5.0, ZONES, currently_shed=["a", "c"]) == ["a"]
    # unknown zone ids are dropped
    assert realtime_shed(2.5, 5.0, ZONES, currently_shed=["zz"]) == []


def test_projected_hour_kwh():
    now = datetime(2026, 10, 19, 10, 30, tzinfo=TZ)
    assert projected_hour_kwh(2.0, 4.0, now) == pytest.approx(4.0)
    assert projected_hour_kwh(0.0, 4.0, now.replace(minute=0)) == pytest.approx(4.0)


def test_estimated_monthly_saving():
    cfg = PeakConfig()
    assert estimated_monthly_saving(6.0, 4.5, cfg) == 110
    assert estimated_monthly_saving(4.5, 4.0, cfg) == 0
