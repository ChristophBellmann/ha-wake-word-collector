"""Announcement guidance and the loudspeaker test."""

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wake_word_collector import speaker_test
from custom_components.wake_word_collector.const import DOMAIN

from .test_collect import speech, wav_bytes
from .test_integration import TOKEN
from .test_training import entry, trained  # noqa: F401 (fixtures)


async def _report(client, device: str = "kitchen", node: str = "kitchen-node"):
    return await client.post(
        "/api/wake_word_collector/clips/hey_nova",
        data=wav_bytes(speech(4)),
        headers={
            "X-Wakeword-Token": TOKEN,
            "X-Wakeword-Device": device,
            "X-Wakeword-Kind": "trigger",
            "X-Wakeword-Node": node,
        },
    )


async def test_announcement(hass: HomeAssistant, entry) -> None:  # noqa: F811
    state = hass.states.get("text.hey_nova_announcement")
    assert state is not None and state.state == ""
    plain = await intent.async_handle(hass, "test", "WakeWordCollectionStart", language="en")
    assert plain.speech_slots == {}
    result = await intent.async_handle(
        hass,
        "test",
        "WakeWordCollectionAnnouncement",
        {"guidance": {"value": "Ankündigen wie ein Pirat"}},
        language="de",
    )
    assert "Ab jetzt" in result.speech["plain"]["speech"]
    assert hass.states.get("text.hey_nova_announcement").state == "Ankündigen wie ein Pirat"
    start = await intent.async_handle(hass, "test", "WakeWordCollectionStart", language="de")
    assert start.speech_slots == {"announcement": "Ankündigen wie ein Pirat"}
    # Kept over a restart of the integration.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("text.hey_nova_announcement").state == "Ankündigen wie ein Pirat"
    await hass.services.async_call(
        "text", "set_value", {"entity_id": "text.hey_nova_announcement", "value": ""}, blocking=True
    )
    await intent.async_handle(hass, "test", "WakeWordCollectionAnnouncement", {"guidance": {"value": ""}})
    assert hass.states.get("text.hey_nova_announcement").state == ""
    # Without a trainer there is no loudspeaker test.
    assert hass.states.get("button.hey_nova_run_speaker_test") is None


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(speaker_test, "WINDOW_WITH_SATELLITE", 0.3)
    monkeypatch.setattr(speaker_test, "WINDOW_REPORT_ONLY", 0.3)


async def test_speaker_test_with_satellite(hass: HomeAssistant, trained, hass_client_no_auth, quick) -> None:  # noqa: F811
    config_entry, _, _ = trained
    collector = config_entry.runtime_data
    client = await hass_client_no_auth()
    # The satellite's ESPHome device, found through the node name its firmware reports.
    esphome = MockConfigEntry(domain="esphome", data={"device_name": "kitchen-node"})
    esphome.add_to_hass(hass)
    satellite = er.async_get(hass).async_get_or_create(
        "assist_satellite", "esphome", "kitchen-sat", config_entry=esphome, suggested_object_id="kitchen"
    )
    hass.states.async_set(satellite.entity_id, "idle")
    assert (await _report(client)).status == 201  # a normal activation report: stored
    assert collector.stats["triggers"] == 1
    await hass.async_block_till_done()
    device = hass.states.get("select.hey_nova_speaker_test_device")
    assert device.state == "kitchen" and device.attributes["satellite"] == satellite.entity_id
    route = hass.states.get("select.hey_nova_speaker_test_route")
    assert route.state == "speakers" and route.attributes["options"] == ["speakers", "usb"]
    await hass.services.async_call(
        "select", "select_option", {"entity_id": route.entity_id, "option": "usb"}, blocking=True
    )

    played = []

    async def play(route: str) -> dict:
        played.append(route)
        if len(played) != 2:  # the satellite recognizes clips 1 and 3
            hass.states.async_set(satellite.entity_id, "listening")
            hass.loop.call_later(0.05, hass.states.async_set, satellite.entity_id, "idle")
            # Its late report of the same activation must neither count again nor be stored.
            assert (await _report(client)).status == 201
        return {"route": route}

    collector.trainer.client.speaker_test = play
    result = await hass.services.async_call(
        DOMAIN, "run_speaker_test", {"clips": 3}, blocking=True, return_response=True
    )
    assert played == ["usb"] * 3
    assert result["state"] == "done" and (result["played"], result["detected"]) == (3, 2)
    assert collector.stats["triggers"] == 1
    sensor = hass.states.get("sensor.hey_nova_speaker_test")
    assert float(sensor.state) == pytest.approx(66.7) and sensor.attributes["route"] == "usb"
    assert hass.states.get("number.hey_nova_speaker_test_clips").state == "3"
    # Shortly after the test, reports of the device are still played-back clips.
    assert (await _report(client)).status == 201
    assert collector.stats["triggers"] == 1


async def test_speaker_test_by_reports(hass: HomeAssistant, trained, hass_client_no_auth, quick) -> None:  # noqa: F811
    config_entry, _, _ = trained
    collector = config_entry.runtime_data
    client = await hass_client_no_auth()
    assert (await _report(client, node="")).status == 201
    played = []

    async def play(route: str) -> dict:
        played.append(route)
        if len(played) == 2:
            await collector.async_add("kitchen", "", wav_bytes(speech(4)), "trigger")
        return {"route": route}

    collector.trainer.client.speaker_test = play
    result = await collector.speaker_test.async_run(clips=2)
    assert (result["played"], result["detected"], result["satellite"]) == (2, 1, None)
    assert collector.stats["triggers"] == 1

    # Button: runs in the background; a second start is refused while it runs.
    gate = asyncio.Event()

    async def slow(route: str) -> dict:
        await gate.wait()
        return {}

    collector.trainer.client.speaker_test = slow
    await hass.services.async_call("button", "press", {"entity_id": "button.hey_nova_run_speaker_test"}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=False)
    assert hass.states.get("sensor.hey_nova_speaker_test").attributes["state"] == "running"
    with pytest.raises(HomeAssistantError, match="already running"):
        await hass.services.async_call(DOMAIN, "run_speaker_test", {}, blocking=True, return_response=True)
    gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.hey_nova_speaker_test").attributes["state"] == "done"


async def test_speaker_test_needs_a_satellite_that_works(hass: HomeAssistant, trained, quick) -> None:  # noqa: F811
    config_entry, _, _ = trained
    collector = config_entry.runtime_data
    with pytest.raises(HomeAssistantError, match="No satellite"):
        await collector.speaker_test.async_run()
    await collector.async_add("kitchen", "", wav_bytes(speech(4)), "trigger")
    hass.states.async_set("assist_satellite.kitchen", "unavailable")
    result = await collector.speaker_test.async_run(satellite="assist_satellite.kitchen")
    assert result["state"] == "failed" and "not available" in result["message"]


async def test_speaker_test_counter_and_test_mode(hass: HomeAssistant, trained, hass_client_no_auth, quick) -> None:  # noqa: F811
    config_entry, _, _ = trained
    collector = config_entry.runtime_data
    client = await hass_client_no_auth()
    esphome = MockConfigEntry(domain="esphome", data={"device_name": "atom-node"})
    esphome.add_to_hass(hass)
    registry = er.async_get(hass)
    satellite = registry.async_get_or_create(
        "assist_satellite", "esphome", "atom-sat", config_entry=esphome, suggested_object_id="atom"
    )
    counter = registry.async_get_or_create(
        "sensor", "esphome", "aa:bb-sensor-wake_word_detections", config_entry=esphome, suggested_object_id="atom_det"
    )
    hass.states.async_set(satellite.entity_id, "idle")
    hass.states.async_set(counter.entity_id, "unknown")
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"atom_test_mode": {}}})
    assert (await _report(client, device="atom", node="atom-node")).status == 201
    detections = 0
    modes = []

    async def play(route: str) -> dict:
        nonlocal detections
        modes.append(hass.states.get("input_boolean.atom_test_mode").state)
        if len(modes) != 3:  # in test mode the satellite only counts, it stays idle
            detections += 1
            hass.states.async_set(counter.entity_id, str(detections))
        return {"route": route}

    collector.trainer.client.speaker_test = play
    result = await hass.services.async_call(
        DOMAIN,
        "run_speaker_test",
        {"device": "atom", "clips": 3, "test_switch": "input_boolean.atom_test_mode"},
        blocking=True,
        return_response=True,
    )
    assert modes == ["on", "on", "on"]
    assert hass.states.get("input_boolean.atom_test_mode").state == "off"
    assert (result["detected"], result["detections"], result["satellite"]) == (
        2,
        counter.entity_id,
        satellite.entity_id,
    )
    # The test mode switch is remembered for this satellite.
    assert collector.speaker_test.test_switch("atom") == "input_boolean.atom_test_mode"
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.hey_nova_speaker_test_device", "option": "atom"}, blocking=True
    )
    attributes = hass.states.get("select.hey_nova_speaker_test_device").attributes
    assert attributes["test_switch"] == "input_boolean.atom_test_mode"
    assert attributes["detections"] == counter.entity_id


async def test_satellite_without_recordings(hass: HomeAssistant, trained, quick) -> None:  # noqa: F811
    config_entry, _, _ = trained
    collector = config_entry.runtime_data
    hass.states.async_set("assist_satellite.door", "idle")

    async def play(route: str) -> dict:
        return {"route": route}

    collector.trainer.client.speaker_test = play
    result = await collector.speaker_test.async_run(
        device="door", route="usb", clips=1, satellite="assist_satellite.door"
    )
    assert result["played"] == 1 and result["detected"] == 0
    await hass.async_block_till_done()
    # Named once, it stays selectable with its satellite and route.
    assert "door" in hass.states.get("select.hey_nova_speaker_test_device").attributes["options"]
    assert collector.speaker_test.satellite("door") == "assist_satellite.door"
    assert collector.speaker_test.route("door") == "usb"
