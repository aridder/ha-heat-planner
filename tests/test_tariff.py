"""Tests for the tariff module (effective household prices) and the min_spread guard."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.heat_planner.planner import (
    LEVEL_EXPENSIVE,
    LEVEL_NORMAL,
    PriceRules,
    classify_prices,
)
from custom_components.heat_planner.tariff import (
    MODE_FIXED,
    MODE_SPOT,
    MODE_SPOT_SUPPORT,
    PRESETS,
    TariffConfig,
    describe,
    effective_price,
    effective_prices,
    energy_price,
    fixed_prices,
    grid_price,
    is_day_hour,
)

TZ = ZoneInfo("Europe/Oslo")
MON = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)  # Monday
SAT = datetime(2026, 10, 24, 0, 0, tzinfo=TZ)


def elvia(mode: str, **kw) -> TariffConfig:
    return TariffConfig.from_options({"preset": "elvia_2026", "mode": mode, **kw})


def test_preset_values():
    cfg = elvia(MODE_SPOT)
    assert cfg.grid_day == 0.364 and cfg.grid_night == 0.264
    assert cfg.vat == 0.25 and cfg.day_start == 6 and cfg.day_end == 22
    assert cfg.grid_name == "Elvia" and cfg.preset == "elvia_2026"
    assert set(PRESETS) >= {"elvia_2026", "none"}


@pytest.mark.parametrize(
    "when,day",
    [
        (MON.replace(hour=5), False),
        (MON.replace(hour=6), True),
        (MON.replace(hour=21), True),
        (MON.replace(hour=22), False),
        (SAT.replace(hour=12), False),
        (SAT.replace(hour=12) + timedelta(days=1), False),  # Sunday
    ],
)
def test_day_night_and_weekend(when, day):
    cfg = elvia(MODE_SPOT)
    assert is_day_hour(when, cfg) is day
    assert grid_price(when, cfg) == (0.364 if day else 0.264)


def test_weekend_as_day_when_configured():
    cfg = elvia(MODE_SPOT, weekend_is_night=False)
    assert grid_price(SAT.replace(hour=12), cfg) == 0.364


def test_plain_spot_adds_vat_and_grid():
    cfg = elvia(MODE_SPOT)
    assert effective_price(MON.replace(hour=8), 1.0, cfg) == pytest.approx(1.25 + 0.364)
    assert effective_price(MON.replace(hour=2), 1.0, cfg) == pytest.approx(1.25 + 0.264)


def test_strømstøtte_above_threshold():
    cfg = elvia(MODE_SPOT_SUPPORT)
    # 90 % of (1.00 - 0.77) is covered, VAT on the remainder
    expected = (1.0 - 0.9 * 0.23) * 1.25
    assert energy_price(1.0, cfg) == pytest.approx(expected)
    assert effective_price(MON.replace(hour=8), 1.0, cfg) == pytest.approx(expected + 0.364)


def test_strømstøtte_below_threshold_is_plain_spot():
    cfg = elvia(MODE_SPOT_SUPPORT)
    assert energy_price(0.5, cfg) == pytest.approx(0.625)
    assert energy_price(0.77, cfg) == pytest.approx(0.77 * 1.25)


def test_strømstøtte_flattens_peaks():
    spot = elvia(MODE_SPOT)
    sup = elvia(MODE_SPOT_SUPPORT)
    spread_spot = energy_price(3.0, spot) - energy_price(1.0, spot)
    spread_sup = energy_price(3.0, sup) - energy_price(1.0, sup)
    assert spread_sup == pytest.approx(spread_spot * 0.1)


def test_spot_including_vat_is_converted():
    a = elvia(MODE_SPOT_SUPPORT)
    b = elvia(MODE_SPOT_SUPPORT, spot_includes_vat=True)
    assert energy_price(1.25, b) == pytest.approx(energy_price(1.0, a))


def test_norgespris_is_flat_plus_grid_and_ignores_spot():
    cfg = elvia(MODE_FIXED)
    assert effective_price(MON.replace(hour=8), 5.0, cfg) == pytest.approx(0.864)
    assert effective_price(MON.replace(hour=8), None, cfg) == pytest.approx(0.864)
    assert effective_price(SAT.replace(hour=8), 0.1, cfg) == pytest.approx(0.764)


def test_extra_is_added():
    cfg = elvia(MODE_FIXED, extra=0.05)
    assert effective_price(MON.replace(hour=8), None, cfg) == pytest.approx(0.914)


def test_effective_prices_maps_all_hours_and_skips_missing():
    spot = {MON + timedelta(hours=i): 1.0 for i in range(24)}
    out = effective_prices(spot, elvia(MODE_SPOT))
    assert list(out) == sorted(spot)
    assert out[MON + timedelta(hours=12)] == pytest.approx(1.614)
    assert out[MON + timedelta(hours=23)] == pytest.approx(1.514)
    assert effective_price(MON, None, elvia(MODE_SPOT)) is None


def test_fixed_prices_without_spot():
    hours = [MON + timedelta(hours=i) for i in range(24)]
    out = fixed_prices(hours, elvia(MODE_FIXED))
    assert len(out) == 24 and {round(v, 4) for v in out.values()} == {0.864, 0.764}
    assert fixed_prices(hours, elvia(MODE_SPOT)) == {}


def test_legacy_price_extra_is_reproduced():
    cfg = TariffConfig.from_options(None, legacy_extra=0.45)
    assert cfg.mode == MODE_SPOT and not cfg.has_grid and cfg.vat == 0
    spot = {MON + timedelta(hours=i): 0.3 * i for i in range(24)}
    out = effective_prices(spot, cfg)
    assert all(out[h] == pytest.approx(spot[h] + 0.45) for h in spot)
    assert TariffConfig.from_options({}).extra == 0.0


def test_options_roundtrip_and_overrides():
    cfg = elvia(MODE_SPOT_SUPPORT, grid_day=0.4, unknown_key=1)
    assert cfg.grid_day == 0.4 and cfg.grid_night == 0.264
    again = TariffConfig.from_options(cfg.as_dict())
    assert again == cfg


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        TariffConfig(mode="banana")


def test_describe():
    assert describe(elvia(MODE_FIXED), "nb") == "Norgespris + Elvia nettleie"
    assert describe(elvia(MODE_SPOT_SUPPORT), "en") == (
        "Spot price with electricity support + Elvia grid tariff"
    )
    assert describe(elvia(MODE_SPOT), "no") == "Spotpris + Elvia nettleie"
    assert describe(TariffConfig.from_options(None, legacy_extra=0.45), "nb") == "Spotpris + påslag"
    assert describe(TariffConfig(mode=MODE_SPOT, grid_day=0.3), "en") == "Spot price + grid tariff"
    assert describe(TariffConfig(), "de") == "Spot price"


# --------------------------------------------------------------------------- min_spread guard


TYPICAL = [1.2, 1.2, 1.1, 1.1, 1.1, 1.2, 1.4, 1.6, 1.8, 1.5, 1.4, 1.3,
           1.1, 1.0, 1.0, 1.2, 1.5, 1.7, 1.9, 1.9, 1.6, 1.5, 1.4, 1.3]


def test_min_spread_makes_norgespris_days_normal():
    hours = [MON + timedelta(hours=i) for i in range(48)]
    eff = fixed_prices(hours, elvia(MODE_FIXED))  # spread 0.10 (day vs night grid)
    levels = classify_prices(eff, PriceRules())
    assert set(levels.values()) == {LEVEL_NORMAL}
    # Without the guard the grid's day/night step would be treated as price signal
    levels = classify_prices(eff, PriceRules(min_spread=0.0))
    assert LEVEL_EXPENSIVE in levels.values()


def test_min_spread_is_per_day():
    flat = {MON + timedelta(hours=i): 1.0 + 0.001 * i for i in range(24)}
    varied = {MON + timedelta(days=1, hours=i): v for i, v in enumerate(TYPICAL)}
    levels = classify_prices({**flat, **varied}, PriceRules())
    assert {levels[h] for h in flat} == {LEVEL_NORMAL}
    assert {h.hour for h in varied if levels[h] == LEVEL_EXPENSIVE} == {8, 18, 19}
