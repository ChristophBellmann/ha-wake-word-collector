"""Roll out a taken-over model to the satellites from Home Assistant.

The same steps as `model_update.py` (model files, model substitution and
sensitivity steps in each device configuration, parity guard), then every
device not yet verified for this model is compiled and installed through the
ESPHome Device Builder (dashboard), one after the other. A device counts as
done only when it is back in Home Assistant with a new compilation time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from . import model_update
from .const import DOMAIN

if TYPE_CHECKING:
    from .collector import Collector

_LOGGER = logging.getLogger(__name__)
ROLLOUT_EVENT = f"{DOMAIN}_rollout_finished"
STATES = ["idle", "running", "completed", "failed", "blocked"]
VERIFY_TIMEOUT = timedelta(minutes=5)
VERIFY_INTERVAL = 5
# Upper bounds for one Device Builder job. The builder does not answer
# websocket pings while a job runs, and an OTA to a device on weak Wi-Fi stays
# silent for minutes, so there is no heartbeat; the job just has to end in time.
COMPILE_TIMEOUT = timedelta(minutes=60)
UPLOAD_TIMEOUT = timedelta(minutes=15)
LOG_LINES = 15
BUILD_TIME = re.compile(r"build_time_str=(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4})")


class DashboardError(Exception):
    """The ESPHome Device Builder could not be reached."""


class DashboardClient:
    """The ESPHome Device Builder's API as Home Assistant's ESPHome integration uses it."""

    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self.session = session
        self.url = url.rstrip("/")

    async def devices(self) -> dict[str, dict[str, Any]]:
        """Configured devices by configuration file name."""
        try:
            async with self.session.get(f"{self.url}/devices", timeout=aiohttp.ClientTimeout(total=20)) as response:
                response.raise_for_status()
                data = await response.json()
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise DashboardError(str(err) or type(err).__name__) from err
        return {device["configuration"]: device for device in data.get("configured", [])}

    async def _spawn(self, path: str, params: dict[str, str], lines: deque[str], timeout: timedelta) -> bool:
        try:
            async with (
                asyncio.timeout(timeout.total_seconds()),
                self.session.ws_connect(f"{self.url}/{path}", timeout=aiohttp.ClientWSTimeout(ws_close=10)) as client,
            ):
                await client.send_json({"type": "spawn", **params})
                async for message in client:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        return False
                    data = message.json()
                    if data.get("event") == "exit":
                        return data.get("code") == 0
                    if data.get("event") == "line":
                        lines.extend(line for line in str(data.get("data", "")).splitlines() if line.strip())
        except TimeoutError:
            lines.append(f"{path} did not finish within {timeout}")
            return False
        except (aiohttp.ClientError, ValueError) as err:
            raise DashboardError(str(err) or type(err).__name__) from err
        return False

    async def compile(self, configuration: str, lines: deque[str]) -> bool:
        return await self._spawn("compile", {"configuration": configuration}, lines, COMPILE_TIMEOUT)

    async def upload(self, configuration: str, port: str, lines: deque[str]) -> bool:
        return await self._spawn("upload", {"configuration": configuration, "port": port}, lines, UPLOAD_TIMEOUT)


def _is_node(info: Any, node: str) -> bool:
    """The device's name is the node name, or that plus a MAC suffix (``name_add_mac_suffix``)."""
    name = getattr(info, "name", None)
    if not node or not name:
        return False
    if name == node:
        return True
    mac = str(getattr(info, "mac_address", "") or "").replace(":", "").lower()
    return len(mac) == 12 and name == f"{node}-{mac[-6:]}"


def dashboard_url(hass: HomeAssistant, configured: str) -> str | None:
    """The configured Device Builder, else the one Home Assistant's ESPHome integration knows."""
    if configured:
        return configured.rstrip("/")
    try:
        from homeassistant.components.esphome.dashboard import async_get_dashboard

        coordinator = async_get_dashboard(hass)
    except Exception:  # noqa: BLE001 - ESPHome not set up or changed internally
        return None
    return getattr(coordinator, "url", None)


def config_path(hass: HomeAssistant, option: str) -> Path:
    path = Path(option).expanduser()
    return path if path.is_absolute() else Path(hass.config.path(option))


class Rollout:
    def __init__(self, hass: HomeAssistant, collector: Collector, config: str, url: str) -> None:
        self.hass = hass
        self.collector = collector
        self.config = config_path(hass, config)
        self.url_option = url
        self._task: asyncio.Task | None = None
        self.result: dict[str, Any] = {"state": "idle"}

    def load(self) -> None:
        """The last result survives a restart; an interrupted run can be resumed."""
        result = dict(self.collector.settings.get("rollout") or {"state": "idle"})
        if result.get("state") == "running":
            result.update(state="failed", message="interrupted by a restart; roll out again to continue")
        self.result = result

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def async_plan(self) -> model_update.Plan:
        return await self.hass.async_add_executor_job(
            lambda: model_update.prepare(self.config, source=self.collector.model_dir)
        )

    async def async_start(self, dry_run: bool = False) -> dict[str, Any]:
        """Check and plan now; install in the background. Returns the plan."""
        if self.running:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="rollout_running")
        try:
            plan = await self.async_plan()
        except (model_update.UpdateError, ValueError) as err:
            if not dry_run:
                self._finish("blocked", str(err), devices={})
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="rollout_blocked",
                translation_placeholders={"error": str(err)},
            ) from err
        summary = plan.summary()
        if dry_run:
            return {**summary, "dry_run": True}
        url = dashboard_url(self.hass, self.url_option)
        if not url:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="rollout_no_dashboard")
        self._set(
            state="running",
            model=summary["model"],
            message="",
            current=None,
            devices={device.file: "pending" for device in plan.devices},
            started_at=dt_util.utcnow().isoformat(),
            ended_at=None,
        )
        self._task = self.hass.async_create_background_task(
            self._run(plan, DashboardClient(async_get_clientsession(self.hass), url)),
            f"{DOMAIN} rollout {self.collector.slug}",
        )
        return {**summary, "state": "running"}

    async def async_stop(self) -> None:
        if self.running:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    # -- the run ------------------------------------------------------------------

    async def _run(self, plan: model_update.Plan, client: DashboardClient) -> None:
        devices = dict(self.result["devices"])
        try:
            await self.hass.async_add_executor_job(model_update.write, plan)
            ledger = await self.hass.async_add_executor_job(model_update.load_ledger, plan)
            try:
                known = await client.devices()
            except DashboardError as err:
                self._finish("failed", f"ESPHome Device Builder not reachable: {err}", devices)
                return
            for device in plan.devices:
                devices[device.file] = await self._install(plan, device, client, known, ledger)
                self._set(devices=dict(devices), current=None)
        except asyncio.CancelledError:
            self._finish("failed", "stopped", devices)
            raise
        except Exception as err:  # keep the integration alive, show the reason
            _LOGGER.exception("Model rollout failed")
            self._finish("failed", f"{type(err).__name__}: {err}", devices)
            return
        failed = [file for file, state in devices.items() if state != "verified"]
        if failed:
            self._finish("failed", "not installed: " + ", ".join(failed) + "; roll out again to retry", devices)
        else:
            self._finish("completed", f"{plan.target_manifest.name} runs on {len(devices)} satellite(s)", devices)

    async def _install(
        self,
        plan: model_update.Plan,
        device: model_update.DevicePlan,
        client: DashboardClient,
        known: dict[str, dict[str, Any]],
        ledger: dict[str, Any],
    ) -> str:
        configuration = Path(os.path.relpath(device.path, plan.root)).as_posix()
        port = str(device.device.get("address") or "OTA")
        method = ["esphome-device-builder", configuration, port]
        device_fingerprint = await self.hass.async_add_executor_job(model_update.fingerprint, plan, device, method)
        if model_update.is_verified(ledger, device, device_fingerprint):
            return "verified"
        entry: dict[str, Any] = {
            "fingerprint": device_fingerprint,
            "verified": False,
            "started_at": dt_util.utcnow().isoformat(),
        }
        ledger["devices"][device.file] = entry
        node = known.get(configuration, {}).get("name", "")
        before = self.compilation_time(node) if node else None
        lines: deque[str] = deque(maxlen=LOG_LINES)
        state, built = await self._build(device.file, configuration, port, client, known, lines)
        if state == "installed":
            self._set(current=device.file, step="verifying")
            verified, detail = await self._verify(node, before, built)
            entry.update(verified=verified, detail=detail)
            state = "verified" if verified else "not verified"
        else:
            entry["log"] = list(lines)
        entry["ended_at"] = dt_util.utcnow().isoformat()
        await self.hass.async_add_executor_job(model_update.write_progress, plan.source / model_update.LEDGER, ledger)
        return state

    async def _build(
        self,
        file: str,
        configuration: str,
        port: str,
        client: DashboardClient,
        known: dict[str, dict[str, Any]],
        lines: deque[str],
    ) -> tuple[str, str | None]:
        """Compile and install; also the build time the compiler reported, if any."""
        if configuration not in known:
            return "not in the ESPHome Device Builder", None
        try:
            self._set(current=file, step="compiling")
            if not await client.compile(configuration, lines):
                return "compile failed", None
            found = BUILD_TIME.search("\n".join(lines))
            built = found.group(1) if found else None
            self._set(current=file, step="installing")
            if not await client.upload(configuration, port, lines):
                return "install failed", built
        except DashboardError as err:
            lines.append(str(err))
            return "ESPHome Device Builder not reachable", None
        return "installed", built

    def _device_data(self, node: str) -> Any:
        """Runtime data of the ESPHome config entry for this node, if Home Assistant has it."""
        for entry in self.hass.config_entries.async_entries("esphome"):
            data = getattr(entry, "runtime_data", None)
            info = getattr(data, "device_info", None)
            if info is not None and _is_node(info, node):
                return data
        return None

    def compilation_time(self, node: str) -> str | None:
        data = self._device_data(node)
        return getattr(getattr(data, "device_info", None), "compilation_time", None)

    async def _verify(self, node: str, before: str | None, built: str | None) -> tuple[bool, str]:
        """Back online in Home Assistant with the firmware just built.

        Without a build time from the compiler, any other firmware than before counts.
        """
        if self._device_data(node) is None:
            return True, "installed; not set up in Home Assistant, so not checked"
        deadline = dt_util.utcnow() + VERIFY_TIMEOUT
        while dt_util.utcnow() < deadline:
            data = self._device_data(node)
            now = self.compilation_time(node)
            installed = now == built if built else now != before
            if data is not None and getattr(data, "available", False) and now and installed:
                return True, f"runs firmware compiled {now}"
            await asyncio.sleep(VERIFY_INTERVAL)
        return False, "did not come back with the new firmware"

    # -- state --------------------------------------------------------------------

    def _set(self, **values: Any) -> None:
        self.result = {**self.result, **values}
        self.collector.settings["rollout"] = self.result
        self.collector.update_settings()

    def _finish(self, state: str, message: str, devices: dict[str, str]) -> None:
        self._set(
            state=state,
            message=message,
            devices=devices,
            current=None,
            step=None,
            ended_at=dt_util.utcnow().isoformat(),
        )
        self.hass.bus.async_fire(ROLLOUT_EVENT, {"slug": self.collector.slug, **self.result})
        persistent_notification.async_create(
            self.hass,
            f"{self.collector.entry.title}: {message}\n\n"
            + "\n".join(f"- {file}: {result}" for file, result in devices.items()),
            title="Wake Word Collector",
            notification_id=f"{DOMAIN}_{self.collector.slug}_rollout",
        )
