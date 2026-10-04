"""Activation reports, negatives for training, and the connection to a Wake Word Trainer service."""

import json
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wake_word_collector.collect import TRIGGER_SECONDS, Phrases, Store
from custom_components.wake_word_collector.const import (
    CONF_CONTROL,
    CONF_PHRASE,
    CONF_SLUG,
    CONF_STORAGE,
    CONF_TOKEN,
    CONF_TRAINER_TOKEN,
    CONF_TRAINER_URL,
    CONF_VARIANTS,
    DOMAIN,
)

from .test_collect import silence, speech, wav_bytes
from .test_integration import TOKEN, upload

TRAINER = "http://trainer.local:10701"
STATUS = {
    "state": "idle",
    "workstation_online": True,
    "profiles": {"quick": "Quick", "recommended": "Recommended"},
    "slug": "hey_nova",
}


# -- Store ----------------------------------------------------------------------------


def test_trigger_reports_and_negatives(tmp_path: Path) -> None:
    store = Store(tmp_path, Phrases.build("Hey Nova", "", "fertig"))
    # A quiet 8 s report: kept (a quiet false alarm is exactly what to learn), trimmed to its end.
    [record] = store.add("kitchen", "", wav_bytes(silence(5) + speech(3, amplitude=200)), kind="trigger")
    assert record["category"] == "triggers" and record["kind"] == "trigger"
    assert record["duration_ms"] == TRIGGER_SECONDS * 1000
    store.add("kitchen", "ich bin fertig", wav_bytes(speech(1.5)))  # command without the wake word
    store.add("kitchen", "hey nova ich bin fertig", wav_bytes(speech(2)))  # command with it
    assert store.stats()["triggers"] == 1
    result = store.review_latest_trigger("negative")
    assert result["category"] == "negatives" and result["previous_category"] == "triggers"
    negatives = store.negatives()
    assert sorted(item["category"] for item in negatives) == ["control", "negatives"]
    assert all("hey nova" not in item["transcript"] for item in negatives)
    with pytest.raises(Exception, match="no_trigger"):
        store.review_latest_trigger("negative")
    with pytest.raises(Exception, match="invalid_kind"):
        store.add("kitchen", "", wav_bytes(speech(1)), kind="other")


def test_mentions() -> None:
    phrases = Phrases.build("Hey Nova", "hei nova")
    assert phrases.mentions("okay hei Nova mach das Licht an")
    assert not phrases.mentions("Nova allein")


# -- HTTP: upload kind, negatives export, model --------------------------------------


@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path: Path) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hey Nova",
        unique_id="hey_nova",
        data={CONF_SLUG: "hey_nova", CONF_TOKEN: TOKEN},
        options={
            CONF_PHRASE: "Hey Nova",
            CONF_VARIANTS: "",
            CONF_CONTROL: "fertig",
            CONF_STORAGE: str(tmp_path / "clips"),
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_trigger_upload_and_negative_export(hass: HomeAssistant, entry, hass_client_no_auth) -> None:
    client = await hass_client_no_auth()
    response = await client.post(
        "/api/wake_word_collector/clips/hey_nova",
        data=wav_bytes(speech(4)),
        headers={"X-Wakeword-Token": TOKEN, "X-Wakeword-Device": "kitchen", "X-Wakeword-Kind": "trigger"},
    )
    assert response.status == 201
    assert (await response.json())["records"][0]["category"] == "triggers"
    await upload(client, "ich bin fertig", wav_bytes(speech(1.5)))

    # "I did not call you": the activation becomes a negative.
    result = await intent.async_handle(hass, "test", "WakeWordFalseAlarm", language="en")
    assert "noted" in result.speech["plain"]["speech"]
    listing = await client.get(
        "/api/wake_word_collector/export/hey_nova/negatives", headers={"X-Wakeword-Token": TOKEN}
    )
    negatives = (await listing.json())["negatives"]
    assert sorted(item["category"] for item in negatives) == ["control", "negatives"]
    audio = await client.get(negatives[0]["audio_url"], headers={"X-Wakeword-Token": TOKEN})
    assert audio.status == 200 and (await audio.read())[:4] == b"RIFF"
    assert (await client.get(negatives[0]["audio_url"])).status == 401
    assert (await client.get("/api/wake_word_collector/export/hey_nova/negatives")).status == 401
    # Accepted clips stay a separate export.
    candidates = await client.get("/api/wake_word_collector/export/hey_nova", headers={"X-Wakeword-Token": TOKEN})
    assert (await candidates.json())["candidates"] == []
    nothing = await intent.async_handle(hass, "test", "WakeWordFalseAlarm", language="de")
    assert "keine Aufnahme" in nothing.speech["plain"]["speech"]


# -- Trainer --------------------------------------------------------------------------


async def test_options_check_the_trainer(hass: HomeAssistant, entry, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{TRAINER}/v1/status", status=401, json={"error": "unauthorized"})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TRAINER_URL: TRAINER + "/", CONF_TRAINER_TOKEN: "wrong"}
    )
    assert result["errors"] == {CONF_TRAINER_URL: "trainer_auth"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{TRAINER}/v1/status", json=STATUS)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TRAINER_URL: TRAINER + "/", CONF_TRAINER_TOKEN: "s" * 24}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_TRAINER_URL] == TRAINER and entry.options[CONF_PHRASE] == "Hey Nova"
    await hass.async_block_till_done()
    assert hass.states.get("sensor.hey_nova_training").state == "idle"


@pytest.fixture
async def trained(hass: HomeAssistant, tmp_path: Path, aioclient_mock: AiohttpClientMocker):
    assert await async_setup_component(hass, "http", {})
    manifest = {"type": "micro", "model": "hey_nova.tflite", "micro": {"probability_cutoff": 0.91}}
    status = {
        **STATUS,
        "state": "completed",
        "best_model_available": True,
        "ended_at": "2026-10-03T18:00:00+00:00",
        "best_recall": 0.95,
        "best_faph": 0.3,
        "message": "Round 2 of 3: recognizes 95%",
        "progress_percent": 100,
        "speaker_routes": ["speakers", "usb"],
    }
    aioclient_mock.get(f"{TRAINER}/v1/status", json=status)
    aioclient_mock.get(f"{TRAINER}/v1/model/hey_nova.json", text=json.dumps(manifest))
    aioclient_mock.get(f"{TRAINER}/v1/model/hey_nova.tflite", content=b"TFL3-model")
    aioclient_mock.get(f"{TRAINER}/v1/report", json={"curve": [{"cutoff": 0.9, "recall": 0.95, "faph": 0.3}]})
    aioclient_mock.post(f"{TRAINER}/v1/start", status=202, json={"started": "quick"})
    aioclient_mock.post(f"{TRAINER}/v1/stop", json={"stopped": True})
    aioclient_mock.post(f"{TRAINER}/v1/speaker_test", json={"route": "speakers", "clip": "recordings/eval/x.wav"})
    events = []
    hass.bus.async_listen(f"{DOMAIN}_model_ready", events.append)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hey Nova",
        unique_id="hey_nova",
        data={CONF_SLUG: "hey_nova", CONF_TOKEN: TOKEN},
        options={
            CONF_PHRASE: "Hey Nova",
            CONF_STORAGE: str(tmp_path / "clips"),
            CONF_TRAINER_URL: TRAINER,
            CONF_TRAINER_TOKEN: "s" * 24,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, events, tmp_path


async def test_trainer_entities_and_model(hass: HomeAssistant, trained, aioclient_mock, hass_client_no_auth) -> None:
    entry, events, tmp_path = trained
    state = hass.states.get("sensor.hey_nova_training")
    assert state.state == "completed" and state.attributes["best_recall"] == 0.95
    assert hass.states.get("sensor.hey_nova_training_progress").state == "100"
    assert hass.states.get("binary_sensor.hey_nova_trainer").state == "on"
    select = hass.states.get("select.hey_nova_training_profile")
    assert select.state == "recommended" and select.attributes["options"] == ["quick", "recommended"]
    # The finished model was taken over once and is served for ESPHome.
    model = hass.states.get("sensor.hey_nova_model")
    assert model.attributes["url"] == "/api/wake_word_collector/model/hey_nova/hey_nova.json"
    assert model.attributes["probability_cutoff"] == 0.91 and len(events) == 1
    assert (tmp_path / "clips" / "model" / "hey_nova.tflite").read_bytes() == b"TFL3-model"
    source = json.loads((tmp_path / "clips" / "model" / "source.json").read_text())
    assert source["report"]["curve"][0]["recall"] == 0.95
    client = await hass_client_no_auth()
    served = await client.get("/api/wake_word_collector/model/hey_nova/hey_nova.json")
    assert served.status == 200 and (await served.json())["model"] == "hey_nova.tflite"
    assert (await client.get("/api/wake_word_collector/model/hey_nova/other.json")).status == 404
    # A second refresh with the same model does not download again.
    downloads = sum(1 for call in aioclient_mock.mock_calls if "/v1/model/" in str(call[1]))
    await entry.runtime_data.trainer.async_refresh()
    assert sum(1 for call in aioclient_mock.mock_calls if "/v1/model/" in str(call[1])) == downloads

    await hass.services.async_call(
        "select", "select_option", {"entity_id": select.entity_id, "option": "quick"}, blocking=True
    )
    await hass.services.async_call("button", "press", {"entity_id": "button.hey_nova_start_training"}, blocking=True)
    start = [call for call in aioclient_mock.mock_calls if str(call[1]).endswith("/v1/start")][-1]
    assert start[2] == {"profile": "quick"}
    assert start[3]["Authorization"] == "Bearer " + "s" * 24
    await hass.services.async_call("button", "press", {"entity_id": "button.hey_nova_stop_training"}, blocking=True)
    result = await hass.services.async_call(
        DOMAIN, "speaker_test", {"route": "speakers"}, blocking=True, return_response=True
    )
    assert result["clip"] == "recordings/eval/x.wav"

    from custom_components.wake_word_collector.diagnostics import async_get_config_entry_diagnostics

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["options"][CONF_TRAINER_TOKEN] == "**REDACTED**"
    assert diagnostics["trainer"]["state"] == "completed"


async def test_trainer_offline(hass: HomeAssistant, tmp_path: Path, aioclient_mock: AiohttpClientMocker) -> None:
    import aiohttp

    assert await async_setup_component(hass, "http", {})
    aioclient_mock.get(f"{TRAINER}/v1/status", exc=aiohttp.ClientError())
    aioclient_mock.post(f"{TRAINER}/v1/start", exc=aiohttp.ClientError())
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hey Nova",
        unique_id="hey_nova",
        data={CONF_SLUG: "hey_nova", CONF_TOKEN: TOKEN},
        options={CONF_PHRASE: "Hey Nova", CONF_STORAGE: str(tmp_path / "c"), CONF_TRAINER_URL: TRAINER},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.hey_nova_trainer").state == "off"
    assert hass.states.get("sensor.hey_nova_training").state == "unavailable"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "start_training", {}, blocking=True)


async def test_training_services_need_a_trainer(hass: HomeAssistant, entry) -> None:
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "stop_training", {}, blocking=True)
    assert hass.states.get("sensor.hey_nova_training") is None


async def test_import_folder(hass: HomeAssistant, entry, tmp_path: Path) -> None:
    import shutil

    source = tmp_path / "earlier"
    (source / "accepted").mkdir(parents=True)
    for index in range(3):
        (source / "accepted" / f"{index}.wav").write_bytes(wav_bytes(speech(1 + index * 0.1)))
    (source / "accepted" / "broken.wav").write_bytes(b"not a wav")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "import", {"folder": str(source), "category": "candidates", "device": "earlier"}, blocking=True
        )
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    result = await hass.services.async_call(
        DOMAIN,
        "import",
        {"folder": str(source), "category": "candidates", "device": "Earlier", "note": "lab"},
        blocking=True,
        return_response=True,
    )
    assert result == {"imported": 3, "duplicates": 0, "refused": 1}
    assert hass.states.get("sensor.hey_nova_recordings").state == "3"
    again = await hass.services.async_call(
        DOMAIN,
        "import",
        {"folder": str(source), "category": "negatives", "device": "earlier"},
        blocking=True,
        return_response=True,
    )
    assert again == {"imported": 0, "duplicates": 3, "refused": 1}
    store = entry.runtime_data.store
    files = sorted(p.name for p in (store.root / "candidates" / "earlier").glob("*.wav"))
    assert len(files) == 3 and all(name.startswith("ha_earlier_") for name in files)
    assert {item["note"] for item in store.list(("candidates",))} == {"lab"}
    shutil.rmtree(source)


async def test_automatic_extraction_keeps_original_and_only_exports_reviewed_cuts(hass, trained, aioclient_mock):
    import asyncio

    entry, _events, _tmp_path = trained
    collector = entry.runtime_data
    segments = [
        {"start_ms": 800, "end_ms": 2000, "phrase": "hey nova"},
        {"start_ms": 6000, "end_ms": 7500, "phrase": "hey nova"},
    ]
    aioclient_mock.post(f"{TRAINER}/v1/extract", json={"segments": segments})
    body = wav_bytes(speech(30))
    [source] = await collector.async_add("office", "", body, "manual")
    await asyncio.gather(*list(collector._extraction_tasks))
    items = await collector.async_list()
    cuts = [i for i in items if i.get("extracted_from") == source["filename"]]
    assert len(cuts) == 2 and all(i["category"] == "needs_review" for i in cuts)
    assert collector.store.audio("needs_review", "office", source["filename"]).read_bytes() == body
    source_item = next(i for i in items if i["filename"] == source["filename"])
    assert source_item["extraction_state"] == "done" and source_item["extraction_count"] == 2
    result = await hass.services.async_call(
        DOMAIN,
        "extract",
        {
            "category": "needs_review",
            "device": "office",
            "filename": source["filename"],
        },
        blocking=True,
        return_response=True,
    )
    assert result["count"] == 2 and len(await collector.async_list()) == len(items)
    assert aioclient_mock.mock_calls[-1][3]["X-Wakeword-Phrases"] == '["hey nova"]'
    await hass.services.async_call(DOMAIN, "auto_extract", {"enabled": False}, blocking=True)
    before = len(aioclient_mock.mock_calls)
    await collector.async_add("office", "", wav_bytes(speech(3)), "manual")
    await hass.async_block_till_done()
    assert len(aioclient_mock.mock_calls) == before and not collector.auto_extract


async def test_extraction_unavailable_is_visible_and_original_is_playable(hass, trained, aioclient_mock):
    import asyncio

    entry, _events, _tmp_path = trained
    collector = entry.runtime_data
    aioclient_mock.post(f"{TRAINER}/v1/extract", status=404, json={})
    body = wav_bytes(speech(30))
    [source] = await collector.async_add("office", "", body, "manual")
    await asyncio.gather(*list(collector._extraction_tasks))
    item = next(i for i in await collector.async_list() if i["filename"] == source["filename"])
    assert item["extraction_state"] == "error" and item["extraction_error"]
    assert collector.store.audio("needs_review", "office", source["filename"]).read_bytes() == body
