"""Model rollout from Home Assistant through the ESPHome Device Builder."""

import asyncio
import json
from collections import deque
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wake_word_collector import rollout as rollout_module
from custom_components.wake_word_collector.const import (
    CONF_ESPHOME_URL,
    CONF_PHRASE,
    CONF_ROLLOUT_CONFIG,
    CONF_SLUG,
    CONF_STORAGE,
    CONF_TOKEN,
    CONF_TRAINER_TOKEN,
    CONF_TRAINER_URL,
    DOMAIN,
)

from .test_integration import TOKEN
from .test_training import STATUS, TRAINER

DASHBOARD = "http://builder.local:6052"
DEVICE = """substitutions:
  name: kitchen
  wake_word_model_file: models/old.json
  wake_word_slight_cutoff_uint8: "246"
  wake_word_moderate_cutoff_uint8: "217"
  wake_word_very_cutoff_uint8: "190"
"""
REPORT = {
    "probability_cutoff": 0.8,
    "recall": 0.95,
    "false_accepts_per_hour": 0.3,
    "max_false_accepts_per_hour": 0.5,
    "curve": [{"cutoff": c / 100, "recall": 1 - c / 300, "faph": max(0, (90 - c) / 10)} for c in range(50, 100, 5)],
}


class FakeBuilder:
    """Device Builder and the satellite: an installed firmware gets a new compilation time."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.compile_ok = True
        self.firmware = "Oct  1 2026, 10:00:00"
        self.known = {"kitchen.yaml": {"name": "kitchen", "configuration": "kitchen.yaml"}}

    def client(self, session, url):
        builder = self

        class Client:
            async def devices(self):
                builder.calls.append(("devices", url))
                return builder.known

            async def compile(self, configuration, lines):
                builder.calls.append(("compile", configuration))
                lines.append("compiling")
                return builder.compile_ok

            async def upload(self, configuration, port, lines):
                builder.calls.append(("upload", configuration, port))
                builder.firmware = f"Oct  6 2026, 10:00:{len(builder.calls):02d}"
                return True

        return Client()

    def device_data(self, node):
        if node != "kitchen":
            return None
        return SimpleNamespace(available=True, device_info=SimpleNamespace(name=node, compilation_time=self.firmware))


@pytest.fixture
def builder(monkeypatch) -> FakeBuilder:
    fake = FakeBuilder()
    monkeypatch.setattr(rollout_module, "DashboardClient", fake.client)
    monkeypatch.setattr(rollout_module.Rollout, "_device_data", lambda rollout, node: fake.device_data(node))
    return fake


def write_model(folder: Path, report: dict | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "hey_nova.tflite").write_bytes(b"TFL3")
    (folder / "hey_nova.json").write_text(
        json.dumps({"type": "micro", "model": "hey_nova.tflite", "micro": {"probability_cutoff": 0.8}})
    )
    (folder / "source.json").write_text(
        json.dumps({"ended_at": "2026-10-06T03:00:00+00:00", "report": report if report is not None else REPORT})
    )


def setup_esphome(tmp_path: Path, **config) -> Path:
    esphome = tmp_path / "esphome"
    esphome.mkdir()
    (esphome / "kitchen.yaml").write_text(DEVICE)
    config = {
        "slug": "hey_nova",
        # ESPHome may see the storage under another path: the integration uses its own.
        "source": "/elsewhere/wake_word_collector/hey_nova/model",
        "devices": [{"file": "kitchen.yaml", "address": "192.168.1.5", "model": "wake_word_model_file"}],
        **config,
    }
    (esphome / "wake-word-model.yaml").write_text(json.dumps(config))
    return esphome


async def setup_entry(hass: HomeAssistant, tmp_path: Path, **options) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hey Nova",
        unique_id="hey_nova",
        data={CONF_SLUG: "hey_nova", CONF_TOKEN: TOKEN},
        options={
            CONF_PHRASE: "Hey Nova",
            CONF_STORAGE: str(tmp_path / "clips"),
            CONF_ROLLOUT_CONFIG: str(tmp_path / "esphome" / "wake-word-model.yaml"),
            CONF_ESPHOME_URL: DASHBOARD,
            **options,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def finish(entry: MockConfigEntry) -> None:
    task = entry.runtime_data.rollout._task
    if task is not None:
        await task


async def test_rollout_installs_verifies_and_skips_verified(hass: HomeAssistant, tmp_path: Path, builder) -> None:
    esphome = setup_esphome(tmp_path, name="{slug}_{date}_{hash}")
    write_model(tmp_path / "clips" / "model")
    entry = await setup_entry(hass, tmp_path)
    assert hass.states.get("sensor.hey_nova_rollout").state == "idle"
    assert hass.states.get("switch.hey_nova_auto_rollout").state == "off"
    events = []
    hass.bus.async_listen(f"{DOMAIN}_rollout_finished", events.append)

    plan = await hass.services.async_call(
        DOMAIN, "rollout_model", {"dry_run": True}, blocking=True, return_response=True
    )
    assert plan["dry_run"] and plan["changed"] == ["kitchen.yaml"]
    assert plan["devices"]["kitchen.yaml"]["wake_word_slight_cutoff_uint8"] == 204
    assert (esphome / "kitchen.yaml").read_text() == DEVICE and not builder.calls

    await hass.services.async_call("button", "press", {"entity_id": "button.hey_nova_rollout_model"}, blocking=True)
    await finish(entry)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.hey_nova_rollout")
    assert state.state == "completed", state.attributes
    assert state.attributes["devices"] == {"kitchen.yaml": "verified"}
    assert builder.calls == [
        ("devices", DASHBOARD),
        ("compile", "kitchen.yaml"),
        ("upload", "kitchen.yaml", "192.168.1.5"),
    ]
    name = state.attributes["model"]
    assert name.startswith("hey_nova_20261006_") and (esphome / "models" / name).is_file()
    assert f"wake_word_model_file: models/{name}\n" in (esphome / "kitchen.yaml").read_text()
    ledger = json.loads((tmp_path / "clips" / "model" / "rollout.json").read_text())
    assert ledger["devices"]["kitchen.yaml"]["verified"] is True
    assert len(events) == 1 and events[0].data["state"] == "completed"

    # The same model again: already verified, nothing is built.
    builder.calls.clear()
    await hass.services.async_call(DOMAIN, "rollout_model", {}, blocking=True, return_response=True)
    await finish(entry)
    assert builder.calls == [("devices", DASHBOARD)]
    assert hass.states.get("sensor.hey_nova_rollout").state == "completed"


async def test_failed_device_is_retried(hass: HomeAssistant, tmp_path: Path, builder) -> None:
    setup_esphome(tmp_path)
    write_model(tmp_path / "clips" / "model")
    entry = await setup_entry(hass, tmp_path)
    builder.compile_ok = False
    await hass.services.async_call(DOMAIN, "rollout_model", {}, blocking=True, return_response=True)
    with pytest.raises(HomeAssistantError, match="rollout_running|already running"):
        await hass.services.async_call(DOMAIN, "rollout_model", {}, blocking=True, return_response=True)
    await finish(entry)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.hey_nova_rollout")
    assert state.state == "failed" and state.attributes["devices"] == {"kitchen.yaml": "compile failed"}
    ledger = json.loads((tmp_path / "clips" / "model" / "rollout.json").read_text())
    assert ledger["devices"]["kitchen.yaml"]["log"] == ["compiling"]

    builder.compile_ok = True
    await hass.services.async_call(DOMAIN, "rollout_model", {}, blocking=True, return_response=True)
    await finish(entry)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.hey_nova_rollout").state == "completed"


async def test_unknown_device_and_unverified_firmware(
    hass: HomeAssistant, tmp_path: Path, builder, monkeypatch
) -> None:
    setup_esphome(tmp_path)
    write_model(tmp_path / "clips" / "model")
    entry = await setup_entry(hass, tmp_path)
    builder.known = {}
    await hass.services.async_call(DOMAIN, "rollout_model", {}, blocking=True, return_response=True)
    await finish(entry)
    await hass.async_block_till_done()
    attributes = hass.states.get("sensor.hey_nova_rollout").attributes
    assert attributes["devices"] == {"kitchen.yaml": "not in the ESPHome Device Builder"}

    # Installed, but the satellite never reports another firmware.
    builder.known = {"kitchen.yaml": {"name": "kitchen"}}
    monkeypatch.setattr(rollout_module, "VERIFY_INTERVAL", 0)
    monkeypatch.setattr(rollout_module, "VERIFY_TIMEOUT", rollout_module.timedelta(seconds=0.05))
    builder.device_data = lambda node: SimpleNamespace(
        available=True, device_info=SimpleNamespace(name=node, compilation_time="old")
    )
    await hass.services.async_call(DOMAIN, "rollout_model", {}, blocking=True, return_response=True)
    await finish(entry)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.hey_nova_rollout")
    assert state.state == "failed" and state.attributes["devices"] == {"kitchen.yaml": "not verified"}


async def test_parity_guard_blocks_rollout(hass: HomeAssistant, tmp_path: Path, builder) -> None:
    esphome = setup_esphome(tmp_path, require_parity=True)
    write_model(tmp_path / "clips" / "model", {**REPORT, "parity": {"passed": False}})
    await setup_entry(hass, tmp_path)
    with pytest.raises(HomeAssistantError, match="parity"):
        await hass.services.async_call(DOMAIN, "rollout_model", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.hey_nova_rollout").state == "blocked"
    assert (esphome / "kitchen.yaml").read_text() == DEVICE and not builder.calls


async def test_no_rollout_without_configuration(hass: HomeAssistant, tmp_path: Path) -> None:
    await setup_entry(hass, tmp_path, **{CONF_ROLLOUT_CONFIG: "", CONF_ESPHOME_URL: ""})
    assert hass.states.get("sensor.hey_nova_rollout") is None
    assert hass.states.get("button.hey_nova_rollout_model") is None
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "rollout_model", {}, blocking=True, return_response=True)


async def test_automatic_rollout_after_training(
    hass: HomeAssistant, tmp_path: Path, builder, aioclient_mock: AiohttpClientMocker, hass_storage
) -> None:
    setup_esphome(tmp_path)
    manifest = {"type": "micro", "model": "hey_nova.tflite", "micro": {"probability_cutoff": 0.8}}
    aioclient_mock.get(
        f"{TRAINER}/v1/status",
        json={**STATUS, "state": "completed", "best_model_available": True, "ended_at": "2026-10-06T03:00:00+00:00"},
    )
    aioclient_mock.get(f"{TRAINER}/v1/model/hey_nova.json", text=json.dumps(manifest))
    aioclient_mock.get(f"{TRAINER}/v1/model/hey_nova.tflite", content=b"TFL3-new")
    aioclient_mock.get(f"{TRAINER}/v1/report", json=REPORT)
    entry_id = "rollout_entry"
    hass_storage[f"{DOMAIN}.{entry_id}"] = {"version": 1, "key": f"{DOMAIN}.{entry_id}", "data": {"auto_rollout": True}}
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        title="Hey Nova",
        unique_id="hey_nova",
        data={CONF_SLUG: "hey_nova", CONF_TOKEN: TOKEN},
        options={
            CONF_PHRASE: "Hey Nova",
            CONF_STORAGE: str(tmp_path / "clips"),
            CONF_TRAINER_URL: TRAINER,
            CONF_TRAINER_TOKEN: "s" * 24,
            CONF_ROLLOUT_CONFIG: str(tmp_path / "esphome" / "wake-word-model.yaml"),
            CONF_ESPHOME_URL: DASHBOARD,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await finish(entry)
    await hass.async_block_till_done()
    assert hass.states.get("switch.hey_nova_auto_rollout").state == "on"
    assert hass.states.get("sensor.hey_nova_rollout").state == "completed"
    assert ("upload", "kitchen.yaml", "192.168.1.5") in builder.calls


async def test_options_check_rollout(hass: HomeAssistant, tmp_path: Path, aioclient_mock: AiohttpClientMocker) -> None:
    setup_esphome(tmp_path)
    entry = await setup_entry(hass, tmp_path, **{CONF_ROLLOUT_CONFIG: "", CONF_ESPHOME_URL: ""})
    (tmp_path / "bad.yaml").write_text("slug: x\n")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ROLLOUT_CONFIG: str(tmp_path / "bad.yaml")}
    )
    assert result["errors"] == {CONF_ROLLOUT_CONFIG: "rollout_config_invalid"}
    aioclient_mock.get(f"{DASHBOARD}/devices", status=500)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ROLLOUT_CONFIG: str(tmp_path / "esphome" / "wake-word-model.yaml"), CONF_ESPHOME_URL: DASHBOARD},
    )
    assert result["errors"] == {CONF_ESPHOME_URL: "esphome_unreachable"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{DASHBOARD}/devices", json={"configured": [], "importable": []})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ROLLOUT_CONFIG: str(tmp_path / "esphome" / "wake-word-model.yaml"), CONF_ESPHOME_URL: DASHBOARD + "/"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ESPHOME_URL] == DASHBOARD


async def builder_server(silence: float):
    """A Device Builder job that stays silent like an OTA to a slow device and never reads the socket."""

    async def job(request):
        socket = web.WebSocketResponse(autoping=False)
        await socket.prepare(request)
        await socket.send_json({"event": "line", "data": "Uploading"})
        await asyncio.sleep(silence)
        await socket.send_json({"event": "exit", "code": 0})
        return socket

    app = web.Application()
    app.router.add_get("/upload", job)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    return server


async def test_silent_upload_is_not_cut_off(monkeypatch, socket_enabled) -> None:
    monkeypatch.setattr(rollout_module, "UPLOAD_TIMEOUT", timedelta(seconds=5))
    server = await builder_server(silence=1.5)
    lines: deque[str] = deque()
    async with aiohttp.ClientSession() as session:
        client = rollout_module.DashboardClient(session, str(server.make_url("")))
        assert await client.upload("kitchen.yaml", "OTA", lines)
    await server.close()
    assert list(lines) == ["Uploading"]


async def test_upload_ends_at_its_time_limit(monkeypatch, socket_enabled) -> None:
    monkeypatch.setattr(rollout_module, "UPLOAD_TIMEOUT", timedelta(seconds=0.3))
    server = await builder_server(silence=5)
    lines: deque[str] = deque()
    async with aiohttp.ClientSession() as session:
        client = rollout_module.DashboardClient(session, str(server.make_url("")))
        assert not await client.upload("kitchen.yaml", "OTA", lines)
    await server.close()
    assert lines[-1] == "upload did not finish within 0:00:00.300000"
