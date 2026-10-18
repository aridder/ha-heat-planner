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


class HeatPlannerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a Heat Planner home."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_PRICE_TYPE] == PRICE_NORDPOOL:
                return await self.async_step_nordpool()
            return await self.async_step_price_sensor()
        schema = vol.Schema(
            {vol.Required(CONF_NAME, default="Heat Planner"): str}
        ).extend(_general_schema(self.hass, {}).schema)
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_nordpool(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not _nordpool_entries(self.hass):
            return self.async_abort(reason="no_nordpool")
        if user_input is not None:
            self._data.update(user_input)
            return self._create()
        return self.async_show_form(step_id="nordpool", data_schema=_nordpool_schema(self.hass, {}))

    async def async_step_price_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return self._create()
        return self.async_show_form(step_id="price_sensor", data_schema=_sensor_schema({}))

    def _create(self) -> ConfigFlowResult:
        name = self._data.pop(CONF_NAME, "Heat Planner")
        return self.async_create_entry(title=name, data={}, options={**self._data, CONF_ZONES: []})

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
        menu = ["add_zone", "general"]
        if self._zones:
            menu = ["add_zone", "edit_zone", "remove_zone", "general"]
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
                vol.Required(CONF_HEATER_KW, default=d.get(CONF_HEATER_KW, 1.0)): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.05, max=30, step=0.05, unit_of_measurement="kW",
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
