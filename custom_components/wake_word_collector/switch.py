"""Optional automatic learning from wake-word activations without a command."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .collector import Collector
from .entity import CollectorEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([AutomaticFalsePositives(entry.runtime_data)])


class AutomaticFalsePositives(CollectorEntity, SwitchEntity):
    _attr_translation_key = "auto_learn_false_positives"
    _attr_icon = "mdi:school"

    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "switch", "auto_learn_false_positives")

    @property
    def is_on(self) -> bool:
        return self.collector.auto_learn_false_positives

    async def async_turn_on(self, **kwargs) -> None:
        self.collector.update_settings(auto_learn_false_positives=True)

    async def async_turn_off(self, **kwargs) -> None:
        self.collector.update_settings(auto_learn_false_positives=False)
