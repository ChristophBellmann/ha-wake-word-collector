"""Intents, so voice assistants (also LLM agents) can control the collection."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent

from .const import DOMAIN

START = "WakeWordCollectionStart"
STOP = "WakeWordCollectionStop"
STATS = "WakeWordCollectionStats"
REVIEW = "WakeWordCollectionReview"

SPEECH = {
    "en": {
        START: "Recording started. Now say only the wake word, as often as you like. Say that you are done to stop.",
        STOP: "Wake word recording stopped.",
        STATS: "There are {candidates} usable recordings, {needs_review} to check and {total} in total.",
        REVIEW: "Noted.",
        "rejected": "The last recording will not be used.",
        "none": "The wake word collector is not set up.",
    },
    "de": {
        START: "Aufnahme läuft. Sag jetzt nur das Aktivierungswort, so oft du möchtest. "
        "Sag, dass du fertig bist, um aufzuhören.",
        STOP: "Die Wakeword-Aufnahme ist beendet.",
        STATS: "Es gibt {candidates} verwendbare Aufnahmen, {needs_review} zum Prüfen und insgesamt {total}.",
        REVIEW: "Notiert.",
        "rejected": "Die letzte Aufnahme wird nicht verwendet.",
        "none": "Der Wake Word Collector ist nicht eingerichtet.",
    },
}


def _speech(hass: HomeAssistant, intent_obj: intent.Intent, key: str, **values) -> str:
    language = (intent_obj.language or hass.config.language or "en").split("-")[0]
    return SPEECH.get(language, SPEECH["en"])[key].format(**values)


def _collector(hass: HomeAssistant, intent_obj: intent.Intent):
    collectors = list(hass.data.get(DOMAIN, {}).values())
    if not collectors:
        raise intent.IntentHandleError(_speech(hass, intent_obj, "none"))
    return collectors[0]


class _Handler(intent.IntentHandler):
    def _respond(self, intent_obj: intent.Intent, text: str) -> intent.IntentResponse:
        response = intent_obj.create_response()
        response.async_set_speech(text)
        return response


class StartHandler(_Handler):
    intent_type = START
    description = (
        "Starts recording examples of the wake word on the satellite the user is talking to, "
        "e.g. to improve or train the wake word. No parameters."
    )

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        _collector(intent_obj.hass, intent_obj).send("start")
        return self._respond(intent_obj, _speech(intent_obj.hass, intent_obj, START))


class StopHandler(_Handler):
    intent_type = STOP
    description = "Stops recording wake word examples when the user says they are done. No parameters."

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        _collector(intent_obj.hass, intent_obj).send("stop")
        return self._respond(intent_obj, _speech(intent_obj.hass, intent_obj, STOP))


class StatsHandler(_Handler):
    intent_type = STATS
    description = "Tells how many wake word recordings were collected. No parameters."

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        collector = _collector(intent_obj.hass, intent_obj)
        await collector.async_refresh()
        stats = collector.stats
        return self._respond(
            intent_obj,
            _speech(
                intent_obj.hass,
                intent_obj,
                STATS,
                candidates=stats.get("candidates", 0),
                needs_review=stats.get("needs_review", 0),
                total=stats.get("total", 0),
            ),
        )


class ReviewHandler(_Handler):
    intent_type = REVIEW
    description = (
        "Comments on the last wake word recording: pass the user's full words as note, e.g. background "
        "noise. Set reject only if the user explicitly says the recording is bad and must not be used."
    )

    @property
    def slot_schema(self) -> dict:
        return {vol.Required("note"): cv.string, vol.Optional("reject"): cv.boolean}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        reject = bool(slots.get("reject", {}).get("value"))
        collector = _collector(intent_obj.hass, intent_obj)
        try:
            await collector.async_review_latest(slots["note"]["value"], "reject" if reject else "note")
        except HomeAssistantError as err:
            raise intent.IntentHandleError(str(err)) from err
        return self._respond(intent_obj, _speech(intent_obj.hass, intent_obj, "rejected" if reject else REVIEW))


HANDLERS = (StartHandler, StopHandler, StatsHandler, ReviewHandler)


def async_register(hass: HomeAssistant) -> None:
    for handler in HANDLERS:
        intent.async_register(hass, handler())
