"""How the assistant announces a new recording session; free text the user
can change by voice (intent WakeWordCollectionAnnouncement) or here."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .collector import Collector
from .entity import CollectorEntity

MAX_LENGTH = 255


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([Announcement(entry.runtime_data)])


class Announcement(CollectorEntity, TextEntity):
    _attr_native_max = MAX_LENGTH

    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "text", "announcement")

    @property
    def native_value(self) -> str:
        return self.collector.announcement

    async def async_set_value(self, value: str) -> None:
        self.collector.update_settings(announcement=value.strip()[:MAX_LENGTH])
