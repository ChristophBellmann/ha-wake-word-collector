"""Which training profile the start button uses (offered by the trainer
service); which satellite and loudspeaker route the loudspeaker test uses."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .collector import Collector
from .const import SIGNAL_UPDATE
from .entity import CollectorEntity, TrainerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    collector: Collector = entry.runtime_data
    if collector.trainer is not None:
        add([TrainingProfile(collector), SpeakerTestDevice(collector), SpeakerTestRoute(collector)])


class TrainingProfile(TrainerEntity, SelectEntity):
    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "select", "training_profile")

    @property
    def options(self) -> list[str]:
        return list(self.status.get("profiles") or {}) or ["recommended"]

    @property
    def current_option(self) -> str | None:
        return self.coordinator.profile

    @property
    def extra_state_attributes(self) -> dict:
        return {"labels": self.status.get("profiles") or {}}

    async def async_select_option(self, option: str) -> None:
        self.coordinator.profile = option
        self.async_write_ha_state()


class SpeakerTestDevice(CollectorEntity, SelectEntity):
    """Devices that sent recordings (their wake_word_device name)."""

    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "select", "speaker_test_device")

    @property
    def options(self) -> list[str]:
        return self.collector.devices or ["-"]

    @property
    def current_option(self) -> str | None:
        return self.collector.speaker_test.device

    @property
    def extra_state_attributes(self) -> dict:
        test = self.collector.speaker_test
        device = test.device
        return {
            "node": self.collector.node(device or ""),
            "satellite": test.satellite(device),
            "detections": test.detections(device),
            "test_switch": test.test_switch(device),
        }

    async def async_select_option(self, option: str) -> None:
        self.collector.update_settings(speaker_test={"device": option})


class SpeakerTestRoute(TrainerEntity, SelectEntity):
    """Loudspeaker routes of the trainer service; remembered per device."""

    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "select", "speaker_test_route")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATE.format(self.collector.entry.entry_id), self.async_write_ha_state
            )
        )

    @property
    def options(self) -> list[str]:
        return self.collector.speaker_test.routes or ["-"]

    @property
    def current_option(self) -> str | None:
        test = self.collector.speaker_test
        return test.route(test.device)

    async def async_select_option(self, option: str) -> None:
        test = self.collector.speaker_test
        if test.device:
            self.collector.update_settings(
                speaker_test={"routes": {**test.choices.get("routes", {}), test.device: option}}
            )
