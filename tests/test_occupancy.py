"""Tests for arrival learning (occupancy.py)."""

from datetime import date, datetime, time, timedelta
import random
from zoneinfo import ZoneInfo

from custom_components.heat_planner.occupancy import (
    ArrivalModel,
    expected_arrival,
    find_arrivals,
    learn_arrivals,
    summary,
    warm_from_hour,
)

TZ = ZoneInfo("Europe/Oslo")
START = datetime(2026, 8, 31, tzinfo=TZ)  # a Monday


def at(day: datetime, h: int, m: int = 0) -> datetime:
    return day.replace(hour=h, minute=m)


def synthetic(weeks: int = 7, seed: int = 1):
    """Two persons. Weekdays: both leave ~07:30/08:00, first home ~15:20.
    Weekends: random short trips. Some unknown/unavailable noise."""
    rnd = random.Random(seed)
    a: list = [(START - timedelta(hours=1), "home")]
    b: list = [(START - timedelta(hours=1), "home")]
    for d in range(weeks * 7):
        day = START + timedelta(days=d)
        if day.weekday() < 5:
            a.append((at(day, 7, 30), "not_home"))
            b.append((at(day, 8, rnd.randint(0, 10)), "not_home"))
            arr = 15 * 60 + 20 + rnd.randint(-10, 15)
            a.append((at(day, arr // 60, arr % 60), "home"))
            b.append((at(day, 17, rnd.randint(0, 30)), "home"))
            if d % 4 == 0:  # flaky tracker during the day
                b.append((at(day, 12), "unavailable"))
                b.append((at(day, 12, 30), "not_home"))
        else:
            leave = rnd.randint(10, 13)
            back = leave + rnd.choice([1, 3, 5])
            a.append((at(day, leave), "not_home"))
            b.append((at(day, leave), "not_home"))
            a.append((at(day, back, rnd.randint(0, 59)), "home"))
            b.append((at(day, back, 30), "home"))
    return {"person.a": a, "person.b": b}


def test_weekday_pattern_learned():
    hist = synthetic()
    model = learn_arrivals(hist, TZ)
    monday = date(2026, 10, 19)
    exp = expected_arrival(model, monday)
    assert exp is not None
    assert time(15, 5) <= exp <= time(15, 30)
    # 6 weeks kept, at most one arrival per day
    assert model.count(0) <= 6
    assert model.count(0) >= 3


def test_short_absence_and_night_ignored():
    day = datetime(2026, 10, 5, tzinfo=TZ)
    hist = {
        "person.a": [
            (at(day, 6), "home"),
            (at(day, 9), "not_home"),
            (at(day, 10), "home"),  # 1 h absence: ignored
            (at(day, 11), "not_home"),
            (at(day, 16), "home"),  # counted
            (at(day, 20), "not_home"),
            (at(day + timedelta(days=1), 2), "home"),  # night arrival: ignored
        ]
    }
    arr = find_arrivals(hist, TZ)
    assert arr == [at(day, 16)]


def test_someone_home_means_no_arrival():
    day = datetime(2026, 10, 5, tzinfo=TZ)
    hist = {
        "person.a": [(at(day, 6), "home"), (at(day, 8), "not_home"), (at(day, 15), "home")],
        "person.b": [(at(day, 6), "home")],  # stays home all day
    }
    assert find_arrivals(hist, TZ) == []


def test_unknown_states_do_not_count():
    day = datetime(2026, 10, 5, tzinfo=TZ)
    hist = {
        "person.a": [(at(day, 6), "home"), (at(day, 8), "not_home"), (at(day, 15), "home")],
        # b's tracker is broken: says nothing, does not keep the house "occupied"
        "person.b": [(at(day, 6), "unknown"), (at(day, 9), "unavailable")],
    }
    assert find_arrivals(hist, TZ) == [at(day, 15)]
    # nothing known at all -> no arrivals
    assert find_arrivals({"person.x": [(at(day, 7), "unknown"), (at(day, 15), "home")]}, TZ) == []


def test_too_few_observations_falls_back_to_pool_then_none():
    model = ArrivalModel()
    assert expected_arrival(model, date(2026, 10, 19)) is None
    # two Mondays + one Tuesday -> Monday uses the weekday pool
    model.observations[0] = [("2026-10-05", 15 * 60), ("2026-10-12", 15 * 60 + 30)]
    model.observations[1] = [("2026-10-06", 16 * 60)]
    exp = expected_arrival(model, date(2026, 10, 19))
    assert exp is not None and time(15, 0) <= exp <= time(15, 30)
    s = summary(model, date(2026, 10, 19), lead_hours=1.5)
    assert s["source"] == "pool" and s["count"] == 3 and s["warm_from"] == 13
    # weekend pool is empty
    assert expected_arrival(model, date(2026, 10, 24)) is None
    assert summary(model, date(2026, 10, 24)) is None


def test_old_observations_dropped_and_roundtrip():
    hist = synthetic(weeks=10)
    model = learn_arrivals(hist, TZ)
    dates = [date.fromisoformat(d) for obs in model.observations.values() for d, _ in obs]
    last = max(dates)
    assert min(dates) >= last - timedelta(weeks=6)
    again = ArrivalModel.from_dict(model.as_dict())
    assert again.observations == model.observations
    d = model.as_dict()
    assert d["weekdays"]["0"]["count"] == model.count(0)
    assert ArrivalModel.from_dict(None).count(0) == 0
    assert ArrivalModel.from_dict({"observations": {"x": [], "9": [], "1": [["bad"]]}}).count(1) == 0


def test_previous_model_is_merged():
    prev = ArrivalModel()
    prev.observations[2] = [("2026-10-07", 900)]
    day = datetime(2026, 10, 14, tzinfo=TZ)
    hist = {"person.a": [(at(day, 6), "home"), (at(day, 8), "not_home"), (at(day, 15, 10), "home")]}
    model = learn_arrivals(hist, TZ, now=at(day, 20), previous=prev)
    assert model.observations[2] == [("2026-10-07", 900), ("2026-10-14", 910)]


def test_warm_from_hour():
    assert warm_from_hour(time(15, 20), 1.5) == 13
    assert warm_from_hour(time(15, 20), 0) == 15
    assert warm_from_hour(time(8, 0), 1) == 10  # clamp low
    assert warm_from_hour(time(22, 0), 1) == 18  # clamp high
    assert warm_from_hour(None, 2) == 13
