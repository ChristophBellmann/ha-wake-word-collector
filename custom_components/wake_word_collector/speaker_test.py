"""Loudspeaker test: the trainer service plays held-out recordings of the wake
word through a loudspeaker next to a satellite, and this counts how many the
satellite recognizes.

A recognition is seen, whichever comes first:
- the satellite's "Wake word detections" sensor (from the ESPHome package)
  goes up, or its assist_satellite entity leaves "idle"; both are found
  through the ESPHome node name the firmware reports, or chosen explicitly;
- without these, the satellite's activation report
  (`wake_word_report_triggers`). Reports during the test are not stored:
  they are played-back evaluation clips.

Optionally a "test mode" switch of the satellite is turned on for the test
(e.g. one that counts detections without starting the voice assistant, so
no conversation slows the test down) and always off again afterwards.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_ENTITY_ID, STATE_IDLE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from .collector import Collector

_LOGGER = logging.getLogger(__name__)

# Seconds to wait for a recognition after a clip: the state change comes at
# once, a trigger report only when the satellite uploads it (up to ~25 s).
WINDOW_WITH_SATELLITE = 6.0
WINDOW_REPORT_ONLY = 30.0
# Before each clip the satellite must be idle again (a recognition starts a
# real conversation, during which the wake word engine is stopped).
IDLE_TIMEOUT = 60.0
# Trigger reports that arrive this long after the test are still swallowed.
GRACE = 90.0
MAX_CLIPS = 20


DETECTIONS_KEY = "wake_word_detections"


def find_entities(hass: HomeAssistant, node: str | None) -> dict[str, str]:
    """assist_satellite and detection counter of the ESPHome device with this node name."""
    found: dict[str, str] = {}
    if not node:
        return found
    registry = er.async_get(hass)
    for entry in hass.config_entries.async_entries("esphome"):
        if str(entry.data.get("device_name", "")).lower() != node:
            continue
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.disabled:
                continue
            if entity.domain == "assist_satellite":
                found.setdefault("satellite", entity.entity_id)
            elif entity.domain == "sensor" and entity.unique_id.endswith(DETECTIONS_KEY):
                found.setdefault("detections", entity.entity_id)
    return found


def _number(state) -> float | None:
    try:
        return float(state.state)
    except (AttributeError, TypeError, ValueError):
        return None


class SpeakerTest:
    def __init__(self, hass: HomeAssistant, collector: Collector) -> None:
        self.hass = hass
        self.collector = collector
        self.result: dict[str, Any] = {"state": "idle"}
        self._device: str | None = None
        self._count_reports = False
        self._detected = asyncio.Event()
        self._swallow_until: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    # -- choices kept in the collector settings --------------------------------

    @property
    def choices(self) -> dict[str, Any]:
        return self.collector.settings.get("speaker_test", {})

    @property
    def device(self) -> str | None:
        device = self.choices.get("device")
        devices = self.collector.devices
        return device if device in devices else (devices[0] if devices else None)

    @property
    def clips(self) -> int:
        return int(self.choices.get("clips", 5))

    def route(self, device: str | None) -> str | None:
        routes = self.routes
        route = self.choices.get("routes", {}).get(device or "")
        return route if route in routes else (routes[0] if routes else None)

    @property
    def routes(self) -> list[str]:
        trainer = self.collector.trainer
        return list((trainer.data or {}).get("speaker_routes") or []) if trainer else []

    def _chosen(self, kind: str, device: str | None) -> str | None:
        return self.choices.get(kind, {}).get(device or "") or None

    def satellite(self, device: str | None) -> str | None:
        found = find_entities(self.hass, self.collector.node(device or ""))
        return self._chosen("satellites", device) or found.get("satellite")

    def detections(self, device: str | None) -> str | None:
        return find_entities(self.hass, self.collector.node(device or "")).get("detections")

    def test_switch(self, device: str | None) -> str | None:
        return self._chosen("test_switches", device)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- recognitions -----------------------------------------------------------

    @callback
    def consume(self, device: str) -> bool:
        """A trigger report arrived: True if it belongs to the test (not stored)."""
        if device == self._device and self.running:
            # With a satellite entity its state counts; its report of the same
            # activation comes late and must not count for the next clip.
            if self._count_reports:
                self._detected.set()
            return True
        return time.monotonic() < self._swallow_until.get(device, 0.0)

    # -- running ----------------------------------------------------------------

    @callback
    def async_start(
        self,
        device: str | None = None,
        route: str | None = None,
        clips: int | None = None,
        satellite: str | None = None,
        test_switch: str | None = None,
    ) -> asyncio.Task:
        """Check the choices and start the test in the background."""
        if self.collector.trainer is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_trainer")
        if self.running:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="speaker_test_running")
        device = (device or self.device or "").lower()
        if not device:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="speaker_test_no_device")
        route = route or self.route(device)
        if not route:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="speaker_test_no_route")
        clips = max(1, min(MAX_CLIPS, int(clips or self.clips)))
        # Explicit choices are remembered per satellite, like the route.
        remember: dict[str, Any] = {"routes": {**self.choices.get("routes", {}), device: route}}
        if satellite:
            remember["satellites"] = {**self.choices.get("satellites", {}), device: satellite}
        if test_switch is not None:  # "" forgets it
            remember["test_switches"] = {**self.choices.get("test_switches", {}), device: test_switch}
        self.collector.update_settings(speaker_test={"device": device, "clips": clips, **remember})
        watch = {
            "satellite": self.satellite(device),
            "detections": self.detections(device),
            "test_switch": self.test_switch(device),
        }
        self._task = self.hass.async_create_background_task(
            self._run(device, route, clips, watch), f"{DOMAIN} speaker test {device}"
        )
        return self._task

    async def async_run(self, **choices: Any) -> dict[str, Any]:
        """Run a test and wait for its result."""
        return await asyncio.shield(self.async_start(**choices))

    async def _run(self, device: str, route: str, clips: int, watch: dict[str, str | None]) -> dict[str, Any]:
        client = self.collector.trainer.client
        self.result = {
            "state": "running",
            "device": device,
            "route": route,
            "satellite": watch["satellite"],
            "detections": watch["detections"],
            "test_switch": watch["test_switch"],
            "clips": clips,
            "played": 0,
            "detected": 0,
            "message": "",
            "started_at": dt_util.utcnow().isoformat(),
            "finished_at": None,
        }
        self._device = device
        satellite, detections, test_switch = watch["satellite"], watch["detections"], watch["test_switch"]
        self._count_reports = not (satellite or detections)
        self.collector.notify()
        unsubscribe = None
        switched = False
        try:
            if satellite:
                state = self.hass.states.get(satellite)
                if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    return self._finish("failed", f"{satellite} is not available")

            @callback
            def changed(event: Event[EventStateChangedData]) -> None:
                new, old = event.data["new_state"], event.data["old_state"]
                if new is None:
                    return
                if event.data["entity_id"] == detections:
                    before, after = _number(old), _number(new)
                    if after is not None and (before is None or after > before):
                        self._detected.set()
                elif new.state not in (STATE_IDLE, STATE_UNAVAILABLE, STATE_UNKNOWN):
                    self._detected.set()

            watched = [entity for entity in (satellite, detections) if entity]
            if watched:
                unsubscribe = async_track_state_change_event(self.hass, watched, changed)
            if test_switch:
                await self._switch(test_switch, True)
                switched = True
            window = WINDOW_REPORT_ONLY if self._count_reports else WINDOW_WITH_SATELLITE
            for _ in range(clips):
                if satellite and not await self._wait_idle(satellite):
                    return self._finish("failed", f"{satellite} did not return to idle")
                self._detected.clear()
                await client.speaker_test(route)
                self.result["played"] += 1
                try:
                    await asyncio.wait_for(self._detected.wait(), window)
                    self.result["detected"] += 1
                except TimeoutError:
                    pass
                self.collector.notify()
            return self._finish("done", "")
        except HomeAssistantError as err:
            return self._finish("failed", str(err))
        finally:
            if unsubscribe:
                unsubscribe()
            if switched:
                try:
                    await self._switch(test_switch, False)
                except HomeAssistantError:
                    _LOGGER.warning("Could not turn %s off again", test_switch)
            self._swallow_until[device] = time.monotonic() + GRACE
            self._device = None

    async def _switch(self, entity_id: str, on: bool) -> None:
        domain = entity_id.split(".", 1)[0]
        await self.hass.services.async_call(
            domain, "turn_on" if on else "turn_off", {ATTR_ENTITY_ID: entity_id}, blocking=True
        )

    async def _wait_idle(self, satellite: str) -> bool:
        deadline = time.monotonic() + IDLE_TIMEOUT
        while time.monotonic() < deadline:
            state = self.hass.states.get(satellite)
            if state is not None and state.state == STATE_IDLE:
                return True
            await asyncio.sleep(0.5)
        return False

    def _finish(self, state: str, message: str) -> dict[str, Any]:
        played, detected = self.result["played"], self.result["detected"]
        self.result.update(
            state=state,
            message=message or f"{detected} of {played} recognized",
            finished_at=dt_util.utcnow().isoformat(),
            recall=round(detected / played, 3) if played else None,
        )
        _LOGGER.info("Speaker test %s via %s: %s", self.result["device"], self.result["route"], self.result["message"])
        self.collector.notify()
        return dict(self.result)
