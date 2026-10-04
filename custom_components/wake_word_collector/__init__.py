"""Wake Word Collector: record wake word examples with your own voice satellites."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import intents
from .collect import CATEGORIES, DECISIONS
from .collector import Collector
from .const import AUDIO_URL, CARD_URL, CONF_TRAINER_TOKEN, CONF_TRAINER_URL, DOMAIN
from .speaker_test import MAX_CLIPS, SpeakerTest
from .trainer import TrainerClient, TrainerCoordinator
from .views import VIEWS

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.TEXT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
ENTRY = vol.Optional("config_entry_id")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    for view in VIEWS:
        hass.http.register_view(view())
    _register_services(hass)
    websocket_api.async_register_command(hass, ws_list)
    intents.async_register(hass)
    await _register_card(hass)
    return True


async def _register_card(hass: HomeAssistant) -> None:
    """Serve the review card and load it in every dashboard."""
    from homeassistant.components.http import StaticPathConfig

    path = Path(__file__).parent / "frontend" / "wake-word-collector-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), False)])
    if "frontend" in hass.config.components:
        from homeassistant.components.frontend import add_extra_js_url

        version = (await hass.async_add_executor_job(path.stat)).st_mtime_ns
        add_extra_js_url(hass, f"{CARD_URL}?v={version}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    collector = Collector(hass, entry)
    await collector.async_setup()
    if entry.options.get(CONF_TRAINER_URL):
        client = TrainerClient(hass, entry.options[CONF_TRAINER_URL], entry.options.get(CONF_TRAINER_TOKEN, ""))
        collector.trainer = TrainerCoordinator(hass, collector, client)
        collector.speaker_test = SpeakerTest(hass, collector)
        await collector.trainer.async_load_model_info()
        # The training computer may be off; entities then show it as unavailable.
        await collector.trainer.async_refresh()
    hass.data[DOMAIN][entry.entry_id] = collector
    entry.runtime_data = collector
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        collector: Collector = hass.data[DOMAIN].pop(entry.entry_id)
        await collector.async_unload()
    return unloaded


def _collector(hass: HomeAssistant, entry_id: str | None) -> Collector:
    collectors: dict[str, Collector] = hass.data.get(DOMAIN, {})
    if entry_id:
        if entry_id not in collectors:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="unknown_entry")
        return collectors[entry_id]
    if len(collectors) != 1:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="choose_entry")
    return next(iter(collectors.values()))


def _register_services(hass: HomeAssistant) -> None:
    async def start(call: ServiceCall) -> None:
        _collector(hass, call.data.get("config_entry_id")).send("start", call.data.get("device"))

    async def record(call: ServiceCall) -> None:
        _collector(hass, call.data.get("config_entry_id")).send("record", call.data["device"])

    async def stop(call: ServiceCall) -> None:
        _collector(hass, call.data.get("config_entry_id")).send("stop", call.data.get("device"))

    async def review(call: ServiceCall) -> dict[str, Any]:
        collector = _collector(hass, call.data.get("config_entry_id"))
        data = call.data
        return await collector.async_review(
            data["category"], data["device"], data["filename"], data["decision"], data.get("note", "")
        )

    async def review_latest(call: ServiceCall) -> dict[str, Any]:
        collector = _collector(hass, call.data.get("config_entry_id"))
        return await collector.async_review_latest(call.data["note"], call.data["decision"], call.data.get("device"))

    async def review_latest_trigger(call: ServiceCall) -> dict[str, Any]:
        collector = _collector(hass, call.data.get("config_entry_id"))
        return await collector.async_review_latest_trigger(call.data["decision"], call.data.get("device"))

    def _trainer(call: ServiceCall) -> TrainerCoordinator:
        collector = _collector(hass, call.data.get("config_entry_id"))
        if collector.trainer is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_trainer")
        return collector.trainer

    async def start_training(call: ServiceCall) -> dict[str, Any]:
        trainer = _trainer(call)
        result = await trainer.client.start(call.data.get("profile") or trainer.profile or "recommended")
        await trainer.async_request_refresh()
        return result

    async def stop_training(call: ServiceCall) -> dict[str, Any]:
        trainer = _trainer(call)
        result = await trainer.client.stop()
        await trainer.async_request_refresh()
        return result

    async def speaker_test(call: ServiceCall) -> dict[str, Any]:
        return await _trainer(call).client.speaker_test(call.data["route"])

    async def run_speaker_test(call: ServiceCall) -> dict[str, Any]:
        _trainer(call)
        collector = _collector(hass, call.data.get("config_entry_id"))
        data = call.data
        return await collector.speaker_test.async_run(
            device=data.get("device"),
            route=data.get("route"),
            clips=data.get("clips"),
            satellite=data.get("satellite"),
            test_switch=data.get("test_switch"),
        )

    async def import_folder(call: ServiceCall) -> dict[str, Any]:
        collector = _collector(hass, call.data.get("config_entry_id"))
        folder = call.data["folder"]
        inside_config = Path(folder).resolve().is_relative_to(Path(hass.config.config_dir).resolve())
        if not (inside_config or hass.config.is_allowed_path(folder)):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="folder_not_allowed")
        return await collector.async_import(
            folder, call.data["category"], call.data["device"], call.data.get("note", "")
        )

    async def trim(call: ServiceCall) -> dict[str, Any]:
        collector = _collector(hass, call.data.get("config_entry_id"))
        data = call.data
        return await collector.async_trim(
            data["category"], data["device"], data["filename"], data["start_ms"], data["end_ms"], data["mode"]
        )

    clip = {
        ENTRY: cv.string,
        vol.Required("category"): vol.In(CATEGORIES),
        vol.Required("device"): cv.string,
        vol.Required("filename"): cv.string,
    }
    target = vol.Schema({ENTRY: cv.string, vol.Optional("device"): cv.string})
    hass.services.async_register(DOMAIN, "start", start, schema=target)
    hass.services.async_register(DOMAIN, "stop", stop, schema=target)
    hass.services.async_register(
        DOMAIN, "record", record, schema=vol.Schema({ENTRY: cv.string, vol.Required("device"): cv.string})
    )
    hass.services.async_register(
        DOMAIN,
        "review",
        review,
        schema=vol.Schema({**clip, vol.Required("decision"): vol.In(list(DECISIONS)), vol.Optional("note"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "review_latest",
        review_latest,
        schema=vol.Schema(
            {
                ENTRY: cv.string,
                vol.Required("note"): cv.string,
                vol.Optional("decision", default="note"): vol.In(["note", "reject"]),
                vol.Optional("device"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "review_latest_trigger",
        review_latest_trigger,
        schema=vol.Schema(
            {
                ENTRY: cv.string,
                vol.Required("decision"): vol.In(["accept", "negative"]),
                vol.Optional("device"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "start_training",
        start_training,
        schema=vol.Schema({ENTRY: cv.string, vol.Optional("profile"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "stop_training",
        stop_training,
        schema=vol.Schema({ENTRY: cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "speaker_test",
        speaker_test,
        schema=vol.Schema({ENTRY: cv.string, vol.Required("route"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "run_speaker_test",
        run_speaker_test,
        schema=vol.Schema(
            {
                ENTRY: cv.string,
                vol.Optional("device"): cv.string,
                vol.Optional("route"): cv.string,
                vol.Optional("clips"): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_CLIPS)),
                vol.Optional("satellite"): cv.entity_id,
                vol.Optional("test_switch"): vol.Any(
                    "", vol.All(cv.entity_id, cv.entity_domain(["switch", "input_boolean"]))
                ),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "import",
        import_folder,
        schema=vol.Schema(
            {
                ENTRY: cv.string,
                vol.Required("folder"): cv.string,
                vol.Required("category"): vol.In(["candidates", "negatives"]),
                vol.Required("device"): cv.string,
                vol.Optional("note"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "trim",
        trim,
        schema=vol.Schema(
            {
                **clip,
                vol.Required("start_ms"): vol.All(vol.Coerce(int), vol.Range(min=0, max=60000)),
                vol.Required("end_ms"): vol.All(vol.Coerce(int), vol.Range(min=0, max=60000)),
                vol.Optional("mode", default="keep"): vol.In(["keep", "remove", "extract"]),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


@websocket_api.websocket_command({vol.Required("type"): "wake_word_collector/list", vol.Optional("entry_id"): str})
@websocket_api.async_response
async def ws_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Collectors with their statistics and reviewable clips, for the card."""
    collectors: dict[str, Collector] = hass.data.get(DOMAIN, {})
    result = []
    for entry_id, collector in collectors.items():
        if msg.get("entry_id") and msg["entry_id"] != entry_id:
            continue
        await collector.async_refresh()
        items = await collector.async_list()
        for item in items:
            item["audio_path"] = AUDIO_URL.format(
                entry_id=entry_id, category=item["category"], device=item["device"], filename=item["filename"]
            )
        result.append({"entry_id": entry_id, "title": collector.entry.title, "stats": collector.stats, "items": items})
    connection.send_result(msg["id"], {"collectors": result})
