"""How many recordings one loudspeaker test plays."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .collector import Collector
from .entity import CollectorEntity
from .speaker_test import MAX_CLIPS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    collector: Collector = entry.runtime_data
    if collector.speaker_test is not None:
        add([SpeakerTestClips(collector)])


class SpeakerTestClips(CollectorEntity, NumberEntity):
    _attr_native_min_value = 1
    _attr_native_max_value = MAX_CLIPS
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "number", "speaker_test_clips")

    @property
    def native_value(self) -> int:
        return self.collector.speaker_test.clips

    async def async_set_native_value(self, value: float) -> None:
        self.collector.update_settings(speaker_test={"clips": int(value)})
