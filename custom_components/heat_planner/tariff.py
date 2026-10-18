"""What a household actually pays per kWh, hour by hour (pure Python, no HA imports).

The price source delivers a *spot* price. What matters for planning is the
*effective* price: the energy part after VAT and any state support (or a fixed
price contract), plus the grid energy charge ("energiledd") that depends on the
time of day, plus any other per-kWh add-on.

Assumptions (documented, deliberately simple):

* ``spot`` values are the Nord Pool spot price **excluding VAT** in currency per
  kWh (e.g. NOK/kWh), as delivered by the Nord Pool integration. If the source
  already includes VAT (Tibber, the HACS nordpool sensor with VAT enabled), set
  ``spot_includes_vat=True`` and VAT is removed before support is calculated.
* Hours are timezone-aware *local* datetimes (Europe/Oslo); weekday and hour are
  read directly from the datetime.
* "spot_support" (strømstøtte 2026): the state covers ``support_rate`` (90 %) of
  the hourly spot price above ``support_threshold`` (0.77 ex VAT). VAT is then
  applied to what remains. The monthly volume cap (5000 kWh) is ignored: a
  household with floor heating stays well below it; ``support_cap_kwh`` is kept
  for information only.
* "fixed" (Norgespris): a flat ``fixed_price`` per kWh **including VAT**; the
  spot price is ignored (and may be missing).
* Grid energy charge: ``grid_day`` on weekdays in ``[day_start, day_end)``,
  otherwise ``grid_night``; weekends count as night when ``weekend_is_night``.
  Public holidays are not modelled. Grid prices are **including** taxes and VAT.
* ``extra``: any other per-kWh add-on (supplier markup, …), including VAT.
  Backwards compatibility: the old flat ``price_extra`` option maps to the
  "none" preset (no VAT, no grid) with ``extra=price_extra``, which reproduces
  the previous ``spot + price_extra`` exactly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any

MODE_SPOT = "spot"
MODE_SPOT_SUPPORT = "spot_support"
MODE_FIXED = "fixed"
MODES = (MODE_SPOT, MODE_SPOT_SUPPORT, MODE_FIXED)


@dataclass
class TariffConfig:
    """How the household is billed per kWh."""

    mode: str = MODE_SPOT
    fixed_price: float = 0.50  # per kWh incl VAT (Norgespris 2026: 40 øre ex VAT)
    support_threshold: float = 0.77  # per kWh ex VAT
    support_rate: float = 0.9
    support_cap_kwh: float = 5000.0  # informational, not applied per hour
    vat: float = 0.25
    spot_includes_vat: bool = False
    grid_day: float = 0.0  # per kWh incl taxes/VAT
    grid_night: float = 0.0
    day_start: int = 6
    day_end: int = 22
    weekend_is_night: bool = True
    extra: float = 0.0  # per kWh incl VAT
    grid_name: str = ""  # e.g. "Elvia", used by describe()
    preset: str = "custom"

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown tariff mode: {self.mode}")

    @property
    def has_grid(self) -> bool:
        return bool(self.grid_day or self.grid_night)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_options(
        cls, data: dict[str, Any] | None, *, legacy_extra: float | None = None
    ) -> "TariffConfig":
        """Build from an options dict (e.g. ``options["tariff"]``).

        ``data`` may name a ``preset`` whose values are applied first; every other
        known key overrides it; unknown keys are ignored. With no data at all the
        legacy behaviour is reproduced: spot + ``legacy_extra`` (no VAT, no grid).
        """
        if not data:
            return cls(**{**PRESETS["none"], "extra": float(legacy_extra or 0.0)})
        preset = data.get("preset") or "custom"
        values: dict[str, Any] = dict(PRESETS.get(preset, {}))
        known = {f.name for f in fields(cls)}
        values.update({k: v for k, v in data.items() if k in known and v is not None})
        values["preset"] = preset
        return cls(**values)


# Presets describe the grid company / tax setting. The billing ``mode`` is chosen
# separately (and may be passed alongside the preset in ``from_options``).
PRESETS: dict[str, dict[str, Any]] = {
    "elvia_2026": {
        "grid_day": 0.364,
        "grid_night": 0.264,
        "day_start": 6,
        "day_end": 22,
        "weekend_is_night": True,
        "vat": 0.25,
        "support_threshold": 0.77,
        "support_rate": 0.9,
        "fixed_price": 0.50,
        "grid_name": "Elvia",
        "preset": "elvia_2026",
    },
    # Legacy: plain spot + flat add-on, no VAT or grid modelling.
    "none": {
        "mode": MODE_SPOT,
        "vat": 0.0,
        "grid_day": 0.0,
        "grid_night": 0.0,
        "grid_name": "",
        "preset": "none",
    },
}


def is_day_hour(when: datetime, cfg: TariffConfig) -> bool:
    """True when the grid day rate applies to the hour starting at ``when``."""
    if cfg.weekend_is_night and when.weekday() >= 5:
        return False
    return cfg.day_start <= when.hour < cfg.day_end


def grid_price(when: datetime, cfg: TariffConfig) -> float:
    """Grid energy charge (energiledd) per kWh incl VAT for that hour."""
    return cfg.grid_day if is_day_hour(when, cfg) else cfg.grid_night


def energy_price(spot: float | None, cfg: TariffConfig) -> float | None:
    """Energy part per kWh incl VAT, after support / fixed price.

    Returns ``None`` when a spot based mode has no spot price.
    """
    if cfg.mode == MODE_FIXED:
        return cfg.fixed_price
    if spot is None:
        return None
    ex_vat = spot / (1 + cfg.vat) if cfg.spot_includes_vat and cfg.vat else spot
    if cfg.mode == MODE_SPOT_SUPPORT:
        ex_vat -= max(0.0, ex_vat - cfg.support_threshold) * cfg.support_rate
    return ex_vat * (1 + cfg.vat)


def effective_price(when: datetime, spot: float | None, cfg: TariffConfig) -> float | None:
    """Everything paid per kWh in the hour starting at ``when``."""
    energy = energy_price(spot, cfg)
    if energy is None:
        return None
    return round(energy + grid_price(when, cfg) + cfg.extra, 5)


def effective_prices(
    spot: dict[datetime, float], cfg: TariffConfig
) -> dict[datetime, float]:
    """Map hourly spot prices (ex VAT, per kWh) to effective household prices."""
    out: dict[datetime, float] = {}
    for h, p in sorted(spot.items()):
        v = effective_price(h, p, cfg)
        if v is not None:
            out[h] = v
    return out


def fixed_prices(hours: list[datetime], cfg: TariffConfig) -> dict[datetime, float]:
    """Effective prices without any spot data (only meaningful in fixed mode).

    Lets the planner work under Norgespris even when no price source exists.
    """
    out: dict[datetime, float] = {}
    for h in hours:
        v = effective_price(h, None, cfg)
        if v is not None:
            out[h] = v
    return out


_LABELS = {
    "nb": {
        MODE_SPOT: "Spotpris",
        MODE_SPOT_SUPPORT: "Spotpris med strømstøtte",
        MODE_FIXED: "Norgespris",
        "grid": "nettleie",
        "extra": "påslag",
    },
    "en": {
        MODE_SPOT: "Spot price",
        MODE_SPOT_SUPPORT: "Spot price with electricity support",
        MODE_FIXED: "Norgespris (fixed price)",
        "grid": "grid tariff",
        "extra": "add-on",
    },
}


def describe(cfg: TariffConfig, lang: str = "en") -> str:
    """Short human label, e.g. "Norgespris + Elvia nettleie"."""
    code = (lang or "en")[:2].lower()
    labels = _LABELS["nb"] if code in ("nb", "no", "nn") else _LABELS["en"]
    parts = [labels[cfg.mode]]
    if cfg.has_grid:
        parts.append(f"{cfg.grid_name} {labels['grid']}".strip())
    if cfg.extra:
        parts.append(labels["extra"])
    return " + ".join(parts)
