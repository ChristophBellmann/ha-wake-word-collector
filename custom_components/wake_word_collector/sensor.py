"""Sensors: number of usable recordings; the command the satellites listen to."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .collector import Collector
from .const import DOMAIN, SIGNAL_UPDATE


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    collector: Collector = entry.runtime_data
    add([RecordingsSensor(collector, "recordings"), CommandSensor(collector, "satellite_command")])


class _Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, collector: Collector, key: str) -> None:
        self.collector = collector
        self._attr_translation_key = key
        self._attr_unique_id = f"{collector.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, collector.entry.entry_id)},
            name=collector.entry.title,
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Wake Word Collector",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATE.format(self.collector.entry.entry_id), self.async_write_ha_state
            )
        )


class RecordingsSensor(_Base):
    _attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> int:
        return self.collector.stats.get("candidates", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        stats = self.collector.stats
        last = self.collector.last_upload or {}
        return {
            "needs_review": stats.get("needs_review", 0),
            "control": stats.get("control", 0),
            "rejected_quality": stats.get("rejected_quality", 0),
            "total": stats.get("total", 0),
            "candidates_by_device": stats.get("candidates_by_device", {}),
            "latest_recording_at": stats.get("latest_recording_at"),
            "last_category": last.get("category"),
            "last_device": last.get("device"),
            "last_transcript": last.get("transcript"),
        }


class CommandSensor(_Base):
    """`<start|stop>:<device or *>:<time>`; read by the ESPHome package."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str:
        return self.collector.command
