"""The integration in a Home Assistant test instance."""

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wake_word_collector.const import (
    CONF_CONTROL,
    CONF_PHRASE,
    CONF_SLUG,
    CONF_STORAGE,
    CONF_TOKEN,
    CONF_VARIANTS,
    DOMAIN,
)
from custom_components.wake_word_collector.diagnostics import async_get_config_entry_diagnostics

from .test_collect import silence, speech, wav_bytes

TOKEN = "t" * 43


@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path: Path) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hey Momo",
        unique_id="hey_momo",
        data={CONF_SLUG: "hey_momo", CONF_TOKEN: TOKEN},
        options={
            CONF_PHRASE: "Hey Momo",
            CONF_VARIANTS: "hei momo, hey mumu",
            CONF_CONTROL: "fertig, reicht",
            CONF_STORAGE: str(tmp_path / "clips"),
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def upload(client, transcript: str, body: bytes, token: str = TOKEN, device: str = "kitchen"):
    return await client.post(
        "/api/wake_word_collector/clips/hey_momo",
        data=body,
        headers={
            "X-Wakeword-Token": token,
            "X-Wakeword-Device": device,
            # Like the ESP32: UTF-8 bytes in a latin-1 header.
            "X-Wakeword-Transcript": transcript.encode("utf-8").decode("latin-1"),
        },
    )


async def test_config_flow_shows_device_settings(hass: HomeAssistant, tmp_path: Path) -> None:
    hass.config.config_dir = str(tmp_path)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PHRASE: "!!"})
    assert result["errors"] == {CONF_PHRASE: "phrase_empty"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PHRASE: "Hey Jarvis"})
    assert result["step_id"] == "device_settings"
    placeholders = result["description_placeholders"]
    assert placeholders["url"].endswith("/api/wake_word_collector/clips/hey_jarvis")
    assert len(placeholders["token"]) >= 32
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.data[CONF_TOKEN] == placeholders["token"]
    assert entry.options[CONF_STORAGE] == str(tmp_path / DOMAIN / "hey_jarvis")
    assert entry.options[CONF_CONTROL]  # language default
    await hass.async_block_till_done()
    assert (tmp_path / DOMAIN / "hey_jarvis" / "candidates").is_dir()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PHRASE: "hey jarvis"})
    assert result["type"] is FlowResultType.ABORT


async def test_options_keep_phrase_and_show_token(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["description_placeholders"]["token"] == TOKEN
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_VARIANTS: "hai momo"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PHRASE] == "Hey Momo" and entry.options[CONF_VARIANTS] == "hai momo"


async def test_upload_sorts_and_updates_sensor(hass: HomeAssistant, entry, hass_client_no_auth) -> None:
    client = await hass_client_no_auth()
    response = await upload(client, "Hey Momo, Hey Momo", wav_bytes(speech(0.8) + silence(0.3) + speech(0.8)))
    assert response.status == 201
    assert len((await response.json())["records"]) == 2
    response = await upload(client, "Wie spät ist es", wav_bytes(speech(1.5, 2000)))
    assert (await response.json())["records"][0]["category"] == "needs_review"
    await hass.async_block_till_done()
    state = hass.states.get("sensor.hey_momo_recordings")
    assert state.state == "2"
    assert state.attributes["needs_review"] == 1
    assert state.attributes["last_transcript"] == "Wie spät ist es"


async def test_upload_refuses_bad_token_and_audio(hass: HomeAssistant, entry, hass_client_no_auth) -> None:
    client = await hass_client_no_auth()
    assert (await upload(client, "Hey Momo", wav_bytes(speech(1)), token="wrong")).status == 401
    response = await upload(client, "Hey Momo", b"RIFF....not a wav....................................")
    assert response.status == 400
    assert (await response.json())["error"] == "not_wav"
    response = await client.post("/api/wake_word_collector/clips/other", data=b"x")
    assert response.status == 404


async def test_start_stop_command(hass: HomeAssistant, entry) -> None:
    await hass.services.async_call(DOMAIN, "start", {}, blocking=True)
    assert hass.states.get("sensor.hey_momo_satellite_command").state.startswith("start:*:")
    await hass.services.async_call(DOMAIN, "stop", {"device": "Kitchen"}, blocking=True)
    assert hass.states.get("sensor.hey_momo_satellite_command").state.startswith("stop:kitchen:")


async def test_list_review_and_trim(hass: HomeAssistant, entry, hass_client_no_auth, hass_ws_client, hass_client):
    client = await hass_client_no_auth()
    await upload(client, "Hey Momi", wav_bytes(speech(3)))
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "wake_word_collector/list"})
    reply = await ws.receive_json()
    [collector] = reply["result"]["collectors"]
    [item] = collector["items"]
    assert item["category"] == "needs_review" and item["transcript"] == "Hey Momi"
    audio = await (await hass_client()).get(item["audio_path"])
    assert audio.status == 200 and audio.headers["Content-Type"] == "audio/wav"
    result = await hass.services.async_call(
        DOMAIN,
        "review",
        {"category": "needs_review", "device": "kitchen", "filename": item["filename"], "decision": "accept"},
        blocking=True,
        return_response=True,
    )
    assert result["category"] == "candidates"
    result = await hass.services.async_call(
        DOMAIN,
        "trim",
        {
            "category": "candidates",
            "device": "kitchen",
            "filename": item["filename"],
            "start_ms": 0,
            "end_ms": 1000,
            "mode": "extract",
        },
        blocking=True,
        return_response=True,
    )
    assert result["duration_ms"] == 2000
    assert hass.states.get("sensor.hey_momo_recordings").state == "2"
    with pytest.raises(Exception) as err:
        await hass.services.async_call(
            DOMAIN,
            "trim",
            {"category": "candidates", "device": "kitchen", "filename": item["filename"], "start_ms": 0, "end_ms": 100},
            blocking=True,
        )
    assert getattr(err.value, "translation_key", None) == "duration"


async def test_intents(hass: HomeAssistant, entry, hass_client_no_auth) -> None:
    response = await intent.async_handle(hass, "test", "WakeWordCollectionStart", language="de")
    assert "Aktivierungswort" in response.speech["plain"]["speech"]
    assert hass.states.get("sensor.hey_momo_satellite_command").state.startswith("start:*:")
    client = await hass_client_no_auth()
    await upload(client, "Hey Momo", wav_bytes(speech(1.5)))
    response = await intent.async_handle(hass, "test", "WakeWordCollectionStats", language="en")
    assert "1 usable" in response.speech["plain"]["speech"]
    await intent.async_handle(
        hass, "test", "WakeWordCollectionReview", {"note": {"value": "bad, a car"}, "reject": {"value": True}}
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.hey_momo_recordings").state == "0"
    response = await intent.async_handle(hass, "test", "WakeWordCollectionStop")
    assert hass.states.get("sensor.hey_momo_satellite_command").state.startswith("stop:*:")


async def test_export_for_training(hass: HomeAssistant, entry, hass_client_no_auth) -> None:
    client = await hass_client_no_auth()
    await upload(client, "Hey Momo", wav_bytes(speech(1.5)))
    assert (await client.get("/api/wake_word_collector/export/hey_momo")).status == 401
    reply = await client.get("/api/wake_word_collector/export/hey_momo", headers={"X-Wakeword-Token": TOKEN})
    [candidate] = (await reply.json())["candidates"]
    audio = await client.get(candidate["audio_url"], headers={"X-Wakeword-Token": TOKEN})
    assert audio.status == 200 and len(await audio.read()) > 44


async def test_diagnostics_hide_token(hass: HomeAssistant, entry) -> None:
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert TOKEN not in str(result)
    assert result["accepted"] == ["hey momo", "hei momo", "hey mumu"]
