"""One collector per wake word (config entry): its store and the command the
satellites listen to."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .collect import CollectorError, Phrases, Store
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
        await self.async_refresh()

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

    async def async_add(self, device: str, transcript: str, body: bytes, kind: str = "utterance") -> list[dict]:
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

    async def async_negatives(self) -> list[dict]:
        return await self.hass.async_add_executor_job(self.store.negatives)

    @property
    def model_dir(self) -> Path:
        """Where the trained model for the satellites is kept (from the trainer)."""
        return self.store.root / "model"

    async def async_trim(self, category: str, device: str, filename: str, start_ms, end_ms, mode: str) -> dict:
        return await self._run(self.store.trim, category, device, filename, start_ms, end_ms, mode)

    async def async_list(self) -> list[dict]:
        return await self.hass.async_add_executor_job(self.store.list)
