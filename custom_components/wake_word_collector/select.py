"""Which training profile the start button uses (offered by the trainer service)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .collector import Collector
from .entity import TrainerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    collector: Collector = entry.runtime_data
    if collector.trainer is not None:
        add([TrainingProfile(collector)])


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
