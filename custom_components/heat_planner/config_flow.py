"""Config and options flow for Heat Planner.

The first screen shows a checklist of what was found and pre-fills every
choice, so most people can press Submit four times. What is missing is either
set up for the user (Nord Pool, Met.no) or explained in one sentence.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_CLIMATE,
    CONF_COMFORT,
    CONF_HEATER_KW,
    CONF_HOUSE,
    CONF_MODE,
    CONF_NAME,
    CONF_NORDPOOL_AREA,
    CONF_NORDPOOL_ENTRY,
    CONF_NUMBER,
    CONF_POWER_SENSOR,
    CONF_PRESENCE,
    CONF_PRICE_EXTRA,
    CONF_PRICE_SENSOR,
    CONF_PRICE_TYPE,
    CONF_TEMP_SENSOR,
    CONF_USE_HISTORY,
    CONF_WEATHER,
    CONF_ZONE_ID,
    CONF_ZONES,
    DOMAIN,
    PRICE_NORDPOOL,
    PRICE_SENSOR,
    PRICE_TIBBER,
)
from .assistant import (
    ai_task_entities,
    async_ask_ai,
    build_prompt,
    describe_proposal,
    validate_proposal,
)
from .discovery import HEATING_SOURCES, HOUSE_TYPES, INSULATION, default_heater_kw, describe, discover
from .onboarding import (
    OTHER_SENSOR,
    SETUP_NORDPOOL,
    TIBBER,
    async_setup_nordpool,
    async_setup_weather,
    parse_price_choice,
    presence_defaults,
    price_options,
    summary,
    weather_default,
)
from .planner import MODE_PERCENT, MODE_SETPOINT

CONF_PRICE_SOURCE = "price_source"
CONF_ROUTINE = "routine"
CONF_SEEDS = "seeds"
CONF_SEED_VERSION = "seed_version"


def _routine_schema(hass: HomeAssistant, with_ai: bool, d: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Optional(CONF_ROUTINE, default=d.get(CONF_ROUTINE, "")): selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        )
    }
    if with_ai:
        ents = ai_task_entities(hass)
        fields[vol.Required("ai_entity", default=d.get("ai_entity", ents[0]))] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="ai_task")
        )
    return vol.Schema(fields)


class _AssistantMixin:
    """Shared AI steps for the config and options flows."""

    hass: HomeAssistant
    _house: dict[str, Any]
    _proposal: dict[str, Any] | None
    _prompt: str

    def _cands(self):
        return discover(self.hass)

    def _setup_menu(self) -> list[str]:
        menu = ["zones"]
        if ai_task_entities(self.hass):
            menu.insert(0, "ai_auto")
        menu.insert(1 if menu[0] == "ai_auto" else 0, "ai_copy")
        return menu

    async def async_step_setup_method(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self._cands():
            return await self.async_step_zones()
        return self.async_show_menu(step_id="setup_method", menu_options=self._setup_menu())

    async def async_step_ai_auto(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cands = self._cands()
            prompt = build_prompt(self.hass, self._house, cands, user_input.get(CONF_ROUTINE, ""))
            try:
                raw = await async_ask_ai(self.hass, user_input["ai_entity"], prompt)
            except Exception:  # noqa: BLE001
                errors["base"] = "ai_failed"
            else:
                self._proposal, err = validate_proposal(self.hass, raw, cands)
                if err:
                    errors["base"] = err
                else:
                    return await self.async_step_ai_review()
        return self.async_show_form(
            step_id="ai_auto", data_schema=_routine_schema(self.hass, True, user_input or {}), errors=errors
        )

    async def async_step_ai_copy(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._prompt = build_prompt(self.hass, self._house, self._cands(), user_input.get(CONF_ROUTINE, ""))
            return await self.async_step_ai_paste()
        return self.async_show_form(step_id="ai_copy", data_schema=_routine_schema(self.hass, False, {}))

    async def async_step_ai_paste(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._proposal, err = validate_proposal(self.hass, user_input.get("answer", ""), self._cands())
            if err:
                errors["answer"] = err
            else:
                return await self.async_step_ai_review()
        return self.async_show_form(
            step_id="ai_paste",
            data_schema=vol.Schema(
                {
                    vol.Optional("prompt", default=self._prompt): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    vol.Required("answer"): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
                }
            ),
            errors=errors,
        )

    async def async_step_ai_review(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._proposal is not None
        if user_input is not None:
            if user_input.get("accept", True):
                return await self._accept_proposal(self._proposal)
            return await self.async_step_zones()
        return self.async_show_form(
            step_id="ai_review",
            data_schema=vol.Schema({vol.Required("accept", default=True): bool}),
            description_placeholders={"proposal": describe_proposal(self.hass, self._proposal)},
        )



def _current_choice(opts: dict[str, Any]) -> str | None:
    t = opts.get(CONF_PRICE_TYPE)
    if t == PRICE_NORDPOOL and opts.get(CONF_NORDPOOL_ENTRY):
        return f"nordpool:{opts[CONF_NORDPOOL_ENTRY]}"
    if t == PRICE_TIBBER:
        return TIBBER
    if t == PRICE_SENSOR and opts.get(CONF_PRICE_SENSOR):
        return f"sensor:{opts[CONF_PRICE_SENSOR]}"
    return None


def _start_schema(hass: HomeAssistant, d: dict[str, Any], with_name: bool) -> vol.Schema:
    options = price_options(hass)
    values = [o.value for o in options]
    current = _current_choice(d)
    default = current if current in values else options[0].value
    weather = d.get(CONF_WEATHER, weather_default(hass))
    country_extra = 0.45 if (hass.config.country or "").upper() == "NO" else 0.0
    fields: dict[Any, Any] = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=d.get(CONF_NAME, "Hjem" if hass.config.language.startswith("nb") else "Home"))] = str
    fields.update(
        {
            vol.Required(CONF_PRICE_SOURCE, default=default): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=o.value, label=o.label) for o in options],
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(
                CONF_PRESENCE, default=d.get(CONF_PRESENCE, presence_defaults(hass))
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["person", "device_tracker", "binary_sensor", "zone", "input_boolean"],
                    multiple=True,
                )
            ),
            vol.Optional(CONF_WEATHER, description={"suggested_value": weather}): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
            vol.Optional(
                CONF_PRICE_EXTRA, default=d.get(CONF_PRICE_EXTRA, country_extra)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=10, step=0.01, mode=selector.NumberSelectorMode.BOX)
            ),
        }
    )
    return vol.Schema(fields)


def _sensor_schema(d: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_PRICE_SENSOR, description={"suggested_value": d.get(CONF_PRICE_SENSOR)}
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        }
    )


def _house_schema(d: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("house_type", default=d.get("house_type", "terraced")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=HOUSE_TYPES, translation_key="house_type",
                                              mode=selector.SelectSelectorMode.LIST)
            ),
            vol.Required("insulation", default=d.get("insulation", "normal")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=INSULATION, translation_key="insulation",
                                              mode=selector.SelectSelectorMode.LIST)
            ),
            vol.Required("heating_sources", default=d.get("heating_sources", ["floor_electric"])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=HEATING_SOURCES, translation_key="heating_sources",
                                              multiple=True, mode=selector.SelectSelectorMode.LIST)
            ),
            vol.Optional("heated_area", default=d.get("heated_area", 100)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=10, max=1000, step=1, unit_of_measurement="m²",
                                              mode=selector.NumberSelectorMode.BOX)
            ),
        }
    )


def _history_schema(d: dict[str, Any]) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_USE_HISTORY, default=d.get(CONF_USE_HISTORY, True)): bool})


def _zones_schema(cands, preselect: list[str]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional("zones", default=preselect): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=c.zone_id, label=c.name) for c in cands],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


def _zones_from(cands, picked: list[str], house: dict[str, Any]) -> list[dict[str, Any]]:
    chosen = [c for c in cands if c.zone_id in picked]
    per_zone_area = None
    if chosen and house.get("heated_area"):
        per_zone_area = float(house["heated_area"]) / max(3, len(chosen) * 2)
    kw = default_heater_kw(house, per_zone_area)
    return [c.as_zone(0.0 if c.power_sensor else kw) for c in chosen]


async def _resolve_start(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[dict[str, Any], str | None, str | None]:
    """Turn the first screen into options. Returns (options, next_step, error)."""
    data = {k: v for k, v in user_input.items() if k not in (CONF_PRICE_SOURCE, CONF_NAME)}
    choice = user_input[CONF_PRICE_SOURCE]
    nxt: str | None = None
    if choice == SETUP_NORDPOOL:
        entry_id, area, err = await async_setup_nordpool(hass)
        if err:
            return data, None, err
        data.update({CONF_PRICE_TYPE: PRICE_NORDPOOL, CONF_NORDPOOL_ENTRY: entry_id, CONF_NORDPOOL_AREA: area})
    elif choice == OTHER_SENSOR:
        nxt = "price_sensor"
    else:
        data.update(parse_price_choice(hass, choice))
    if not data.get(CONF_WEATHER):
        weather = await async_setup_weather(hass)
        if weather:
            data[CONF_WEATHER] = weather
    return data, nxt, None


class HeatPlannerConfigFlow(_AssistantMixin, ConfigFlow, domain=DOMAIN):
    """Create a Heat Planner home."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._name = "Home"
        self._house: dict[str, Any] = {}
        self._proposal: dict[str, Any] | None = None
        self._prompt = ""

    async def _accept_proposal(self, proposal: dict[str, Any]) -> ConfigFlowResult:
        self._data[CONF_ZONES] = proposal["zones"]
        self._data[CONF_SEEDS] = proposal["seeds"]
        self._data[CONF_SEED_VERSION] = 1
        return await self.async_step_history()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._name = user_input.get(CONF_NAME, self._name)
            data, nxt, err = await _resolve_start(self.hass, user_input)
            if err:
                errors[CONF_PRICE_SOURCE] = err
            else:
                self._data.update(data)
                if nxt == "price_sensor":
                    return await self.async_step_price_sensor()
                return await self.async_step_house()
        return self.async_show_form(
            step_id="user",
            data_schema=_start_schema(self.hass, user_input or {}, True),
            errors=errors,
            description_placeholders={"summary": summary(self.hass, len(discover(self.hass)))},
        )

    async def async_step_price_sensor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update({CONF_PRICE_TYPE: PRICE_SENSOR, **user_input})
            return await self.async_step_house()
        return self.async_show_form(step_id="price_sensor", data_schema=_sensor_schema({}))

    async def async_step_house(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_HOUSE] = self._house = user_input
            return await self.async_step_setup_method()
        return self.async_show_form(step_id="house", data_schema=_house_schema({}))

    async def async_step_zones(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        cands = discover(self.hass)
        if user_input is not None or not cands:
            picked = (user_input or {}).get("zones", [])
            self._data[CONF_ZONES] = _zones_from(cands, picked, self._data.get(CONF_HOUSE, {}))
            return await self.async_step_history()
        return self.async_show_form(
            step_id="zones",
            data_schema=_zones_schema(cands, [c.zone_id for c in cands if c.area]),
            description_placeholders={"found": describe(self.hass, cands, self.hass.config.language)},
        )

    async def async_step_history(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            zones = self._data.pop(CONF_ZONES, None)
            if zones is None:
                zones = _zones_from(discover(self.hass), [c.zone_id for c in discover(self.hass) if c.area], self._data.get(CONF_HOUSE, {}))
            return self.async_create_entry(title=self._name, data={}, options={**self._data, CONF_ZONES: zones})
        return self.async_show_form(step_id="history", data_schema=_history_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HeatPlannerOptionsFlow()


class HeatPlannerOptionsFlow(_AssistantMixin, OptionsFlow):
    """Manage zones and general settings."""

    def __init__(self) -> None:
        self._opts: dict[str, Any] = {}
        self._edit: str | None = None
        self._house: dict[str, Any] = {}
        self._proposal: dict[str, Any] | None = None
        self._prompt = ""

    async def _accept_proposal(self, proposal: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={
            **self._opts, CONF_ZONES: proposal["zones"], CONF_SEEDS: proposal["seeds"],
            CONF_SEED_VERSION: int(self._opts.get(CONF_SEED_VERSION, 0)) + 1,
        })

    async def async_step_assistant(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._house = self._opts.get(CONF_HOUSE, {})
        return self.async_show_menu(step_id="assistant", menu_options=[m for m in self._setup_menu() if m != "zones"])

    async def async_step_zones(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_discover(user_input)

    @property
    def _zones(self) -> list[dict[str, Any]]:
        return list(self._opts.get(CONF_ZONES, []))

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._opts = {**self.config_entry.options}
        menu = ["assistant", "discover", "add_zone"]
        if self._zones:
            menu += ["edit_zone", "remove_zone"]
        menu += ["house", "history", "general"]
        return self.async_show_menu(step_id="init", menu_options=menu)

    # -- zones ---------------------------------------------------------------

    def _zone_schema(self, d: dict[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): str,
                vol.Required(CONF_MODE, default=d.get(CONF_MODE, MODE_SETPOINT)): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[MODE_SETPOINT, MODE_PERCENT],
                        translation_key="mode",
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(
                    CONF_CLIMATE, description={"suggested_value": d.get(CONF_CLIMATE)}
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
                vol.Optional(
                    CONF_NUMBER, description={"suggested_value": d.get(CONF_NUMBER)}
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["number", "input_number"])),
                vol.Optional(
                    CONF_TEMP_SENSOR, description={"suggested_value": d.get(CONF_TEMP_SENSOR)}
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Required(CONF_COMFORT, default=d.get(CONF_COMFORT, 21.0)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=30, step=0.5, unit_of_measurement="°C")
                ),
                vol.Optional(
                    CONF_POWER_SENSOR, description={"suggested_value": d.get(CONF_POWER_SENSOR)}
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Required(CONF_HEATER_KW, default=d.get(CONF_HEATER_KW, 0.0)): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=30, step=0.05, unit_of_measurement="kW",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )

    def _validate_zone(self, data: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        if data[CONF_MODE] == MODE_SETPOINT and not data.get(CONF_CLIMATE):
            errors[CONF_CLIMATE] = "climate_required"
        if data[CONF_MODE] == MODE_PERCENT and not data.get(CONF_NUMBER):
            errors[CONF_NUMBER] = "number_required"
        return errors

    async def async_step_add_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate_zone(user_input)
            zid = slugify(user_input[CONF_NAME]) or "zone"
            if any(z[CONF_ZONE_ID] == zid for z in self._zones):
                errors[CONF_NAME] = "duplicate"
            if not errors:
                zones = self._zones + [{CONF_ZONE_ID: zid, **user_input}]
                return self.async_create_entry(data={**self._opts, CONF_ZONES: zones})
        return self.async_show_form(
            step_id="add_zone", data_schema=self._zone_schema(user_input or {}), errors=errors
        )

    async def async_step_edit_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._edit = user_input[CONF_ZONE_ID]
            return await self.async_step_zone()
        return self.async_show_form(step_id="edit_zone", data_schema=self._pick_schema())

    async def async_step_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = next(z for z in self._zones if z[CONF_ZONE_ID] == self._edit)
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate_zone(user_input)
            if not errors:
                zones = [
                    {CONF_ZONE_ID: self._edit, **user_input} if z[CONF_ZONE_ID] == self._edit else z
                    for z in self._zones
                ]
                return self.async_create_entry(data={**self._opts, CONF_ZONES: zones})
        return self.async_show_form(
            step_id="zone", data_schema=self._zone_schema(user_input or current), errors=errors
        )

    async def async_step_remove_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            zones = [z for z in self._zones if z[CONF_ZONE_ID] != user_input[CONF_ZONE_ID]]
            return self.async_create_entry(data={**self._opts, CONF_ZONES: zones})
        return self.async_show_form(step_id="remove_zone", data_schema=self._pick_schema())

    def _pick_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_ZONE_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=z[CONF_ZONE_ID], label=z[CONF_NAME])
                            for z in self._zones
                        ]
                    )
                )
            }
        )

    async def async_step_discover(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        existing = {z.get(CONF_CLIMATE) for z in self._zones}
        cands = [c for c in discover(self.hass, existing)
                 if c.zone_id not in {z[CONF_ZONE_ID] for z in self._zones}]
        if not cands:
            return self.async_abort(reason="nothing_new")
        if user_input is not None:
            new = _zones_from(cands, user_input.get("zones", []), self._opts.get(CONF_HOUSE, {}))
            return self.async_create_entry(data={**self._opts, CONF_ZONES: self._zones + new})
        return self.async_show_form(
            step_id="discover",
            data_schema=_zones_schema(cands, [c.zone_id for c in cands if c.area]),
            description_placeholders={"found": describe(self.hass, cands, self.hass.config.language)},
        )

    # -- home ----------------------------------------------------------------

    async def async_step_house(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self._opts, CONF_HOUSE: user_input})
        return self.async_show_form(step_id="house", data_schema=_house_schema(self._opts.get(CONF_HOUSE, {})))

    async def async_step_history(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self._opts, **user_input})
        return self.async_show_form(step_id="history", data_schema=_history_schema(self._opts))

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data, nxt, err = await _resolve_start(self.hass, user_input)
            if err:
                errors[CONF_PRICE_SOURCE] = err
            else:
                for k in (CONF_NORDPOOL_ENTRY, CONF_NORDPOOL_AREA, CONF_PRICE_SENSOR, CONF_WEATHER):
                    if k not in data:
                        self._opts.pop(k, None)
                self._opts.update(data)
                if nxt == "price_sensor":
                    return await self.async_step_price_sensor()
                return self.async_create_entry(data=self._opts)
        return self.async_show_form(
            step_id="general",
            data_schema=_start_schema(self.hass, self._opts, False),
            errors=errors,
            description_placeholders={"summary": summary(self.hass, len(self._zones))},
        )

    async def async_step_price_sensor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._opts.update({CONF_PRICE_TYPE: PRICE_SENSOR, **user_input})
            return self.async_create_entry(data=self._opts)
        return self.async_show_form(step_id="price_sensor", data_schema=_sensor_schema(self._opts))
