"""HTTP endpoints.

* Upload (satellites): token in ``X-Wakeword-Token``, device in
  ``X-Wakeword-Device``, speech-to-text result in ``X-Wakeword-Transcript``,
  body a mono PCM16 WAV. No Home Assistant login: ESPHome devices have none.
* Audio (position card, logged-in users or signed paths).
* Export (training pipelines): the upload token, candidates only.
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.exceptions import HomeAssistantError

from .collect import CANDIDATES, MAX_BODY_BYTES, CollectorError
from .const import AUDIO_URL, DOMAIN, EXPORT_URL, UPLOAD_URL

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
        body = await request.content.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            return self.json_message("too large", 413)
        device = request.headers.get("X-Wakeword-Device", "")
        transcript = _header_text(request.headers.get("X-Wakeword-Transcript", "")).strip()
        try:
            records = await collector.async_add(device, transcript, body)
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


VIEWS = (UploadView, AudioView, ExportListView, ExportAudioView)
