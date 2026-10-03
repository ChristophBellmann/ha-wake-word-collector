"""Core tests, ported from the standalone collector and generalised."""

import io
import json
import struct
import wave
from pathlib import Path

import pytest

from custom_components.wake_word_collector.collect import (
    CANDIDATES,
    CONTROL,
    NEEDS_REVIEW,
    REJECTED_QUALITY,
    CollectorError,
    Phrases,
    Store,
    analyse,
    normalize,
    split_repetitions,
)

HEY_MOMO = Phrases.build("Hey Momo", ["hei momo", "hai momo", "hey mumu"], ["fertig", "reicht", "beenden"])


def wav_bytes(samples, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def speech(seconds: float, amplitude: int = 3000, rate: int = 16000) -> list[int]:
    return [amplitude if i % 2 else -amplitude for i in range(int(seconds * rate))]


def silence(seconds: float, rate: int = 16000) -> list[int]:
    return [0] * int(seconds * rate)


def write(path: Path, samples) -> Path:
    path.write_bytes(wav_bytes(samples))
    return path


# Transcripts -------------------------------------------------------------------


def test_normalize() -> None:
    assert normalize("Hey, Mömo!") == "hey momo"


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ("Hey, Momo!", CANDIDATES),
        ("Hey Mumu! Hey Mumu!", CANDIDATES),
        ("Hey Momon! Hey Momon!", CANDIDATES),
        ("Hai Momo", CANDIDATES),
        ("Hey Momondo", NEEDS_REVIEW),  # longer similar word stays out
        ("Hey Momo, mach das Licht an", NEEDS_REVIEW),  # wake word with other speech
        ("Ich bin fertig", CONTROL),
        ("Das reicht jetzt", CONTROL),
        ("Wie wird das Wetter", NEEDS_REVIEW),
        ("", NEEDS_REVIEW),
    ],
)
def test_classify(transcript: str, expected: str) -> None:
    assert HEY_MOMO.classify(transcript) == expected


def test_repetitions_count_complete_phrases() -> None:
    assert HEY_MOMO.repetitions("Hey Momo, Hey Momo") == 2
    assert HEY_MOMO.repetitions("Hey Momo Hei Momo Hey Mumu") == 3
    assert HEY_MOMO.repetitions("Hey Momo Momo") == 0


def test_single_word_wake_word() -> None:
    jarvis = Phrases.build("Jarvis", "Jarvas")
    assert jarvis.repetitions("Jarvis. Jarvas, Jarvis!") == 3
    assert jarvis.classify("Hey Jarvis") == NEEDS_REVIEW


def test_empty_phrase_is_refused() -> None:
    with pytest.raises(CollectorError) as err:
        Phrases.build("  !! ")
    assert err.value.code == "phrase_empty"


# Audio --------------------------------------------------------------------------


def test_analyse(tmp_path: Path) -> None:
    good = analyse(write(tmp_path / "good.wav", speech(2)))
    assert good["duration_ms"] == 2000 and good["quality_reasons"] == []
    assert "too_quiet" in analyse(write(tmp_path / "quiet.wav", silence(2)))["quality_reasons"]
    reasons = analyse(write(tmp_path / "clip.wav", [32767] * 32000))["quality_reasons"]
    assert "clipping" in reasons and "dc_offset" in reasons
    with pytest.raises(CollectorError) as err:
        analyse(write(tmp_path / "short.wav", speech(0.2)))
    assert err.value.code == "duration"


def test_analyse_refuses_stereo(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0" * 128000)
    with pytest.raises(CollectorError) as err:
        analyse(path)
    assert err.value.code == "not_mono_pcm16"


def test_split_at_quiet_section(tmp_path: Path) -> None:
    path = write(tmp_path / "two.wav", speech(0.8) + silence(0.3) + speech(0.8))
    parts = split_repetitions(path, 2, tmp_path)
    durations = [analyse(p)["duration_ms"] for p in parts]
    assert len(parts) == 2 and sum(durations) == 1900
    assert 800 <= durations[0] <= 1100


# Store --------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "hey_momo", HEY_MOMO)
    store.ensure()
    return store


def test_add_sorts_by_transcript_and_quality(store: Store) -> None:
    [good] = store.add("Kitchen", "Hey Momo", wav_bytes(speech(1.5)))
    assert good["category"] == CANDIDATES and good["device"] == "kitchen"
    [other] = store.add("kitchen", "Wie spät ist es", wav_bytes(speech(1.5, 2000)))
    assert other["category"] == NEEDS_REVIEW
    [quiet] = store.add("kitchen", "Hey Momo", wav_bytes(silence(1.5)))
    assert quiet["category"] == REJECTED_QUALITY
    [stop] = store.add("kitchen", "Ich bin fertig", wav_bytes(speech(1.5, 2500)))
    assert stop["category"] == CONTROL
    stats = store.stats()
    assert stats["candidates"] == 1 and stats["needs_review"] == 1
    assert stats["rejected_quality"] == 1 and stats["control"] == 1
    assert stats["candidates_by_device"] == {"kitchen": 1}
    assert len(store.records()) == 4


def test_add_splits_repetitions(store: Store) -> None:
    records = store.add("bath", "Hey Momo, Hey Momo", wav_bytes(speech(0.8) + silence(0.3) + speech(0.8)))
    assert [r["split_index"] for r in records] == [1, 2]
    assert store.stats()["candidates"] == 2


def test_add_refuses_bad_device_and_size(store: Store) -> None:
    with pytest.raises(CollectorError) as err:
        store.add("../etc", "Hey Momo", wav_bytes(speech(1)))
    assert err.value.code == "invalid_device"
    with pytest.raises(CollectorError) as err:
        store.add("bath", "Hey Momo", b"RIFF")
    assert err.value.code == "invalid_size"


def test_review_moves_recoverably(store: Store) -> None:
    [other] = store.add("office", "Hey Momi", wav_bytes(speech(1.5)))
    listed = store.list()
    assert listed[0]["filename"] == other["filename"] and listed[0]["category"] == NEEDS_REVIEW
    record = store.review(NEEDS_REVIEW, "office", other["filename"], "accept")
    assert record["category"] == CANDIDATES
    assert store.records()[("office", other["filename"])]["category"] == CANDIDATES
    store.review(CANDIDATES, "office", other["filename"], "reject", "car outside")
    assert store.stats()["rejected_quality"] == 1
    reviews = (store.root / "reviews.jsonl").read_text().splitlines()
    assert json.loads(reviews[-1])["note"] == "car outside"
    with pytest.raises(CollectorError) as err:
        store.review(CANDIDATES, "office", other["filename"], "reject")
    assert err.value.code == "clip_not_found"


def test_review_latest_notes_or_rejects(store: Store) -> None:
    store.add("office", "Hey Momo", wav_bytes(speech(1.5)))
    record = store.review_latest("dishwasher running")
    assert record["decision"] == "note"
    assert next(iter(store.records().values()))["note"] == "dishwasher running"
    store.review_latest("bad", "reject", "office")
    assert store.stats()["candidates"] == 0
    with pytest.raises(CollectorError) as err:
        store.review_latest("again", "reject")
    assert err.value.code == "no_candidate"


def test_trim_keep_remove_extract(store: Store) -> None:
    [clip] = store.add("lab", "Hey Momo", wav_bytes(speech(3)))
    name = clip["filename"]
    assert store.trim(CANDIDATES, "lab", name, 500, 2500)["duration_ms"] == 2000
    assert store.records()[("lab", name)]["trim_mode"] == "keep"
    assert store.trim(CANDIDATES, "lab", name, 0, 500, "remove")["duration_ms"] == 1500
    result = store.trim(CANDIDATES, "lab", name, 0, 700, "extract")
    assert result["duration_ms"] == 800
    assert store.stats()["candidates"] == 2
    assert store.records()[("lab", result["new_filename"])]["extracted_from"] == name
    assert len(list((store.root / "trim_backups" / "lab").glob("*.wav"))) == 3


@pytest.mark.parametrize(
    ("start", "end", "mode", "code"),
    [(0, 200, "keep", "duration"), (1000, 500, "keep", "trim_range"), (0, 1000, "cut", "invalid_mode")],
)
def test_trim_refuses(store: Store, start, end, mode, code) -> None:
    [clip] = store.add("lab", "Hey Momo", wav_bytes(speech(1.2)))
    with pytest.raises(CollectorError) as err:
        store.trim(CANDIDATES, "lab", clip["filename"], start, end, mode)
    assert err.value.code == code


def test_unsafe_paths_are_refused(store: Store) -> None:
    with pytest.raises(CollectorError) as err:
        store.audio(CANDIDATES, "lab", "../../secrets.yaml")
    assert err.value.code == "invalid_clip"


def test_reads_layout_of_older_collectors(store: Store) -> None:
    legacy = store.root / "rejected_transcript" / "sat1"
    legacy.mkdir(parents=True)
    write(legacy / "ha_sat1_20260801T120000_0123456789ab.wav", speech(1))
    assert store.stats()["needs_review"] == 1
    assert store.list()[0]["category"] == "rejected_transcript"
