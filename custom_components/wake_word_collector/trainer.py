"""Connection to a Wake Word Trainer service (`wake-word-trainer serve`) on the
training computer: status, start, stop, loudspeaker test, and taking over the
finished model so the satellites can load it from Home Assistant."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, MODEL_URL

if TYPE_CHECKING:
    from .collector import Collector

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=15)
TIMEOUT = aiohttp.ClientTimeout(total=20)
MODEL_EVENT = f"{DOMAIN}_model_ready"


class TrainerError(HomeAssistantError):
    """The trainer could not be reached or refused the request."""


class TrainerClient:
    def __init__(self, hass: HomeAssistant, url: str, token: str) -> None:
        self.session = async_get_clientsession(hass)
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, body: dict | None = None, timeout=TIMEOUT) -> tuple[int, bytes]:
        try:
            async with self.session.request(
                method, self.url + path, json=body, headers=self.headers, timeout=timeout
            ) as response:
                return response.status, await response.read()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise TrainerError(translation_domain=DOMAIN, translation_key="trainer_unreachable") from err

    async def _json(self, method: str, path: str, body: dict | None = None, timeout=TIMEOUT) -> dict[str, Any]:
        status, data = await self._request(method, path, body, timeout)
        if status == 401:
            raise TrainerError(translation_domain=DOMAIN, translation_key="trainer_auth")
        try:
            payload = json.loads(data or b"{}")
        except json.JSONDecodeError as err:
            raise TrainerError(translation_domain=DOMAIN, translation_key="trainer_unreachable") from err
        if status >= 400:
            raise TrainerError(
                translation_domain=DOMAIN,
                translation_key="trainer_refused",
                translation_placeholders={"error": str(payload.get("error", status))},
            )
        return payload

    async def status(self) -> dict[str, Any]:
        return await self._json("GET", "/v1/status")

    async def start(self, profile: str) -> dict[str, Any]:
        return await self._json("POST", "/v1/start", {"profile": profile})

    async def stop(self) -> dict[str, Any]:
        return await self._json("POST", "/v1/stop", {}, aiohttp.ClientTimeout(total=70))

    async def speaker_test(self, route: str) -> dict[str, Any]:
        return await self._json("POST", "/v1/speaker_test", {"route": route}, aiohttp.ClientTimeout(total=45))

    async def report(self) -> dict[str, Any]:
        return await self._json("GET", "/v1/report")

    async def model_file(self, name: str) -> bytes:
        status, data = await self._request("GET", f"/v1/model/{name}", timeout=aiohttp.ClientTimeout(total=60))
        if status != 200:
            raise TrainerError(translation_domain=DOMAIN, translation_key="trainer_no_model")
        return data


class TrainerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, collector: Collector, client: TrainerClient) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN} trainer {collector.slug}", update_interval=SCAN_INTERVAL, always_update=False
        )
        self.collector = collector
        self.client = client
        self.profile: str | None = None
        self.model: dict[str, Any] = {}

    async def async_load_model_info(self) -> None:
        self.model = await self.hass.async_add_executor_job(self._read_model_info)

    def _read_model_info(self) -> dict[str, Any]:
        path = self.collector.model_dir / "source.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            status = await self.client.status()
        except TrainerError as err:
            raise UpdateFailed(str(err)) from err
        profiles = list(status.get("profiles") or {})
        if self.profile not in profiles:
            self.profile = "recommended" if "recommended" in profiles else (profiles[0] if profiles else None)
        if (
            status.get("state") == "completed"
            and status.get("best_model_available")
            and status.get("ended_at")
            and status.get("ended_at") != self.model.get("ended_at")
        ):
            await self._take_over_model(status)
        return status

    async def _take_over_model(self, status: dict[str, Any]) -> None:
        slug = self.collector.slug
        try:
            manifest = await self.client.model_file(f"{slug}.json")
            model = await self.client.model_file(f"{slug}.tflite")
            data = json.loads(manifest)
        except (TrainerError, json.JSONDecodeError) as err:
            _LOGGER.warning("Could not take over the trained model: %s", err)
            return
        try:
            # Recall and false activations over all cutoffs, e.g. for a sensitivity ladder.
            report = await self.client.report()
        except TrainerError:
            report = {}
        if data.get("model") != f"{slug}.tflite":
            _LOGGER.warning("Trained model manifest names %s, expected %s.tflite", data.get("model"), slug)
            return
        info = {
            "ended_at": status.get("ended_at"),
            "recall": status.get("best_recall"),
            "false_accepts_per_hour": status.get("best_faph"),
            "probability_cutoff": data.get("micro", {}).get("probability_cutoff"),
            "message": status.get("message", ""),
            "url": MODEL_URL.format(slug=slug, filename=f"{slug}.json"),
        }
        await self.hass.async_add_executor_job(self._write_model, manifest, model, {**info, "report": report})
        self.model = info
        self.hass.bus.async_fire(MODEL_EVENT, {"slug": slug, **info})
        persistent_notification.async_create(
            self.hass,
            f"{self.collector.entry.title}: {info['message']}\n\nmicro_wake_word model: `{info['url']}`",
            title="Wake Word Collector",
            notification_id=f"{DOMAIN}_{slug}_model",
        )

    def _write_model(self, manifest: bytes, model: bytes, info: dict[str, Any]) -> None:
        folder = self.collector.model_dir
        folder.mkdir(parents=True, exist_ok=True)
        slug = self.collector.slug
        # The .tflite first: a satellite reading the manifest must find the matching model.
        (folder / f"{slug}.tflite.part").write_bytes(model)
        (folder / f"{slug}.tflite.part").replace(folder / f"{slug}.tflite")
        (folder / f"{slug}.json.part").write_bytes(manifest)
        (folder / f"{slug}.json.part").replace(folder / f"{slug}.json")
        (folder / "source.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
