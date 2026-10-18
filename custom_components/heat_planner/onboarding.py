"""Make setup effortless: find what exists, explain what is missing, set up what we can."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

SETUP_NORDPOOL = "setup_nordpool"
OTHER_SENSOR = "other_sensor"
TIBBER = "tibber"

_PRICE_ATTRS = ("raw_today", "prices_today", "today", "prices", "data")


def _nb(hass: HomeAssistant) -> bool:
    return (hass.config.language or "en").lower().startswith(("nb", "no", "nn"))


def guess_nordpool_area(hass: HomeAssistant) -> tuple[str, str]:
    """Best guess of Nord Pool area and currency from the home location."""
    country = (hass.config.country or "").upper()
    lat, lon = hass.config.latitude or 0.0, hass.config.longitude or 0.0
    if country == "NO":
        if lat >= 65.0:
            area = "NO4"
        elif lat >= 62.0 or (lat >= 61.5 and lon >= 9.0):
            area = "NO3"
        elif lon < 7.6 and lat >= 59.6:
            area = "NO5"
        elif lat < 59.8 and lon < 9.6:
            area = "NO2"
        else:
            area = "NO1"
        return area, "NOK"
    if country == "SE":
        area = "SE1" if lat >= 66 else "SE2" if lat >= 63 else "SE3" if lat >= 56.5 else "SE4"
        return area, "SEK"
    if country == "DK":
        return ("DK1" if lon < 11.0 else "DK2"), "DKK"
    eur = {"FI": "FI", "EE": "EE", "LV": "LV", "LT": "LT", "DE": "DE-LU", "LU": "DE-LU",
           "NL": "NL", "BE": "BE", "FR": "FR", "AT": "AT"}
    if country in eur:
        return eur[country], "EUR"
    if country == "PL":
        return "PL", "PLN"
    return "NO1", hass.config.currency or "EUR"


@dataclass
class PriceOption:
    value: str
    label: str
    ready: bool  # True if it works without further steps


def price_options(hass: HomeAssistant) -> list[PriceOption]:
    nb = _nb(hass)
    opts: list[PriceOption] = []
    for e in hass.config_entries.async_entries("nordpool"):
        areas = ", ".join(e.data.get("areas", []))
        opts.append(PriceOption(f"nordpool:{e.entry_id}", f"Nord Pool ({areas})", True))
    if hass.config_entries.async_entries("tibber"):
        opts.append(PriceOption(TIBBER, "Tibber" + (" (pris inkl. avgifter)" if nb else " (price incl. taxes)"), True))
    for st in hass.states.async_all("sensor"):
        unit = str(st.attributes.get("unit_of_measurement") or "").lower()
        if "/kwh" not in unit and "/mwh" not in unit:
            continue
        if any(isinstance(st.attributes.get(a), list) and st.attributes.get(a) for a in _PRICE_ATTRS):
            opts.append(PriceOption(f"sensor:{st.entity_id}", f"{st.name} ({st.entity_id})", True))
    area, _ = guess_nordpool_area(hass)
    if not any(o.value.startswith("nordpool:") for o in opts):
        opts.append(PriceOption(
            SETUP_NORDPOOL,
            (f"Sett opp Nord Pool for meg – gratis, ingen konto (område {area})" if nb
             else f"Set up Nord Pool for me – free, no account (area {area})"),
            False,
        ))
    opts.append(PriceOption(OTHER_SENSOR, "Annen prissensor …" if nb else "Another price sensor …", False))
    return opts


def presence_defaults(hass: HomeAssistant) -> list[str]:
    persons = [
        p for p in hass.states.async_all("person")
        if p.state not in ("unknown", "unavailable")
    ]
    with_phone = [p.entity_id for p in persons if p.attributes.get("device_trackers")]
    return with_phone or [p.entity_id for p in persons]


def weather_default(hass: HomeAssistant) -> str | None:
    states = hass.states.async_all("weather")
    home = next((s.entity_id for s in states if s.entity_id.endswith("forecast_home")), None)
    return home or (states[0].entity_id if states else None)


def summary(hass: HomeAssistant, thermostats: int) -> str:
    """Checklist shown on the first setup screen."""
    nb = _nb(hass)
    lines: list[str] = []
    opts = [o for o in price_options(hass) if o.ready]
    if opts:
        lines.append(("✅ Strømpris: " if nb else "✅ Electricity price: ") + opts[0].label)
    else:
        area, _ = guess_nordpool_area(hass)
        lines.append(
            (f"➕ Strømpris: ingen funnet. Velg «Sett opp Nord Pool for meg» under – gratis og uten konto, "
             f"område {area} ut fra adressen din. Har du Tibber, kan du heller "
             "[legge til Tibber](https://my.home-assistant.io/redirect/config_flow_start/?domain=tibber) først.")
            if nb else
            (f"➕ Electricity price: none found. Choose «Set up Nord Pool for me» below – free, no account, "
             f"area {area} from your address. Using Tibber? "
             "[Add Tibber](https://my.home-assistant.io/redirect/config_flow_start/?domain=tibber) first instead.")
        )
    people = presence_defaults(hass)
    if people:
        names = ", ".join(hass.states.get(p).name for p in people if hass.states.get(p))
        lines.append(("✅ Hvem som er hjemme: " if nb else "✅ Who is home: ") + names)
    else:
        lines.append(
            "➕ Hvem som er hjemme: ingen personer med telefon. Installer Home Assistant-appen på telefonen "
            "for å senke varmen når alle er borte. Kan hoppes over." if nb else
            "➕ Who is home: no people with a phone. Install the Home Assistant app on your phone "
            "to lower the heat when everyone is away. Optional."
        )
    w = weather_default(hass)
    if w:
        lines.append(("✅ Værvarsel: " if nb else "✅ Weather forecast: ") + (hass.states.get(w).name if hass.states.get(w) else w))
    else:
        lines.append(
            "➕ Værvarsel: mangler – settes opp automatisk fra Met.no." if nb
            else "➕ Weather forecast: missing – will be set up automatically from Met.no."
        )
    if thermostats:
        lines.append((f"✅ {thermostats} termostater" if nb else f"✅ {thermostats} thermostats")
                     if thermostats > 1 else ("✅ 1 termostat" if nb else "✅ 1 thermostat"))
    else:
        lines.append(
            "⚠️ Ingen termostater funnet. Legg dem til i Home Assistant først (Zigbee, Z-Wave, Matter …), "
            "eller legg til soner manuelt senere." if nb else
            "⚠️ No thermostats found. Add them to Home Assistant first (Zigbee, Z-Wave, Matter …), "
            "or add zones manually later."
        )
    return "\n".join(lines)


async def async_setup_nordpool(hass: HomeAssistant) -> tuple[str | None, str, str | None]:
    """Create a Nord Pool entry for the guessed area. Returns (entry_id, area, error)."""
    area, currency = guess_nordpool_area(hass)
    try:
        result = await hass.config_entries.flow.async_init("nordpool", context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"areas": [area], "currency": currency}
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not set up Nord Pool automatically: %s", err)
        return None, area, "nordpool_failed"
    if result.get("type") != "create_entry":
        if result.get("flow_id"):
            hass.config_entries.flow.async_abort(result["flow_id"])
        return None, area, "nordpool_failed"
    return result["result"].entry_id, area, None


async def async_setup_weather(hass: HomeAssistant) -> str | None:
    """Create the Met.no home forecast if the home location is set. Returns entity id or None."""
    if hass.config_entries.async_entries("met"):
        return weather_default(hass)
    try:
        result = await hass.config_entries.flow.async_init("met", context={"source": "onboarding"})
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Met.no setup failed: %s", err)
        return None
    if result.get("type") != "create_entry":
        return None
    await hass.async_block_till_done()
    return weather_default(hass)


def parse_price_choice(hass: HomeAssistant, choice: str) -> dict[str, Any]:
    """Translate the price select value into options."""
    from .const import (
        CONF_NORDPOOL_AREA, CONF_NORDPOOL_ENTRY, CONF_PRICE_SENSOR, CONF_PRICE_TYPE,
        PRICE_NORDPOOL, PRICE_SENSOR, PRICE_TIBBER,
    )

    if choice.startswith("nordpool:"):
        entry_id = choice.split(":", 1)[1]
        entry = hass.config_entries.async_get_entry(entry_id)
        areas = list(entry.data.get("areas", [])) if entry else []
        guess, _ = guess_nordpool_area(hass)
        return {CONF_PRICE_TYPE: PRICE_NORDPOOL, CONF_NORDPOOL_ENTRY: entry_id,
                CONF_NORDPOOL_AREA: guess if guess in areas else (areas[0] if areas else guess)}
    if choice == TIBBER:
        return {CONF_PRICE_TYPE: PRICE_TIBBER}
    if choice.startswith("sensor:"):
        return {CONF_PRICE_TYPE: PRICE_SENSOR, CONF_PRICE_SENSOR: choice.split(":", 1)[1]}
    return {}
