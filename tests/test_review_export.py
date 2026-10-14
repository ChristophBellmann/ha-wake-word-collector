"""Read-only token export of unconfirmed clips, separate from training."""

from datetime import UTC, datetime, timedelta

import pytest

from .test_collect import speech, wav_bytes
from .test_integration import TOKEN, entry  # noqa: F401
from .test_training import trained  # noqa: F401

PREFIX = "/api/wake_word_collector/export/hey_nova/review"


async def test_review_export_is_authenticated_filtered_and_readonly(entry, hass_client_no_auth):  # noqa: F811
    collector = entry.runtime_data
    body = wav_bytes(speech(3))
    [trigger] = await collector.async_add("bath", "", body, "trigger")
    await collector.async_add("kitchen", "", wav_bytes(speech(3.1)), "trigger")
    await collector.async_add("bath", "Hey Nova", wav_bytes(speech(1.2)), "utterance")
    before = await collector.async_list()
    client = await hass_client_no_auth()
    headers = {"X-Wakeword-Token": TOKEN}
    assert (await client.get(PREFIX)).status == 401
    assert (await client.get(PREFIX, headers={"X-Wakeword-Token": "wrong"})).status == 401
    response = await client.get(PREFIX, headers=headers)
    assert response.status == 200 and "no-store" in response.headers["Cache-Control"]
    report = await response.json()
    assert report["labels_confirmed"] is False and report["total"] == 2
    assert {item["category"] for item in report["clips"]} == {"triggers"}
    limited = await (await client.get(PREFIX + "?limit=1", headers=headers)).json()
    assert len(limited["clips"]) == 1 and limited["truncated"] is True
    filtered = await (await client.get(PREFIX + "?device=bath", headers=headers)).json()
    assert filtered["total"] == 1 and filtered["clips"][0]["sha256"] == trigger["sha256"]
    audio_url = filtered["clips"][0]["audio_url"]
    assert (await client.get(audio_url)).status == 401
    audio = await client.get(audio_url, headers=headers)
    assert audio.status == 200 and await audio.read() == body
    assert "no-store" in audio.headers["Cache-Control"]
    assert await collector.async_list() == before
    confirmed = await (await client.get("/api/wake_word_collector/export/hey_nova", headers=headers)).json()
    assert len(confirmed["candidates"]) == 1
    assert all(item["sha256"] != trigger["sha256"] for item in confirmed["candidates"])
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    filtered = await (await client.get(PREFIX, params={"created_after": future}, headers=headers)).json()
    assert filtered["total"] == 0
    assert (await client.get(PREFIX + "/negatives/bath/" + trigger["filename"], headers=headers)).status == 404
    await collector.async_review("triggers", "bath", trigger["filename"], "negative")
    assert (await client.get(audio_url, headers=headers)).status == 404


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=2001", "limit=no", "device=../bath", "created_after=garbage", "created_after=2026-10-14"],
)
async def test_review_export_rejects_bad_filter(entry, hass_client_no_auth, query):  # noqa: F811
    client = await hass_client_no_auth()
    assert (await client.get(PREFIX + "?" + query, headers={"X-Wakeword-Token": TOKEN})).status == 400


async def test_review_evidence_reaches_card_but_not_recorder(hass, trained, aioclient_mock, hass_ws_client):  # noqa: F811
    from homeassistant.setup import async_setup_component

    from .test_training import STATUS, TRAINER

    config_entry, _, _ = trained
    collector = config_entry.runtime_data
    [trigger] = await collector.async_add("bath", "", wav_bytes(speech(3)), "trigger")
    report = {
        "created_at": "fresh",
        "unique_clips": 1,
        "labels_changed": False,
        "clips": [
            {
                "sha256": trigger["sha256"],
                "transcript": "private synthetic test",
                "findings": ["negative_candidate"],
                "scores": [{"peak": 0.9}],
                "teachers": [{"peak": 0.1}],
            }
        ],
    }
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{TRAINER}/v1/status",
        json={
            **STATUS,
            "best_model_available": False,
            "review_profiles": ["review"],
            "review": {k: v for k, v in report.items() if k != "clips"},
        },
    )
    aioclient_mock.get(f"{TRAINER}/v1/review", json=report)
    collector.trainer.async_set_updated_data(await collector.trainer._async_update_data())
    assert await async_setup_component(hass, "websocket_api", {})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "wake_word_collector/list"})
    message = await ws.receive_json()
    result = message["result"]["collectors"][0]
    item = next(i for i in result["items"] if i["filename"] == trigger["filename"])
    assert item["analysis"]["transcript"] == "private synthetic test"
    assert result["review_profiles"] == ["review"]
    sensor = hass.states.get("sensor.hey_nova_training")
    assert "private synthetic test" not in str(sensor.attributes)
