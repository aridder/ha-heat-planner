"""Regressions for the engine changes made after the strategy benchmark (tools/bench).

* Percent zones (regulators): price shifting moves heat instead of removing it (the
  bathroom ended twice as far below its comfort as with a plain schedule).
* The benchmark itself runs and is reproducible.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.heat_planner.planner import (
    MODE_PERCENT,
    R_EXPENSIVE,
    PriceRules,
    ZoneConfig,
    build_plan,
    default_schedule,
)

TZ = ZoneInfo("Europe/Oslo")
DAY = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)
TYPICAL = [1.2, 1.2, 1.1, 1.1, 1.1, 1.2, 1.4, 1.6, 1.8, 1.5, 1.4, 1.3,
           1.1, 1.0, 1.0, 1.2, 1.5, 1.7, 1.9, 1.9, 1.6, 1.5, 1.4, 1.3]


def test_percent_zone_price_shifting_keeps_the_days_heat():
    z = ZoneConfig(zone_id="bad", name="Bad", mode=MODE_PERCENT, comfort=0.0, min_value=0.0, max_value=100.0,
                   away_offset=-20.0, price_shift=15.0, preheat=10.0)
    sched = default_schedule(MODE_PERCENT)
    prices = {DAY + timedelta(hours=i): v for i, v in enumerate(TYPICAL)}
    p = build_plan(z, sched, {}, prices, PriceRules(), DAY, 24)
    assert any(R_EXPENSIVE in h.reasons for h in p.hours)  # still shifts away from the peaks
    planned = sum(h.target or 0.0 for h in p.hours)
    scheduled = sum(h.scheduled for h in p.hours)
    assert planned >= scheduled - 0.5
    # and puts it in cheaper hours: the price-weighted heat goes down
    assert sum((h.target or 0.0) * h.price for h in p.hours) < sum(h.scheduled * h.price for h in p.hours)


def test_benchmark_smoke():
    from tools.bench.ours import Ours
    from tools.bench.sim import Case, run
    from tools.bench.strategies import Emhass, Fixed, PriceAnalyzer, Sparegris

    case = Case("oct", 1, "spot_support", days=1)
    out = {s.name: run(s, case) for s in (Fixed(), PriceAnalyzer(), Sparegris(), Emhass(mode="power"), Ours("rules"))}
    for r in out.values():
        b = r["bills"]["spot_support"]
        assert b["cost"] > 0 and r["heat_kwh"] > 0 and r["deficit"] >= r["deficit_band"] >= 0
    # price-blind strategies are billed under every contract from the same run
    assert set(out["fixed_schedule"]["bills"]) == {"spot", "spot_support", "norgespris"}
    # deterministic
    again = run(Fixed(), case)
    assert again["bills"] == out["fixed_schedule"]["bills"]
