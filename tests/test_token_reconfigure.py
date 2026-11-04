"""Only the configured recorder credential changes; old auth fails closed."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.wake_word_collector.const import CONF_SLUG, CONF_TOKEN, DOMAIN

from .test_collect import speech, wav_bytes
from .test_integration import TOKEN, entry, upload  # noqa: F401

NEW_TOKEN = "new_" + "n" * 40


async def begin(hass, config_entry):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": config_entry.entry_id}
    )


async def test_reconfigure_form_never_shows_current_token(hass, entry):  # noqa: F811
    result = await begin(hass, entry)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    assert TOKEN not in json.dumps(result, default=str)
    assert not result.get("description_placeholders")
    assert entry.data[CONF_TOKEN] == TOKEN


@pytest.mark.parametrize("value", ["", "x" * 31, "x" * 129, "a b" * 20, "!" * 43, TOKEN])
async def test_reconfigure_rejects_bad_or_unchanged_token(hass, entry, value):  # noqa: F811
    before = dict(entry.data)
    result = await begin(hass, entry)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: value})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TOKEN: "token_unchanged" if value == TOKEN else "token_invalid"}
    assert entry.data == before
    assert TOKEN not in json.dumps(result, default=str)


async def test_reconfigure_changes_only_token_and_rejects_old_upload_export(hass, entry, hass_client_no_auth):  # noqa: F811
    original_data, original_options = dict(entry.data), dict(entry.options)
    original_identity = entry.entry_id, entry.unique_id, entry.title
    entries_before = len(hass.config_entries.async_entries(DOMAIN))
    collector = entry.runtime_data
    body = wav_bytes(speech(1))
    client = await hass_client_no_auth()
    assert (await upload(client, "Hey Nova", body)).status == 201
    export = "/api/wake_word_collector/export/hey_nova"
    assert (await client.get(export, headers={"X-Wakeword-Token": TOKEN})).status == 200
    before_files = sorted(
        (str(p.relative_to(collector.store.root)), p.read_bytes()) for p in collector.store.root.rglob("*.wav")
    )

    result = await begin(hass, entry)
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as reload_entry:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: NEW_TOKEN})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert TOKEN not in json.dumps(result, default=str) and NEW_TOKEN not in json.dumps(result, default=str)
    assert entry.data == {**original_data, CONF_TOKEN: NEW_TOKEN}
    assert entry.options == original_options
    assert (entry.entry_id, entry.unique_id, entry.title) == original_identity
    assert len(hass.config_entries.async_entries(DOMAIN)) == entries_before
    assert entry.data[CONF_SLUG] == "hey_nova"
    assert reload_entry.await_count == 1
    assert all(call.args == (entry.entry_id,) for call in reload_entry.await_args_list)
    assert collector.token == NEW_TOKEN

    assert (await upload(client, "Hey Nova", body)).status == 401
    assert (await client.get(export, headers={"X-Wakeword-Token": TOKEN})).status == 401
    assert (await client.get(export, headers={"X-Wakeword-Token": NEW_TOKEN})).status == 200
    review = "/api/wake_word_collector/export/hey_nova/review/triggers/bath/unconfirmed.wav"
    assert (await client.post(review, json={}, headers={"X-Wakeword-Token": TOKEN})).status == 401
    assert (await client.post(review, json={}, headers={"X-Wakeword-Token": NEW_TOKEN})).status == 403
    assert (
        sorted((str(p.relative_to(collector.store.root)), p.read_bytes()) for p in collector.store.root.rglob("*.wav"))
        == before_files
    )
    assert (await upload(client, "Hey Nova", body, token=NEW_TOKEN)).status == 201


async def test_failed_reload_never_restores_old_credential(hass, entry, hass_client_no_auth):  # noqa: F811
    result = await begin(hass, entry)
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=False)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: NEW_TOKEN})
        await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_TOKEN] == NEW_TOKEN
    client = await hass_client_no_auth()
    export = "/api/wake_word_collector/export/hey_nova"
    assert (await client.get(export, headers={"X-Wakeword-Token": TOKEN})).status == 401


async def test_supported_http_flow_requires_admin(hass, entry, hass_client, hass_read_only_access_token):  # noqa: F811
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    path = "/api/config/config_entries/flow"
    payload = {"handler": DOMAIN, "entry_id": entry.entry_id}
    assert (
        await client.post(path, json=payload, headers={"Authorization": "Bearer invalid-synthetic-token"})
    ).status == 401
    response = await client.post(path, json=payload)
    assert response.status == 200
    form = await response.json()
    assert form["step_id"] == "reconfigure" and TOKEN not in json.dumps(form)
    response = await client.get("/api/config/config_entries/entry?domain=" + DOMAIN)
    metadata = await response.json()
    assert next(item for item in metadata if item["entry_id"] == entry.entry_id)["supports_reconfigure"] is True
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as reload_entry:
        response = await client.post(path + "/" + form["flow_id"], json={CONF_TOKEN: NEW_TOKEN})
        result = await response.json()
        await hass.async_block_till_done()
    assert response.status == 200 and result["reason"] == "reconfigure_successful"
    assert TOKEN not in json.dumps(result) and NEW_TOKEN not in json.dumps(result)
    assert entry.data[CONF_TOKEN] == NEW_TOKEN
    assert reload_entry.await_count == 1
    restricted = {"Authorization": "Bearer " + hass_read_only_access_token}
    assert (await client.get("/api/config/config_entries/entry", headers=restricted)).status == 200
    assert (await client.post(path, json=payload, headers=restricted)).status in {401, 403}
    assert entry.data[CONF_TOKEN] == NEW_TOKEN
