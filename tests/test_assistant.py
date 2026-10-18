"""AI-assisted setup."""

import json

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.heat_planner.assistant import build_prompt, validate_proposal
from custom_components.heat_planner.const import DOMAIN
from custom_components.heat_planner.discovery import discover

from .test_integration import PRICE_SENSOR, setup_states  # noqa: F401

ANSWER = {
    "house": {"house_type": "terraced", "insulation": "good", "heating_sources": ["floor_electric", "bogus"], "heated_area": 130},
    "zones": [
        {"climate_entity": "climate.stue", "name": "Stue", "mode": "setpoint", "comfort": 21.5,
         "weekdays": [-2] * 5 + [1] * 3 + [-1] * 7 + [0] * 8 + [-2],
         "weekend": [-2] * 7 + [1] * 3 + [0] * 13 + [-2],
         "settings": {"away_offset": -12, "away_until_hour": 14, "price_aggressiveness": 1, "evil": 1}},
        {"climate_entity": "climate.not_here", "name": "Ghost", "mode": "setpoint", "comfort": 21},
    ],
    "explanation": "Stua er varm om kvelden og senkes når dere er på jobb.",
}


async def _start(hass):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "Hjem", "price_source": f"sensor:{PRICE_SENSOR}", "weather_entity": "weather.x"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["step_id"] == "energy"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    return r


async def test_validate_clamps_and_ignores_unknown(hass: HomeAssistant, setup_states) -> None:
    prop, err = validate_proposal(hass, "Here you go:\n```json\n" + json.dumps(ANSWER) + "\n```", discover(hass))
    assert err is None
    assert [z["climate_entity"] for z in prop["zones"]] == ["climate.stue"]
    seed = prop["seeds"]["stue"]
    assert seed["settings"] == {"away_offset": -8, "away_until_hour": 14, "price_aggressiveness": 1.0}
    assert seed["schedule"]["0"][5] == 1 and seed["schedule"]["6"][5] == -2
    assert prop["house"]["heating_sources"] == ["floor_electric"]
    assert validate_proposal(hass, "nonsense", discover(hass))[1] == "ai_invalid"
    assert validate_proposal(hass, {"zones": [{"climate_entity": "climate.nope"}]}, discover(hass))[1] == "ai_no_zones"


async def test_prompt_has_no_people_and_lists_thermostats(hass: HomeAssistant, setup_states) -> None:
    text = build_prompt(hass, {"insulation": "good"}, discover(hass), "Jobb 8-15")
    assert "climate.stue" in text and "Jobb 8-15" in text and "person." not in text


async def test_copy_paste_flow_seeds_schedule(hass: HomeAssistant, setup_states) -> None:
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "ai_copy"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"routine": "På jobb 8–15"})
    assert r["step_id"] == "ai_paste"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"answer": "oops"})
    assert r["errors"] == {"answer": "ai_invalid"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"answer": json.dumps(ANSWER)})
    assert r["step_id"] == "ai_review" and "21.5°" in r["description_placeholders"]["proposal"]
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"accept": True})
    assert r["step_id"] == "history"
    set_temp = async_mock_service(hass, "climate", "set_temperature")
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"use_history": False})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    coord = r["result"].runtime_data
    assert coord.zone("stue").comfort == 21.5 and coord.zone("stue").away_until_hour == 14
    assert coord.schedule("stue")[0][5] == 1
    assert set_temp


async def test_ai_task_flow(hass: HomeAssistant, setup_states) -> None:
    hass.states.async_set("ai_task.claude", "unknown")
    seen = {}

    async def generate(call):
        seen.update(call.data)
        return {"conversation_id": "x", "data": json.dumps(ANSWER)}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    r = await _start(hass)
    assert r["menu_options"][0] == "ai_auto"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "ai_auto"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"routine": "Barna legger seg 19", "ai_entity": "ai_task.claude"})
    assert r["step_id"] == "ai_review"
    assert seen["entity_id"] == "ai_task.claude" and "Barna legger seg 19" in seen["instructions"]


async def test_ai_failure_is_explained(hass: HomeAssistant, setup_states) -> None:
    hass.states.async_set("ai_task.claude", "unknown")

    async def generate(call):
        raise RuntimeError("no network")

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "ai_auto"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"routine": "", "ai_entity": "ai_task.claude"})
    assert r["errors"] == {"base": "ai_failed"}


async def test_options_reseed_keeps_learned_model(hass: HomeAssistant, setup_states) -> None:
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "number", "set_value")
    entry = MockConfigEntry(domain=DOMAIN, title="Hjem", data={}, options={
        "price_type": "sensor", "price_sensor": PRICE_SENSOR, "use_history": False,
        "zones": [{"zone_id": "stue", "name": "Stue", "mode": "setpoint", "climate_entity": "climate.stue", "comfort": 20, "heater_kw": 1}],
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    coord.zone_state("stue")["thermal"] = {"gross_rate": 0.9, "tau_hours": 44.0, "learned": True, "samples": 50}
    await coord._save()
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "assistant"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "ai_copy"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"routine": ""})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"answer": json.dumps(ANSWER)})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"accept": True})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord.zone("stue").comfort == 21.5
    assert coord.zone("stue").thermal.tau_hours == 44.0
