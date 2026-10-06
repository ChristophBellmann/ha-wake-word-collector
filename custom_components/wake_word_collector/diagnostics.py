"""Diagnostics: settings and statistics, without the token and without audio."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN, CONF_TRAINER_TOKEN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    collector = entry.runtime_data
    await collector.async_refresh()
    return {
        "data": async_redact_data(dict(entry.data), {CONF_TOKEN}),
        "options": async_redact_data(dict(entry.options), {CONF_TRAINER_TOKEN}),
        "upload_path": collector.upload_path,
        "accepted": [" ".join(words) for words in collector.phrases.accepted],
        "stats": collector.stats,
        "command": collector.command,
        "last_upload": {k: v for k, v in (collector.last_upload or {}).items() if k != "transcript"},
        "trainer": None
        if collector.trainer is None
        else {
            "available": collector.trainer.last_update_success,
            "state": (collector.trainer.data or {}).get("state"),
            "model": collector.trainer.model,
        },
        "rollout": None if collector.rollout is None else collector.rollout.result,
    }
