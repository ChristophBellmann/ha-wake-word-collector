"""Workstation decisions require an unchanged unconfirmed trigger and keep evidence."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.wake_word_collector.const import CONF_ALLOW_CONSENSUS_REVIEW

from .test_collect import speech, wav_bytes
from .test_integration import TOKEN, entry  # noqa: F401


@pytest.fixture
async def consensus_entry(hass, entry):  # noqa: F811
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)):
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CONSENSUS_REVIEW: True})
        await hass.async_block_till_done()
    return entry


async def test_consensus_sha_guard_and_recoverable_negative(consensus_entry, hass_client_no_auth):
    collector = consensus_entry.runtime_data
    [record] = await collector.async_add("bath", "", wav_bytes(speech(3)), "trigger")
    url = "/api/wake_word_collector/export/hey_nova/review/triggers/bath/" + record["filename"]
    payload = {
        "decision": "negative",
        "sha256": record["sha256"],
        "review_id": "test_run",
        "teacher_hashes": ["a" * 64],
        "reason": "known_negative_teacher_stt_consensus",
    }
    client = await hass_client_no_auth()
    headers = {"X-Wakeword-Token": TOKEN}
    assert (await client.post(url, json=payload)).status == 401
    assert (await client.post(url, json={**payload, "decision": "accept"}, headers=headers)).status == 400
    assert (await client.post(url, json={**payload, "sha256": "0" * 64}, headers=headers)).status == 409
    assert (await collector.async_list())[0]["category"] == "triggers"
    response = await client.post(url, json=payload, headers=headers)
    assert response.status == 200 and "no-store" in response.headers["Cache-Control"]
    result = await response.json()
    assert result["category"] == "negatives" and result["sha256"] == record["sha256"]
    assert (await client.post(url, json=payload, headers=headers)).status == 409
    [item] = await collector.async_list()
    assert item["auto_negative"] is True
    evidence = json.loads((collector.store.root / "analysis_decisions.jsonl").read_text().splitlines()[0])
    assert evidence["evidence"]["review_id"] == "test_run"
    # Owner can recover the clip through the ordinary review action.
    await collector.async_review("negatives", "bath", record["filename"], "accept")
    assert (await collector.async_list())[0]["category"] == "candidates"


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": []},
        {"teacher_hashes": []},
        {"teacher_hashes": [[]]},
        {"review_id": "../secret"},
        {"reason": "unspecified"},
    ],
)
async def test_invalid_consensus_cannot_change_labels(consensus_entry, hass_client_no_auth, change):
    collector = consensus_entry.runtime_data
    [record] = await collector.async_add("bath", "", wav_bytes(speech(3)), "trigger")
    url = "/api/wake_word_collector/export/hey_nova/review/triggers/bath/" + record["filename"]
    payload = {
        "decision": "negative",
        "sha256": record["sha256"],
        "review_id": "test_run",
        "teacher_hashes": ["a" * 64],
        "reason": "known_negative_teacher_stt_consensus",
        **change,
    }
    client = await hass_client_no_auth()
    assert (await client.post(url, json=payload, headers={"X-Wakeword-Token": TOKEN})).status == 400
    assert (await collector.async_list())[0]["category"] == "triggers"


async def test_default_option_never_grants_consensus_write_access(entry, hass_client_no_auth):  # noqa: F811
    collector = entry.runtime_data
    [record] = await collector.async_add("bath", "", wav_bytes(speech(3)), "trigger")
    client = await hass_client_no_auth()
    url = "/api/wake_word_collector/export/hey_nova/review/triggers/bath/" + record["filename"]
    response = await client.post(url, json={"decision": "negative"}, headers={"X-Wakeword-Token": TOKEN})
    assert response.status == 403
    assert (await collector.async_list())[0]["category"] == "triggers"
