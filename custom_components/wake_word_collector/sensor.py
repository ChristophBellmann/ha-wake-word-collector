"""Sensors: number of usable recordings; the command the satellites listen to."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .collector import Collector
from .entity import CollectorEntity, TrainerEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    collector: Collector = entry.runtime_data
    entities: list[SensorEntity] = [
        RecordingsSensor(collector, "recordings"),
        CommandSensor(collector, "satellite_command"),
    ]
    if collector.trainer is not None:
        entities += [TrainingSensor(collector), TrainingProgressSensor(collector), ModelSensor(collector)]
    add(entities)


class _Base(CollectorEntity, SensorEntity):
    def __init__(self, collector: Collector, key: str) -> None:
        super().__init__(collector, "sensor", key)


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


# -- Training (with a Wake Word Trainer service) ----------------------------------

STATUS_ATTRIBUTES = (
    "message",
    "profile",
    "phase",
    "round_current",
    "rounds_total",
    "step_current",
    "steps_total",
    "accepted_rounds",
    "rejected_rounds",
    "best_model_available",
    "best_recall",
    "best_faph",
    "best_threshold",
    "started_at",
    "ended_at",
    "last_error",
    "gpu",
    "workstation_online",
)
TRAINING_STATES = ["idle", "starting", "running", "completed", "failed", "stopped"]


class TrainingSensor(TrainerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = TRAINING_STATES

    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "sensor", "training")

    @property
    def native_value(self) -> str | None:
        state = self.status.get("state")
        return state if state in TRAINING_STATES else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {key: self.status.get(key) for key in STATUS_ATTRIBUTES}


class TrainingProgressSensor(TrainerEntity, SensorEntity):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "sensor", "training_progress")

    @property
    def native_value(self) -> float | None:
        return self.status.get("progress_percent")


class ModelSensor(TrainerEntity, SensorEntity):
    """The last model taken over from the trainer, for micro_wake_word."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, collector: Collector) -> None:
        super().__init__(collector, "sensor", "model")

    @property
    def available(self) -> bool:
        return True  # the model stays usable while the training computer is off

    @property
    def native_value(self) -> datetime | None:
        ended = self.coordinator.model.get("ended_at")
        return dt_util.parse_datetime(ended) if ended else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            key: self.coordinator.model.get(key)
            for key in ("url", "recall", "false_accepts_per_hour", "probability_cutoff", "message")
        }
