"""Start and stop a training on the Wake Word Trainer service; run the loudspeaker test;
roll out the model to the satellites."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .collector import Collector
from .entity import CollectorEntity, TrainerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    collector: Collector = entry.runtime_data
    if collector.trainer is not None:
        add([StartTraining(collector), StopTraining(collector), RunSpeakerTest(collector)])
    if collector.rollout is not None:
        add([RolloutModel(collector)])


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


class RunSpeakerTest(TrainerEntity, ButtonEntity):
    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "button", "run_speaker_test")

    async def async_press(self) -> None:
        # Runs in the background; the result appears in sensor ..._speaker_test.
        self.collector.speaker_test.async_start()


class RolloutModel(CollectorEntity, ButtonEntity):
    _attr_icon = "mdi:cellphone-arrow-down"

    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "button", "rollout_model")

    async def async_press(self) -> None:
        # Installs in the background; progress in sensor ..._rollout.
        await self.collector.rollout.async_start()
