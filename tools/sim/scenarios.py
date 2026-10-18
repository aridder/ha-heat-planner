"""The scenario matrix (``python -m tools.sim`` prints its KPI table)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .harness import Fault, Scenario
from .twin import TZ


def at(y: int, m: int, d: int, h: int = 0, mi: int = 0, fold: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=TZ, fold=fold)


def matrix(days: float = 7, quick: bool = False) -> list[Scenario]:
    """All scenarios; ``quick`` shortens them for the test suite."""
    d = 2 if quick else days
    mon = at(2026, 10, 12)  # a Monday
    out = [
        Scenario("spot_support", mon, days=d, price_pattern=("normal", "cheap_night", "normal", "spike")),
        Scenario("spot_rules", mon, days=d, optimize=False, price_pattern=("normal", "spike")),
        Scenario("cold_-15_rules", mon, days=d, optimize=False, outdoor_mean=-13.0,
                 price_pattern=("spike", "normal"), forecast_error=2.0),
        Scenario("cold_-15_opt", mon, days=d, outdoor_mean=-13.0, price_pattern=("spike", "normal"), forecast_error=2.0),
        Scenario("mild_+10", mon, days=d, outdoor_mean=8.0, contract="spot", grid="none"),
        Scenario("norgespris_elvia", mon, days=d, contract="fixed", grid="elvia_2026"),
        Scenario("norgespris_flat", mon, days=d, contract="fixed", grid="none", optimize=False),
        Scenario("15min_spike", mon, days=d, contract="spot", quarter_hour=True, price_pattern=("spike", "normal")),
        Scenario("no_peak_limit", mon, days=d, peak_limit=False),
        Scenario("aggressive", mon, days=d, aggressiveness=2.0, price_pattern=("spike", "normal")),
        Scenario("holiday_away", at(2026, 10, 16), days=max(d, 4), presence="workweek",
                 holiday=(date(2026, 10, 17), date(2026, 10, 19))),
        Scenario("dst_autumn", at(2026, 10, 24, 12), days=max(d, 2), price_pattern=("spike", "normal"),
                 restarts=[(at(2026, 10, 25, 2, 30, fold=1), False)]),
        Scenario("dst_spring", at(2027, 3, 27, 12), days=max(d, 2), outdoor_mean=-2.0,
                 price_pattern=("spike", "normal")),
        Scenario("month_boundary", at(2026, 10, 30), days=max(d, 3), outdoor_mean=-8.0),
        # heating-driven peaks (no cooking spikes): staying in the 2–5 kW step is feasible
        Scenario("peak_feasible", mon, days=d, outdoor_mean=-6.0, cooking=False, contract="fixed",
                 grid="none"),
        Scenario("feedback_manual", mon, days=d,
                 feedback=[(mon + timedelta(hours=h), "stue", k) for h, k in
                           ((8, "too_cold"), (8.05, "too_cold"), (19, "too_warm"), (20, "too_cold"), (30, "too_cold"))]
                 + [(mon + timedelta(hours=9), "bad", "too_cold")],
                 manual=[(mon + timedelta(hours=10, minutes=7), "stue", 24.0),
                         (mon + timedelta(hours=33, minutes=40), "kjokken", 17.0)]),
        Scenario("faults", mon, days=d, contract="spot", price_pattern=("spike", "normal"), outdoor_mean=-6.0,
                 restarts=[(mon + timedelta(hours=1), False), (mon + timedelta(hours=8, minutes=20), False),
                           (mon + timedelta(hours=18, minutes=40), True)],
                 faults=[
                     Fault("prices_down", mon, mon + timedelta(hours=10)),
                     Fault("forecast_down", mon + timedelta(hours=12), mon + timedelta(hours=30)),
                     Fault("weather_down", mon + timedelta(hours=30), mon + timedelta(hours=34)),
                     Fault("sensor_down", mon + timedelta(hours=6), mon + timedelta(hours=14), zone="stue"),
                     Fault("sensor_down", mon + timedelta(hours=16), mon + timedelta(hours=20), zone="kjokken"),
                     Fault("meter_down", mon + timedelta(hours=17, minutes=20), mon + timedelta(hours=17, minutes=50)),
                     Fault("device_down", mon + timedelta(hours=7, minutes=30), mon + timedelta(hours=9), zone="sov"),
                 ]),
        Scenario("replan_5min", mon, days=min(d, 2), replan_minutes=5, price_pattern=("spike", "normal")),
    ]
    if not quick:
        out.append(Scenario("auto_arrival_14d", at(2026, 10, 5), days=14, auto_arrival=True))
    return out


PEAK_PAIRS = ("spot_support", "cold_-15_opt", "month_boundary", "peak_feasible")
