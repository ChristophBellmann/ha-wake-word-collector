"""HTTP endpoints.

* Upload (satellites): token in ``X-Wakeword-Token``, device in
  ``X-Wakeword-Device``, speech-to-text result in ``X-Wakeword-Transcript``,
  body a mono PCM16 WAV. No Home Assistant login: ESPHome devices have none.
* Audio (position card, logged-in users or signed paths).
* Export (training pipelines): the upload token; accepted clips, and clips
  that are not the wake word (negatives).
* Model (satellites, ESPHome at compile time): the trained model, no login;
  it contains no audio.
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.exceptions import HomeAssistantError

from .collect import CANDIDATES, CONTROL, MAX_BODY_BYTES, NEGATIVES, CollectorError
from .const import AUDIO_URL, DOMAIN, EXPORT_URL, MODEL_URL, NEGATIVE_AUDIO_URL, NEGATIVES_URL, UPLOAD_URL

if TYPE_CHECKING:
    from .collector import Collector

_LOGGER = logging.getLogger(__name__)


def _collectors(request: web.Request) -> list[Collector]:
    return list(request.app["hass"].data.get(DOMAIN, {}).values())


def _by_slug(request: web.Request, slug: str) -> Collector | None:
    return next((c for c in _collectors(request) if c.slug == slug), None)


def _authorized(request: web.Request, collector: Collector) -> bool:
    supplied = request.headers.get("X-Wakeword-Token", "")
    return bool(supplied) and hmac.compare_digest(supplied, collector.token)


def _header_text(value: str) -> str:
    """HTTP headers arrive as latin-1; the device sends UTF-8."""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


class UploadView(HomeAssistantView):
    url = UPLOAD_URL
    name = "api:wake_word_collector:upload"
    requires_auth = False

    async def post(self, request: web.Request, slug: str) -> web.Response:
        collector = _by_slug(request, slug)
        if collector is None:
            return self.json_message("unknown wake word", 404)
        if not _authorized(request, collector):
            return self.json_message("authentication failed", 401)
        if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
            return self.json_message("too large", 413)
        # StreamReader.read(n) may return the first available fragment only.
        # A satellite sends the WAV over many packets; read through EOF while
        # keeping chunked requests bounded as well as Content-Length requests.
        body = bytearray()
        while chunk := await request.content.read(65536):
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                return self.json_message("too large", 413)
        body = bytes(body)
        device = request.headers.get("X-Wakeword-Device", "")
        transcript = _header_text(request.headers.get("X-Wakeword-Transcript", "")).strip()
        kind = request.headers.get("X-Wakeword-Kind", "utterance").strip().lower() or "utterance"
        try:
            node = request.headers.get("X-Wakeword-Node", "").strip()
            records = await collector.async_add(device, transcript, body, kind, node)
        except HomeAssistantError as err:
            cause = err.__cause__
            code = cause.code if isinstance(cause, CollectorError) else "error"
            _LOGGER.debug("Clip from %s refused: %s", device, code)
            return self.json({"error": code}, 400)
        return self.json({"records": records}, 201)


class AudioView(HomeAssistantView):
    url = AUDIO_URL
    name = "api:wake_word_collector:audio"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, category: str, device: str, filename: str
    ) -> web.StreamResponse:
        collector = request.app["hass"].data.get(DOMAIN, {}).get(entry_id)
        if collector is None:
            raise web.HTTPNotFound
        try:
            path = collector.store.audio(category, device, filename)
        except CollectorError as err:
            raise web.HTTPNotFound from err
        return web.FileResponse(path, headers={"Content-Type": "audio/wav", "Cache-Control": "private, no-store"})


class ExportListView(HomeAssistantView):
    url = "/api/wake_word_collector/export/{slug}"
    name = "api:wake_word_collector:export_list"
    requires_auth = False

    async def get(self, request: web.Request, slug: str) -> web.Response:
        collector = _by_slug(request, slug)
        if collector is None or not _authorized(request, collector):
            return self.json_message("authentication failed", 401)
        items = [item for item in await collector.async_list() if item["category"] == CANDIDATES]
        for item in items:
            item["audio_url"] = EXPORT_URL.format(slug=slug, device=item["device"], filename=item["filename"])
        return self.json({"phrase": collector.entry.title, "candidates": items})


class ExportAudioView(HomeAssistantView):
    url = EXPORT_URL
    name = "api:wake_word_collector:export_audio"
    requires_auth = False

    async def get(self, request: web.Request, slug: str, device: str, filename: str) -> web.StreamResponse:
        collector = _by_slug(request, slug)
        if collector is None or not _authorized(request, collector):
            return self.json_message("authentication failed", 401)
        try:
            path = collector.store.audio(CANDIDATES, device, filename)
        except CollectorError as err:
            raise web.HTTPNotFound from err
        return web.FileResponse(path, headers={"Content-Type": "audio/wav"})


class ExportNegativesView(HomeAssistantView):
    url = NEGATIVES_URL
    name = "api:wake_word_collector:export_negatives"
    requires_auth = False

    async def get(self, request: web.Request, slug: str) -> web.Response:
        collector = _by_slug(request, slug)
        if collector is None or not _authorized(request, collector):
            return self.json_message("authentication failed", 401)
        items = await collector.async_negatives()
        for item in items:
            item["audio_url"] = NEGATIVE_AUDIO_URL.format(
                slug=slug, category=item["category"], device=item["device"], filename=item["filename"]
            )
        return self.json({"phrase": collector.entry.title, "negatives": items})


class ExportNegativeAudioView(HomeAssistantView):
    url = NEGATIVE_AUDIO_URL
    name = "api:wake_word_collector:export_negative_audio"
    requires_auth = False

    async def get(
        self, request: web.Request, slug: str, category: str, device: str, filename: str
    ) -> web.StreamResponse:
        collector = _by_slug(request, slug)
        if collector is None or not _authorized(request, collector):
            return self.json_message("authentication failed", 401)
        if category not in (NEGATIVES, CONTROL):
            raise web.HTTPNotFound
        try:
            path = collector.store.audio(category, device, filename)
        except CollectorError as err:
            raise web.HTTPNotFound from err
        return web.FileResponse(path, headers={"Content-Type": "audio/wav"})


class ModelView(HomeAssistantView):
    """The trained model for micro_wake_word: <slug>.json and <slug>.tflite."""

    url = MODEL_URL
    name = "api:wake_word_collector:model"
    requires_auth = False

    async def get(self, request: web.Request, slug: str, filename: str) -> web.StreamResponse:
        collector = _by_slug(request, slug)
        if collector is None or filename not in (f"{slug}.json", f"{slug}.tflite"):
            raise web.HTTPNotFound
        path = collector.model_dir / filename
        if not path.is_file():
            raise web.HTTPNotFound
        kind = "application/json" if filename.endswith(".json") else "application/octet-stream"
        return web.FileResponse(path, headers={"Content-Type": kind, "Cache-Control": "no-cache"})


VIEWS = (
    UploadView,
    AudioView,
    ExportListView,
    ExportAudioView,
    ExportNegativesView,
    ExportNegativeAudioView,
    ModelView,
)
