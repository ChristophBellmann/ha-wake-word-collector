"""Config flow: one entry per wake word."""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector as sel
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import slugify

from .collect import CollectorError, Phrases
from .const import (
    CONF_CONTROL,
    CONF_PHRASE,
    CONF_SLUG,
    CONF_STORAGE,
    CONF_TOKEN,
    CONF_VARIANTS,
    CONTROL_DEFAULTS,
    DOMAIN,
    UPLOAD_URL,
)

TEXT = sel.TextSelector()


def _schema(values: dict[str, Any], with_phrase: bool) -> vol.Schema:
    schema: dict = {}
    if with_phrase:
        schema[vol.Required(CONF_PHRASE, default=values.get(CONF_PHRASE, vol.UNDEFINED))] = TEXT
    schema[vol.Optional(CONF_VARIANTS, description={"suggested_value": values.get(CONF_VARIANTS, "")})] = TEXT
    schema[vol.Optional(CONF_CONTROL, description={"suggested_value": values.get(CONF_CONTROL, "")})] = TEXT
    schema[vol.Optional(CONF_STORAGE, description={"suggested_value": values.get(CONF_STORAGE, "")})] = TEXT
    return vol.Schema(schema)


def _valid(values: dict[str, Any]) -> dict[str, str]:
    try:
        Phrases.build(values[CONF_PHRASE], values.get(CONF_VARIANTS, ""), values.get(CONF_CONTROL, ""))
    except CollectorError:
        return {CONF_PHRASE: "phrase_empty"}
    return {}


def upload_url(hass, slug: str) -> str:
    try:
        base = get_url(hass, prefer_external=False, allow_cloud=False)
    except NoURLAvailableError:
        base = "http://homeassistant.local:8123"
    return base + UPLOAD_URL.format(slug=slug)


class WakeWordCollectorConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WakeWordCollectorOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        language = (self.hass.config.language or "en").split("-")[0]
        if user_input is not None:
            errors = _valid(user_input)
            slug = slugify(user_input[CONF_PHRASE]) or "wake_word"
            if not errors:
                await self.async_set_unique_id(slug)
                self._abort_if_unique_id_configured()
                self._options = {
                    CONF_PHRASE: user_input[CONF_PHRASE].strip(),
                    CONF_VARIANTS: user_input.get(CONF_VARIANTS, ""),
                    CONF_CONTROL: user_input.get(CONF_CONTROL)
                    or CONTROL_DEFAULTS.get(language, CONTROL_DEFAULTS["en"]),
                    CONF_STORAGE: user_input.get(CONF_STORAGE) or self.hass.config.path(DOMAIN, slug),
                }
                self._data = {CONF_SLUG: slug, CONF_TOKEN: secrets.token_urlsafe(32)}
                return await self.async_step_device_settings()
        defaults = {CONF_CONTROL: CONTROL_DEFAULTS.get(language, CONTROL_DEFAULTS["en"]), **(user_input or {})}
        return self.async_show_form(step_id="user", data_schema=_schema(defaults, True), errors=errors)

    async def async_step_device_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show what goes into the ESPHome configuration of the satellites."""
        if user_input is not None:
            return self.async_create_entry(title=self._options[CONF_PHRASE], data=self._data, options=self._options)
        return self.async_show_form(
            step_id="device_settings",
            data_schema=vol.Schema({}),
            description_placeholders={
                "url": upload_url(self.hass, self._data[CONF_SLUG]),
                "token": self._data[CONF_TOKEN],
                "slug": self._data[CONF_SLUG],
            },
        )


class WakeWordCollectorOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            options = {**entry.options, **user_input, CONF_PHRASE: entry.options[CONF_PHRASE]}
            options[CONF_STORAGE] = user_input.get(CONF_STORAGE) or entry.options[CONF_STORAGE]
            errors = _valid(options)
            if not errors:
                return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=_schema({**entry.options, **(user_input or {})}, False),
            errors=errors,
            description_placeholders={
                "url": upload_url(self.hass, entry.data[CONF_SLUG]),
                "token": entry.data[CONF_TOKEN],
                "slug": entry.data[CONF_SLUG],
            },
        )
