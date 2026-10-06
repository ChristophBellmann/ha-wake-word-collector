"""One collector per wake word (config entry): its store and the command the
satellites listen to."""

from __future__ import annotations

import asyncio
import re
import threading
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
        # Rollout when a model_update configuration is set (rollout_config).
        self.rollout = None
        # Settings changed from entities: announcement guidance, speaker test
        # choices, and which ESPHome node each recording device is.
        self._settings_store = SettingsStore(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.settings: dict[str, Any] = {}
        self._storage_lock = threading.RLock()
        self._extraction_lock = asyncio.Lock()
        self._extraction_tasks: set[asyncio.Task] = set()

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
        for task in self._extraction_tasks:
            task.cancel()
        if self._extraction_tasks:
            await asyncio.gather(*self._extraction_tasks, return_exceptions=True)
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
        self.stats = await self.hass.async_add_executor_job(self._store_call, self.store.stats)
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

    def _store_call(self, func, *args):
        with self._storage_lock:
            return func(*args)

    @property
    def auto_extract(self) -> bool:
        return self.settings.get("auto_extract", True)

    async def async_resume_extractions(self) -> None:
        if self.trainer is None or not self.auto_extract:
            return
        for record in await self.async_list():
            if record.get("kind") == "manual" and record.get("extraction_state") in (
                "pending",
                "running",
                "interrupted",
            ):
                self._queue_extraction(record)

    def _queue_extraction(self, record: dict) -> None:
        task = self.hass.async_create_background_task(self._automatic_extract(record), "wake word extraction")
        self._extraction_tasks.add(task)
        task.add_done_callback(self._extraction_tasks.discard)

    async def _automatic_extract(self, record: dict) -> None:
        try:
            await self.async_extract(record["category"], record["device"], record["filename"])
        except HomeAssistantError:
            # The original is already saved. The error is visible on its card;
            # the user can retry once the workstation is available.
            pass

    async def async_extract(self, category: str, device: str, filename: str) -> dict:
        async with self._extraction_lock:
            await self._run(self.store.extraction_status, device, filename, "running")
            try:
                if self.trainer is None:
                    raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_trainer")
                body, sha = await self._run(self.store.extraction_snapshot, category, device, filename)
                result = await self.trainer.client.extract(body, [" ".join(p) for p in self.phrases.accepted])
                return await self._run(self.store.extract_phrases, category, device, filename, sha, result["segments"])
            except HomeAssistantError as err:
                await self._run(self.store.extraction_status, device, filename, "error", str(err))
                raise
            except asyncio.CancelledError:
                await self._run(self.store.extraction_status, device, filename, "interrupted")
                raise

    async def _run(self, func, *args):
        try:
            result = await self.hass.async_add_executor_job(self._store_call, func, *args)
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
        if (
            kind in ("trigger", "trigger_no_input")
            and self.speaker_test is not None
            and self.speaker_test.consume(device)
        ):
            # Played back by the loudspeaker test: an evaluation clip, not a new example.
            return []
        records = await self._run(self.store.add, device, transcript, body, kind, self.auto_learn_false_positives)
        if kind == "manual" and self.auto_extract and self.trainer is not None:
            for record in records:
                await self._run(self.store.extraction_status, device, record["filename"], "pending")
                self._queue_extraction(record)
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
    def auto_learn_false_positives(self) -> bool:
        return bool(self.settings.get("auto_learn_false_positives", False))

    @property
    def auto_rollout(self) -> bool:
        return bool(self.settings.get("auto_rollout", False))

    @property
    def model_dir(self) -> Path:
        """Where the trained model for the satellites is kept (from the trainer)."""
        return self.store.root / "model"

    async def async_trim(self, category: str, device: str, filename: str, start_ms, end_ms, mode: str) -> dict:
        return await self._run(self.store.trim, category, device, filename, start_ms, end_ms, mode)

    async def async_list(self) -> list[dict]:
        return await self.hass.async_add_executor_job(self._store_call, self.store.list, CATEGORIES)
