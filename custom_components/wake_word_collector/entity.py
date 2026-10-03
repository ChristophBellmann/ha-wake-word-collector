"""Common base: one device per wake word, entity ids fixed in every language
(satellites, blueprints and dashboards refer to them)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SLUG, DOMAIN, SIGNAL_UPDATE
from .trainer import TrainerCoordinator

if TYPE_CHECKING:
    from .collector import Collector


def _setup(entity: Entity, collector: Collector, platform: str, key: str) -> None:
    entity._attr_has_entity_name = True
    entity._attr_translation_key = key
    entity._attr_unique_id = f"{collector.entry.entry_id}_{key}"
    entity.entity_id = f"{platform}.{collector.entry.data[CONF_SLUG]}_{key}"
    entity._attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, collector.entry.entry_id)},
        name=collector.entry.title,
        entry_type=DeviceEntryType.SERVICE,
        manufacturer="Wake Word Collector",
    )


class CollectorEntity(Entity):
    """Updated whenever the collector changes."""

    _attr_should_poll = False

    def __init__(self, collector: Collector, platform: str, key: str) -> None:
        self.collector = collector
        _setup(self, collector, platform, key)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATE.format(self.collector.entry.entry_id), self.async_write_ha_state
            )
        )


class TrainerEntity(CoordinatorEntity[TrainerCoordinator]):
    """Follows the trainer service; unavailable while the training computer is off."""

    def __init__(self, collector: Collector, platform: str, key: str) -> None:
        assert collector.trainer is not None
        super().__init__(collector.trainer)
        self.collector = collector
        _setup(self, collector, platform, key)

    @property
    def status(self) -> dict:
        return self.coordinator.data or {}
