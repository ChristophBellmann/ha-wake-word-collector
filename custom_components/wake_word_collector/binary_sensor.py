"""Is the training computer (Wake Word Trainer service) reachable?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .collector import Collector
from .entity import TrainerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    collector: Collector = entry.runtime_data
    if collector.trainer is not None:
        add([TrainerOnline(collector)])


class TrainerOnline(TrainerEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "binary_sensor", "trainer")

    @property
    def available(self) -> bool:
        return True  # "off" is the answer while the computer is off

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.last_update_success and self.status.get("workstation_online"))
