"""One collector per wake word (config entry): its store and the command the
satellites listen to."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store as SettingsStore
from homeassistant.util import dt as dt_util

from .collect import CATEGORIES, DEVICE_RE, CollectorError, Phrases, Store
from .const import (
    COMMAND_IDLE,
    CONF_CONTROL,
    CONF_PHRASE,
    CONF_SLUG,
    CONF_STORAGE,
    CONF_TOKEN,
    CONF_VARIANTS,
    DOMAIN,
    SIGNAL_UPDATE,
    UPLOAD_URL,
)

NODE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")


def raise_translated(err: CollectorError) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN, translation_key=err.code, translation_placeholders=err.placeholders or None
    )


class Collector:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        options = entry.options
        self.phrases = Phrases.build(
            options[CONF_PHRASE], options.get(CONF_VARIANTS, ""), options.get(CONF_CONTROL, "")
        )
        self.store = Store(Path(options[CONF_STORAGE]), self.phrases)
        self.command = COMMAND_IDLE
        self.stats: dict[str, Any] = {}
        self.last_upload: dict[str, Any] | None = None
        # TrainerCoordinator when a Wake Word Trainer service is configured.
        self.trainer = None
        # SpeakerTest; consumes trigger reports of the device under test.
        self.speaker_test = None
        # Settings changed from entities: announcement guidance, speaker test
        # choices, and which ESPHome node each recording device is.
        self._settings_store = SettingsStore(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.settings: dict[str, Any] = {}

    @property
    def slug(self) -> str:
        return self.entry.data[CONF_SLUG]

    @property
    def token(self) -> str:
        return self.entry.data[CONF_TOKEN]

    @property
    def upload_path(self) -> str:
        return UPLOAD_URL.format(slug=self.slug)

    async def async_setup(self) -> None:
        await self.hass.async_add_executor_job(self.store.ensure)
        self.settings = await self._settings_store.async_load() or {}
        await self.async_refresh()

    @callback
    def update_settings(self, **values: Any) -> None:
        """Change and keep settings; nested dicts are merged."""
        for key, value in values.items():
            if isinstance(value, dict):
                self.settings[key] = {**self.settings.get(key, {}), **value}
            else:
                self.settings[key] = value
        self._settings_store.async_delay_save(lambda: self.settings, 1)
        self.notify()

    async def async_unload(self) -> None:
        if self.settings:
            await self._settings_store.async_save(self.settings)

    @property
    def announcement(self) -> str:
        return self.settings.get("announcement", "")

    @property
    def devices(self) -> list[str]:
        """Every device that sent recordings (also from earlier firmware), or
        that was named in a loudspeaker test (satellites that do not collect)."""
        tested = self.settings.get("speaker_test", {}).get("routes", {})
        return sorted(set(self.stats.get("devices", [])) | set(self.settings.get("nodes", {})) | set(tested))

    def node(self, device: str) -> str | None:
        """ESPHome node name of a recording device, as reported by its firmware."""
        return self.settings.get("nodes", {}).get(device)

    async def async_refresh(self) -> None:
        self.stats = await self.hass.async_add_executor_job(self.store.stats)
        self.notify()

    @callback
    def notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

    @callback
    def send(self, action: str, target: str | None = None) -> None:
        """Command for the satellites. Without a target, the satellite that is
        running the current voice command takes it."""
        target = (target or "*").lower()
        self.command = f"{action}:{target}:{dt_util.utcnow().isoformat()}"
        self.notify()

    async def _run(self, func, *args):
        try:
            result = await self.hass.async_add_executor_job(func, *args)
        except CollectorError as err:
            raise raise_translated(err) from err
        await self.async_refresh()
        return result

    async def async_add(
        self, device: str, transcript: str, body: bytes, kind: str = "utterance", node: str = ""
    ) -> list[dict]:
        device, node = device.lower(), node.lower()
        if node and NODE_RE.fullmatch(node) and DEVICE_RE.fullmatch(device) and self.node(device) != node:
            self.update_settings(nodes={device: node})
        if kind == "trigger" and self.speaker_test is not None and self.speaker_test.consume(device):
            # Played back by the loudspeaker test: an evaluation clip, not a new example.
            return []
        records = await self._run(self.store.add, device, transcript, body, kind)
        self.last_upload = records[-1]
        self.notify()
        return records

    async def async_review(self, category: str, device: str, filename: str, decision: str, note: str = "") -> dict:
        return await self._run(self.store.review, category, device, filename, decision, note)

    async def async_review_latest(self, note: str, decision: str = "note", device: str | None = None) -> dict:
        return await self._run(self.store.review_latest, note, decision, device)

    async def async_review_latest_trigger(self, decision: str, device: str | None = None) -> dict:
        return await self._run(self.store.review_latest_trigger, decision, device)

    async def async_import(self, folder: str, category: str, device: str, note: str = "") -> dict:
        return await self._run(self.store.import_folder, Path(folder), category, device, note)

    async def async_negatives(self) -> list[dict]:
        return await self.hass.async_add_executor_job(self.store.negatives)

    @property
    def model_dir(self) -> Path:
        """Where the trained model for the satellites is kept (from the trainer)."""
        return self.store.root / "model"

    async def async_trim(self, category: str, device: str, filename: str, start_ms, end_ms, mode: str) -> dict:
        return await self._run(self.store.trim, category, device, filename, start_ms, end_ms, mode)

    async def async_list(self) -> list[dict]:
        return await self.hass.async_add_executor_job(self.store.list, CATEGORIES)
