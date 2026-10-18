"""Config and options flow for Heat Planner."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_HOUSE,
    CONF_USE_HISTORY,
    CONF_POWER_SENSOR,
    CONF_CLIMATE,
    CONF_COMFORT,
    CONF_HEATER_KW,
    CONF_MODE,
    CONF_NAME,
    CONF_NORDPOOL_AREA,
    CONF_NORDPOOL_ENTRY,
    CONF_NUMBER,
    CONF_PRESENCE,
    CONF_PRICE_EXTRA,
    CONF_PRICE_SENSOR,
    CONF_PRICE_TYPE,
    CONF_TEMP_SENSOR,
    CONF_WEATHER,
    CONF_ZONE_ID,
    CONF_ZONES,
    DOMAIN,
    PRICE_NORDPOOL,
    PRICE_SENSOR,
)
from .discovery import HEATING_SOURCES, HOUSE_TYPES, INSULATION, default_heater_kw, describe, discover
from .planner import MODE_PERCENT, MODE_SETPOINT


def _nordpool_entries(hass) -> list[ConfigEntry]:
    return hass.config_entries.async_entries("nordpool")


def _general_schema(hass, defaults: dict[str, Any]) -> vol.Schema:
    has_np = bool(_nordpool_entries(hass))
    return vol.Schema(
        {
            vol.Required(
                CONF_PRICE_TYPE,
                default=defaults.get(CONF_PRICE_TYPE, PRICE_NORDPOOL if has_np else PRICE_SENSOR),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[PRICE_NORDPOOL, PRICE_SENSOR],
                    translation_key="price_type",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(
                CONF_PRESENCE, default=defaults.get(CONF_PRESENCE, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["person", "device_tracker", "binary_sensor", "zone", "input_boolean"],
                    multiple=True,
                )
            ),
            vol.Optional(
                CONF_WEATHER, description={"suggested_value": defaults.get(CONF_WEATHER)}
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="weather")),
            vol.Optional(
                CONF_PRICE_EXTRA, default=defaults.get(CONF_PRICE_EXTRA, 0.0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=10, step=0.01, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _nordpool_schema(hass, defaults: dict[str, Any]) -> vol.Schema:
    entries = _nordpool_entries(hass)
    areas: list[str] = []
    for e in entries:
        areas.extend(e.data.get("areas", []))
    return vol.Schema(
        {
            vol.Required(
                CONF_NORDPOOL_ENTRY,
                default=defaults.get(CONF_NORDPOOL_ENTRY, entries[0].entry_id if entries else None),
            ): selector.ConfigEntrySelector(selector.ConfigEntrySelectorConfig(integration="nordpool")),
            vol.Required(
                CONF_NORDPOOL_AREA,
                default=defaults.get(CONF_NORDPOOL_AREA, areas[0] if areas else "NO1"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=sorted(set(areas)) or ["NO1"], custom_value=True)
            ),
        }
    )


def _sensor_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_PRICE_SENSOR, description={"suggested_value": defaults.get(CONF_PRICE_SENSOR)}
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
    per_zone_area = None
    chosen = [c for c in cands if c.zone_id in picked]
    if chosen and house.get("heated_area"):
        per_zone_area = float(house["heated_area"]) / max(3, len(chosen) * 2)
    kw = default_heater_kw(house, per_zone_area)
    zones = []
    for c in chosen:
        z = c.as_zone(0.0 if c.power_sensor else kw)  # 0 = learn from the power meter
        zones.append(z)
    return zones


class HeatPlannerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a Heat Planner home."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_house()
        schema = vol.Schema(
            {vol.Required(CONF_NAME, default="Heat Planner"): str}
        ).extend(_general_schema(self.hass, {}).schema)
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_house(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_HOUSE] = user_input
            if self._data[CONF_PRICE_TYPE] == PRICE_NORDPOOL:
                return await self.async_step_nordpool()
            return await self.async_step_price_sensor()
        return self.async_show_form(step_id="house", data_schema=_house_schema({}))

    async def async_step_zones(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        cands = discover(self.hass)
        if user_input is not None or not cands:
            picked = (user_input or {}).get("zones", [])
            self._data[CONF_ZONES] = _zones_from(cands, picked, self._data.get(CONF_HOUSE, {}))
            return await self.async_step_history()
        pre = [c.zone_id for c in cands if c.area]
        return self.async_show_form(
            step_id="zones",
            data_schema=_zones_schema(cands, pre),
            description_placeholders={"found": describe(self.hass, cands, self.hass.config.language)},
        )

    async def async_step_history(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return self._create()
        return self.async_show_form(step_id="history", data_schema=_history_schema({}))

    async def async_step_nordpool(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not _nordpool_entries(self.hass):
            return self.async_abort(reason="no_nordpool")
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_zones()
        return self.async_show_form(step_id="nordpool", data_schema=_nordpool_schema(self.hass, {}))

    async def async_step_price_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_zones()
        return self.async_show_form(step_id="price_sensor", data_schema=_sensor_schema({}))

    def _create(self) -> ConfigFlowResult:
        name = self._data.pop(CONF_NAME, "Heat Planner")
        zones = self._data.pop(CONF_ZONES, [])
        return self.async_create_entry(title=name, data={}, options={**self._data, CONF_ZONES: zones})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HeatPlannerOptionsFlow()


class HeatPlannerOptionsFlow(OptionsFlow):
    """Manage zones and general settings."""

    def __init__(self) -> None:
        self._opts: dict[str, Any] = {}
        self._edit: str | None = None

    @property
    def _zones(self) -> list[dict[str, Any]]:
        return list(self._opts.get(CONF_ZONES, []))

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._opts = {**self.config_entry.options}
        menu = ["discover", "add_zone"]
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
                vol.Required(CONF_HEATER_KW, default=d.get(CONF_HEATER_KW, 1.0)): selector.NumberSelector(
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

    async def async_step_house(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self._opts, CONF_HOUSE: user_input})
        return self.async_show_form(step_id="house", data_schema=_house_schema(self._opts.get(CONF_HOUSE, {})))

    async def async_step_history(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self._opts, **user_input})
        return self.async_show_form(step_id="history", data_schema=_history_schema(self._opts))

    # -- general -------------------------------------------------------------

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if CONF_WEATHER not in user_input:
                self._opts.pop(CONF_WEATHER, None)
            self._opts.update(user_input)
            if user_input[CONF_PRICE_TYPE] == PRICE_NORDPOOL:
                return await self.async_step_nordpool()
            return await self.async_step_price_sensor()
        return self.async_show_form(
            step_id="general", data_schema=_general_schema(self.hass, self._opts)
        )

    async def async_step_nordpool(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not _nordpool_entries(self.hass):
            return self.async_abort(reason="no_nordpool")
        if user_input is not None:
            self._opts.update(user_input)
            return self.async_create_entry(data=self._opts)
        return self.async_show_form(
            step_id="nordpool", data_schema=_nordpool_schema(self.hass, self._opts)
        )

    async def async_step_price_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._opts.update(user_input)
            return self.async_create_entry(data=self._opts)
        return self.async_show_form(step_id="price_sensor", data_schema=_sensor_schema(self._opts))
