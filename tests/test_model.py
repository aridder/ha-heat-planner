"""Room model: sun position, solar gain, dead time, fitting and fit quality."""

from datetime import datetime, timedelta, timezone
import math
import random
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from custom_components.heat_planner.learning import samples_from_history
from custom_components.heat_planner.planner import (
    PlanHour,
    Sample,
    ThermalModel,
    ZoneConfig,
    _simulate,
    blend_models,
    fit_thermal,
    model_quality,
    model_rmse,
)
from custom_components.heat_planner.solar import (
    clear_sky_factor,
    cloud_attenuation,
    forecast_solar,
    solar_factor,
    sun_elevation,
)

OSLO = ZoneInfo("Europe/Oslo")
LAT, LON = 59.91, 10.75
UTC = timezone.utc


# --------------------------------------------------------------------------- sun


def test_sun_position_oslo():
    june_noon = datetime(2026, 6, 21, 13, 20, tzinfo=OSLO)  # solar noon ≈ 13:20 CEST
    assert sun_elevation(june_noon, LAT, LON) == pytest.approx(53.5, abs=1.0)
    assert clear_sky_factor(june_noon, LAT, LON) > 0.7
    assert clear_sky_factor(datetime(2026, 6, 21, 1, 0, tzinfo=OSLO), LAT, LON) == 0.0
    assert clear_sky_factor(datetime(2026, 12, 21, 0, 0, tzinfo=OSLO), LAT, LON) == 0.0
    dec = clear_sky_factor(datetime(2026, 12, 21, 12, 20, tzinfo=OSLO), LAT, LON)
    assert 0.0 < dec < 0.15  # sun ~6.6° above the horizon
    # morning lower than noon
    assert clear_sky_factor(datetime(2026, 6, 21, 8, 0, tzinfo=OSLO), LAT, LON) < 0.7


def test_cloud_attenuation():
    when = datetime(2026, 6, 21, 13, 20, tzinfo=OSLO)
    clear = clear_sky_factor(when, LAT, LON)
    assert solar_factor(when, LAT, LON, 0) == pytest.approx(clear)
    assert solar_factor(when, LAT, LON, 100) == pytest.approx(clear * 0.25)
    assert cloud_attenuation(50) == pytest.approx(1 - 0.75 * 0.5**3.4)
    assert cloud_attenuation(None) == pytest.approx(0.6)
    assert solar_factor(when, LAT, LON, 30) > solar_factor(when, LAT, LON, 80)


def test_forecast_solar_hourly_and_condition_fallback():
    base = datetime(2026, 6, 21, 10, 0, tzinfo=OSLO)
    items = [
        {"datetime": base.isoformat(), "cloud_coverage": 0, "condition": "sunny"},
        {"datetime": (base + timedelta(hours=1)).isoformat(), "condition": "cloudy"},
        {"datetime": base + timedelta(hours=2), "condition": "something-new"},
        {"datetime": (base + timedelta(hours=14)).isoformat(), "condition": "clear-night"},
        {"bad": True},
    ]
    f = forecast_solar(items, LAT, LON)
    assert f[base] > 0.6
    # cloudy ≈ 90 % cover lets about half of the clear-sky sun through
    assert f[base + timedelta(hours=1)] < 0.6 * clear_sky_factor(base + timedelta(minutes=90), LAT, LON)
    # unknown condition → clear sky × 0.6, and spread over the gap until the next item
    assert base + timedelta(hours=5) in f
    assert f[base + timedelta(hours=14)] == 0.0  # midnight
    assert all(0.0 <= v <= 1.0 for v in f.values())
    assert forecast_solar(None, LAT, LON) == {}


# --------------------------------------------------------------------------- simulate


def _slots(start: datetime, targets: list[float | None]) -> list[PlanHour]:
    return [
        PlanHour(start=start + timedelta(hours=i), price=1.0, level="normal",
                 scheduled=t or 0, target=t, mode="normal", reasons=[])
        for i, t in enumerate(targets)
    ]


def _zone(**thermal) -> ZoneConfig:
    return ZoneConfig("z", "Z", comfort=21.0, thermal=ThermalModel(**{"gross_rate": 1.5, "tau_hours": 30, **thermal}))


DAY = datetime(2026, 4, 20, 0, 0, tzinfo=OSLO)


def test_simulate_defaults_unchanged_by_empty_solar():
    a, b = _slots(DAY, [18.0] * 6 + [21.0] * 18), _slots(DAY, [18.0] * 6 + [21.0] * 18)
    _simulate(_zone(), a, 18.0, {}, 0.0)
    _simulate(_zone(), b, 18.0, {}, 0.0, solar={s.start: 0.8 for s in b})  # gain 0
    assert [(s.predicted, s.duty) for s in a] == [(s.predicted, s.duty) for s in b]
    assert a[6].predicted > 18.5 and a[-1].predicted == 21.0


def test_lag_delays_warm_up():
    targets = [18.0] * 4 + [21.0] * 12
    fast, slow = _slots(DAY, targets), _slots(DAY, targets)
    _simulate(_zone(), fast, 18.0, {}, 0.0)
    _simulate(_zone(lag_hours=2.0), slow, 18.0, {}, 0.0)
    # same commanded heat starts at once, but the room warms ~2 h later
    assert slow[4].duty == pytest.approx(fast[4].duty, abs=0.05)
    assert slow[4].predicted < fast[4].predicted - 0.5
    assert slow[5].predicted < fast[5].predicted
    assert slow[-1].predicted == pytest.approx(21.0, abs=0.3)  # still reaches the target


def test_lag_percent_zone():
    z = ZoneConfig("p", "P", mode="percent", thermal=ThermalModel(gross_rate=1.5, tau_hours=30, lag_hours=1.0))
    slots = _slots(DAY, [0.0] * 2 + [100.0] * 4)
    _simulate(z, slots, 18.0, {}, 18.0)
    assert slots[2].predicted == pytest.approx(18.0)  # heat not felt yet
    assert slots[3].predicted > 19.0


def test_solar_raises_predicted_temperature_at_noon():
    targets = [None] * 24  # heating off: free-running room
    dark, sunny = _slots(DAY, targets), _slots(DAY, targets)
    sun = {s.start: clear_sky_factor(s.start + timedelta(minutes=30), LAT, LON) for s in sunny}
    z = _zone(solar_gain=1.0)
    _simulate(z, dark, 20.0, {}, 0.0)
    _simulate(z, sunny, 20.0, {}, 0.0, solar=sun)
    assert sunny[3].predicted == dark[3].predicted  # night: no sun
    assert sunny[14].predicted > dark[14].predicted + 1.0
    # sun also lets a setpoint zone heat less around noon
    a, b = _slots(DAY, [21.0] * 24), _slots(DAY, [21.0] * 24)
    _simulate(z, a, 21.0, {}, 0.0)
    _simulate(z, b, 21.0, {}, 0.0, solar=sun)
    assert b[13].duty < a[13].duty


# --------------------------------------------------------------------------- fitting


def synth(
    *, tau=30.0, gross=1.5, lag=1.5, gain=0.8, days=21, noise=0.0, seed=1,
    start=datetime(2026, 3, 10, tzinfo=UTC),
) -> list[Sample]:
    """History from a known room: 5-minute physics, 30-minute samples."""
    rng = random.Random(seed)
    model = ThermalModel(gross_rate=gross, tau_hours=tau, solar_gain=gain)
    slots = days * 48
    flags: list[bool] = []
    while len(flags) < slots + 12:
        flags += [True] * rng.randint(4, 8) + [False] * rng.randint(6, 14)
    clouds = [rng.choice([0, 20, 50, 80, 100]) for _ in range(days + 1)]
    lag_slots = int(round(lag * 2))
    temp, samples = 20.0, []
    for k in range(slots):
        t = start + timedelta(minutes=30 * k)
        cloud = clouds[k // 48]
        out = -2 + 4 * math.sin(2 * math.pi * (k % 48) / 48)
        samples.append(Sample(t, temp + rng.gauss(0, noise), out, flags[k + 12],
                              solar=solar_factor(t, LAT, LON, cloud)))
        duty = 1.0 if flags[k + 12 - lag_slots] else 0.0
        for m in range(6):
            tm = t + timedelta(minutes=5 * m)
            temp = model.step(temp, duty, out, 5 / 60, solar_factor(tm, LAT, LON, cloud))
    return samples


def test_fit_recovers_tau_gross_lag_and_solar():
    samples = synth()
    model = fit_thermal(samples, ThermalModel())
    assert model.learned
    assert model.tau_hours == pytest.approx(30, rel=0.2)
    assert model.gross_rate == pytest.approx(1.5, rel=0.15)
    assert model.lag_hours == 1.5
    assert model.solar_gain == pytest.approx(0.8, rel=0.25)
    assert model.rmse is not None and model.rmse < 0.1
    assert model_quality(model) == "good"


def test_fit_without_lag_or_sun_finds_none():
    model = fit_thermal(synth(lag=0.0, gain=0.0), ThermalModel(lag_hours=1.0, solar_gain=0.5))
    assert model.lag_hours == 0.0
    assert model.solar_gain < 0.1


def test_rmse_reflects_noise_and_wrong_models():
    samples = synth()
    good = fit_thermal(samples)
    wrong = ThermalModel(gross_rate=good.gross_rate, tau_hours=good.tau_hours)  # no lag, no sun
    assert model_rmse(wrong, samples) > good.rmse
    assert model_rmse(good, samples[:5]) is None
    noisy = fit_thermal(synth(noise=0.25, seed=3))
    assert 0.15 < noisy.rmse < 0.6


def test_quality_labels():
    assert model_quality(ThermalModel()) == "unknown"
    assert model_quality(None) == "unknown"
    assert model_quality(ThermalModel(rmse=0.1)) == "good"
    assert model_quality(ThermalModel(rmse=0.45)) == "fair"
    assert model_quality(ThermalModel(rmse=1.2)) == "poor"


def test_poor_fit_blends_with_prior():
    prior = ThermalModel(gross_rate=1.0, tau_hours=60.0)
    samples = synth(noise=1.0, seed=5)
    raw = fit_thermal(samples, prior, blend_poor=False)
    assert model_quality(raw) == "poor"
    model = fit_thermal(samples, prior)
    assert model.learned and model.rmse == raw.rmse
    assert model_quality(model) == "poor"  # the UI still shows low confidence
    assert model.gross_rate == pytest.approx((raw.gross_rate + 1.0) / 2, abs=0.002)
    assert model.tau_hours == pytest.approx((raw.tau_hours + 60.0) / 2, abs=0.1)
    assert blend_models(raw, prior, 1.0).tau_hours == raw.tau_hours


def test_fit_falls_back_without_data():
    prior = ThermalModel(gross_rate=0.8, tau_hours=50, lag_hours=1.0, solar_gain=0.3)
    model = fit_thermal([], prior)
    assert not model.learned and model.rmse is None
    assert (model.gross_rate, model.tau_hours, model.lag_hours, model.solar_gain) == (0.8, 50, 1.0, 0.3)
    assert model_quality(model) == "unknown"


# --------------------------------------------------------------------------- history


def _st(when: datetime, state, **attrs):
    return SimpleNamespace(state=state, attributes=attrs, last_updated=when)


def test_samples_from_history_duty_outdoor_and_sun():
    t0 = datetime(2026, 6, 21, 9, 0, tzinfo=UTC)
    hist = {
        "climate.stue": [
            _st(t0, "heat", current_temperature=20.0, hvac_action="idle"),
            _st(t0 + timedelta(minutes=10), "heat", current_temperature=20.1, hvac_action="heating"),
            _st(t0 + timedelta(minutes=25), "heat", current_temperature=20.3, hvac_action="idle"),
            _st(t0 + timedelta(hours=2), "heat", current_temperature=20.5, hvac_action="idle"),
        ],
        "sensor.stue": [_st(t0, "20.4"), _st(t0 + timedelta(hours=1), "unavailable")],
        "weather.home": [
            _st(t0, "sunny", temperature=12.0, cloud_coverage=0),
            _st(t0 + timedelta(hours=1), "cloudy", temperature=13.0),
        ],
    }
    s = samples_from_history(hist, "climate.stue", "sensor.stue", "weather.home", LAT, LON,
                             start=t0, end=t0 + timedelta(hours=2))
    assert len(s) == 5
    assert s[0].room == 20.4 and s[0].outdoor == 12.0
    assert s[0].duty == pytest.approx(0.5, abs=0.01)  # heating 10–25 min of the first 30
    assert s[1].duty == 0.0
    assert s[0].solar > 0.6  # clear sky, sun high
    # "cloudy" condition without cloud_coverage
    assert s[2].solar < 0.6 * clear_sky_factor(s[2].when, LAT, LON)
    assert s[2].outdoor == 13.0

    # no outdoor/weather history, no room sensor: climate attribute, default outdoor, clear sky × 0.6
    s2 = samples_from_history(hist, "climate.stue", None, None, LAT, LON)
    assert s2[0].room == 20.0 and s2[0].outdoor == 5.0
    assert s2[0].solar == pytest.approx(clear_sky_factor(t0, LAT, LON) * 0.6, abs=0.002)
    # without a location there is no sun, and a state list works in place of an entity id
    s3 = samples_from_history(hist, "climate.stue", None, hist["weather.home"], None, None)
    assert all(x.solar == 0.0 for x in s3) and s3[0].outdoor == 12.0
    assert samples_from_history({}, "climate.x", None, None, LAT, LON) == []
