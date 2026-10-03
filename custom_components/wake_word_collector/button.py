"""Start and stop a training on the Wake Word Trainer service."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .collector import Collector
from .entity import TrainerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    collector: Collector = entry.runtime_data
    if collector.trainer is not None:
        add([StartTraining(collector), StopTraining(collector)])


class StartTraining(TrainerEntity, ButtonEntity):
    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "button", "start_training")

    async def async_press(self) -> None:
        await self.coordinator.client.start(self.coordinator.profile or "recommended")
        await self.coordinator.async_request_refresh()


class StopTraining(TrainerEntity, ButtonEntity):
    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "button", "stop_training")

    async def async_press(self) -> None:
        await self.coordinator.client.stop()
        await self.coordinator.async_request_refresh()
