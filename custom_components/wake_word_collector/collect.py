"""Storage and processing of wake word clips, free of Home Assistant imports.

Layout below the storage folder (compatible with earlier standalone collectors):

    <category>/<device>/ha_<device>_<UTC time>_<sha256[:12]>.wav
    manifest.jsonl      one JSON line per stored clip
    reviews.jsonl       one JSON line per review decision
    trim_backups/<device>/   originals before trimming

Categories: ``candidates`` (the wake word, ready for training), ``needs_review``
(anything else a person should listen to), ``control`` (spoken commands such as
"stop recording"), ``rejected_quality`` (too quiet, clipped, offset, or rejected
by a person; kept for recovery). ``rejected_transcript`` from older collectors
is treated like ``needs_review``.

Errors are raised as :class:`CollectorError` with a code that the integration
translates.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import tempfile
import unicodedata
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

CANDIDATES = "candidates"
NEEDS_REVIEW = "needs_review"
CONTROL = "control"
REJECTED_QUALITY = "rejected_quality"
LEGACY_REVIEW = "rejected_transcript"
CATEGORIES = (CANDIDATES, NEEDS_REVIEW, CONTROL, REJECTED_QUALITY, LEGACY_REVIEW)
REVIEWABLE = (CANDIDATES, NEEDS_REVIEW, LEGACY_REVIEW)

MAX_BODY_BYTES = 4 * 1024 * 1024
MIN_MS = 500
MAX_MS = 15000
DEVICE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
FILENAME_RE = re.compile(r"^ha_[a-z0-9][a-z0-9_-]{0,63}_[0-9]{8}T[0-9]{6}_[a-f0-9]{12}\.wav$")
SAMPLE_RATES = (16000, 48000)


class CollectorError(ValueError):
    """Invalid input or state; `code` is a translation key."""

    def __init__(self, code: str, **placeholders: Any) -> None:
        super().__init__(code)
        self.code = code
        self.placeholders = {k: str(v) for k, v in placeholders.items()}


def utc_now() -> datetime:
    return datetime.now(UTC)


# -- Transcripts ---------------------------------------------------------------


def normalize(value: str) -> str:
    """Lower case, no accents or punctuation: "Hey, Mömo!" -> "hey momo"."""
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def word_matches(heard: str, expected: str) -> bool:
    """Speech recognition sometimes appends a liaison "n" or "s" to a name
    ("Momon"). Bounded on purpose: longer words stay different."""
    return heard == expected or (
        len(expected) >= 4 and len(heard) == len(expected) + 1 and heard[:-1] == expected and heard[-1] in "ns"
    )


@dataclass
class Phrases:
    """The wake word and the transcriptions that count as it."""

    accepted: list[list[str]]
    control: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, phrase: str, variants: list[str] | str = (), control: list[str] | str = ()) -> Phrases:
        if isinstance(variants, str):
            variants = variants.split(",")
        if isinstance(control, str):
            control = control.split(",")
        accepted = []
        for item in [phrase, *variants]:
            words = normalize(item).split()
            if words and words not in accepted:
                accepted.append(words)
        if not accepted:
            raise CollectorError("phrase_empty")
        return cls(accepted, [normalize(c) for c in control if normalize(c)])

    def repetitions(self, transcript: str) -> int:
        """How many complete wake word phrases the transcript consists of; 0 if
        anything else was said. Phrases are never split into single words."""
        words = normalize(transcript).split()
        if not words:
            return 0
        # best[i]: number of phrases covering words[:i], or None.
        best: list[int | None] = [0] + [None] * len(words)
        for start in range(len(words)):
            if best[start] is None:
                continue
            for phrase in self.accepted:
                end = start + len(phrase)
                if end <= len(words) and all(word_matches(h, e) for h, e in zip(words[start:end], phrase, strict=True)):
                    count = best[start] + 1
                    if best[end] is None or count < best[end]:
                        best[end] = count
        return best[-1] or 0

    def classify(self, transcript: str) -> str:
        if self.repetitions(transcript):
            return CANDIDATES
        normalized = f" {normalize(transcript)} "
        if any(f" {word} " in normalized for word in self.control):
            return CONTROL
        # The transcript is only a hint: an unusual pronunciation must not get
        # lost because the recognizer spelled it differently.
        return NEEDS_REVIEW


# -- Audio -----------------------------------------------------------------------


def read_wav(path: Path) -> tuple[wave._wave_params, bytes]:
    try:
        with wave.open(str(path), "rb") as source:
            params = source.getparams()
            frames = source.readframes(params.nframes)
    except (wave.Error, EOFError) as err:
        raise CollectorError("not_wav") from err
    if params.nchannels != 1 or params.sampwidth != 2:
        raise CollectorError("not_mono_pcm16")
    if params.framerate not in SAMPLE_RATES:
        raise CollectorError("sample_rate")
    return params, frames


def write_wav(path: Path, params, frames: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with wave.open(str(temporary), "wb") as target:
        target.setparams(params)
        target.writeframes(frames)
    temporary.replace(path)


def analyse(path: Path) -> dict[str, Any]:
    """Duration and quality of a clip. Quality problems do not raise; they are
    listed in `quality_reasons` and send the clip to rejected_quality."""
    params, frames = read_wav(path)
    count = len(frames) // 2
    duration_ms = round(count * 1000 / params.framerate)
    if not MIN_MS <= duration_ms <= MAX_MS:
        raise CollectorError("duration", min=MIN_MS, max=MAX_MS)
    samples = struct.unpack(f"<{count}h", frames[: count * 2])
    mean = sum(samples) / count
    rms = math.sqrt(sum((s - mean) ** 2 for s in samples) / count)
    clipping = sum(abs(s) >= 32760 for s in samples) / count
    reasons = []
    if rms < 130:
        reasons.append("too_quiet")
    if clipping > 0.10:
        reasons.append("clipping")
    if abs(mean) > 6000:
        reasons.append("dc_offset")
    return {
        "sample_rate": params.framerate,
        "frames": count,
        "duration_ms": duration_ms,
        "rms_dbfs": round(20 * math.log10(max(rms, 1) / 32768), 2),
        "peak_abs": max(abs(s) for s in samples),
        "clipping_ratio": round(clipping, 6),
        "dc_offset": round(mean, 2),
        "quality_reasons": reasons,
    }


def split_repetitions(path: Path, count: int, output_dir: Path) -> list[Path]:
    """Split a clip of `count` complete phrases at the quietest point near
    each equal partition."""
    if count <= 1:
        return [path]
    params, frames = read_wav(path)
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    rate = params.framerate
    minimum = rate // 2
    if len(samples) < count * minimum:
        raise CollectorError("split_too_short")
    prefix = [0]
    for sample in samples:
        prefix.append(prefix[-1] + abs(sample))
    window = max(1, round(rate * 0.06))
    cuts = [0]
    for index in range(1, count):
        target = round(len(samples) * index / count)
        radius = round(len(samples) / count * 0.25)
        lower = max(cuts[-1] + minimum, target - radius)
        upper = min(len(samples) - (count - index) * minimum, target + radius)
        if lower > upper:
            raise CollectorError("split_no_boundary")
        cuts.append(
            min(
                range(lower, upper + 1),
                key=lambda pos: prefix[min(len(samples), pos + window)] - prefix[max(0, pos - window)],
            )
        )
    cuts.append(len(samples))
    parts = []
    for index, (start, end) in enumerate(pairwise(cuts), start=1):
        part = output_dir / f"{path.stem}_part{index}.wav"
        write_wav(part, params, struct.pack(f"<{end - start}h", *samples[start:end]))
        parts.append(part)
    return parts


def _window(params, frames: bytes, start_ms: int, end_ms: int) -> tuple[int, int]:
    try:
        start_ms, end_ms = int(start_ms), int(end_ms)
    except (TypeError, ValueError) as err:
        raise CollectorError("trim_range") from err
    total = len(frames) // 2
    start = min(total, max(0, round(start_ms * params.framerate / 1000)))
    end = min(total, max(0, round(end_ms * params.framerate / 1000)))
    if end <= start:
        raise CollectorError("trim_range")
    return start, end


def _check_duration(frames_count: int, rate: int) -> int:
    duration_ms = round(frames_count * 1000 / rate)
    if not MIN_MS <= duration_ms <= MAX_MS:
        raise CollectorError("duration", min=MIN_MS, max=MAX_MS)
    return duration_ms


# -- Store -----------------------------------------------------------------------


class Store:
    """All clips of one wake word below `root`."""

    def __init__(self, root: Path, phrases: Phrases) -> None:
        self.root = root
        self.phrases = phrases

    def ensure(self) -> None:
        for category in (CANDIDATES, NEEDS_REVIEW, CONTROL, REJECTED_QUALITY):
            (self.root / category).mkdir(parents=True, exist_ok=True)

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.jsonl"

    def _append(self, name: str, record: dict) -> None:
        with (self.root / name).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def _path(self, category: str, device: str, filename: str, categories=REVIEWABLE) -> Path:
        if not DEVICE_RE.fullmatch(device) or not FILENAME_RE.fullmatch(filename):
            raise CollectorError("invalid_clip")
        if category not in categories:
            raise CollectorError("invalid_category")
        path = self.root / category / device / filename
        if not path.is_file():
            raise CollectorError("clip_not_found")
        return path

    def records(self) -> dict[tuple[str, str], dict]:
        result: dict[tuple[str, str], dict] = {}
        if not self.manifest.is_file():
            return result
        for line in self.manifest.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("device") and record.get("filename"):
                result[(str(record["device"]), str(record["filename"]))] = record
        return result

    def _update_record(self, device: str, filename: str, updates: dict) -> None:
        if not self.manifest.is_file():
            return
        lines, changed = [], False
        for line in self.manifest.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if record.get("device") == device and record.get("filename") == filename:
                record.update(updates)
                changed = True
                line = json.dumps(record, ensure_ascii=False, sort_keys=True)
            lines.append(line)
        if changed:
            self.manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _stage(self, part: Path, category: str, device: str, timestamp: datetime) -> tuple[str, str]:
        sha256 = hashlib.sha256(part.read_bytes()).hexdigest()
        target_dir = self.root / category / device
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"ha_{device}_{timestamp:%Y%m%dT%H%M%S}_{sha256[:12]}.wav"
        target = target_dir / filename
        if target.exists():
            part.unlink(missing_ok=True)
        else:
            os.replace(part, target)
        return filename, sha256

    # Upload -------------------------------------------------------------------

    def add(self, device: str, transcript: str, body: bytes) -> list[dict]:
        """Store an uploaded clip; returns one record per stored part."""
        device = device.lower()
        if not DEVICE_RE.fullmatch(device):
            raise CollectorError("invalid_device")
        if not 44 <= len(body) <= MAX_BODY_BYTES:
            raise CollectorError("invalid_size")
        self.ensure()
        incoming = self.root / ".incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=incoming, suffix=".wav", delete=False) as tmp:
            tmp.write(body)
            temp_path = Path(tmp.name)
        try:
            meta = analyse(temp_path)
            category = self.phrases.classify(transcript)
            if meta["quality_reasons"]:
                category = REJECTED_QUALITY
            repetitions = self.phrases.repetitions(transcript)
            parts = (
                split_repetitions(temp_path, repetitions, incoming)
                if category == CANDIDATES and repetitions > 1
                else [temp_path]
            )
            timestamp = utc_now()
            upload_sha = hashlib.sha256(body).hexdigest()
            records = []
            for index, part in enumerate(parts, start=1):
                part_meta = analyse(part)
                part_category = REJECTED_QUALITY if part_meta["quality_reasons"] else category
                filename, sha256 = self._stage(part, part_category, device, timestamp)
                record = {
                    "created_at": timestamp.isoformat(),
                    "device": device,
                    "category": part_category,
                    "filename": filename,
                    "transcript": transcript,
                    "normalized_transcript": normalize(transcript),
                    "repetition_count": repetitions,
                    "split_index": index if len(parts) > 1 else None,
                    "split_count": len(parts),
                    "source_upload_sha256": upload_sha,
                    "sha256": sha256,
                    **part_meta,
                }
                self._append("manifest.jsonl", record)
                records.append(record)
            return records
        finally:
            temp_path.unlink(missing_ok=True)

    # Listing ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        counts, by_device, latest = {}, {}, 0.0
        for category in CATEGORIES:
            files = list((self.root / category).glob("*/*.wav"))
            counts[category] = len(files)
            for path in files:
                latest = max(latest, path.stat().st_mtime)
                if category == CANDIDATES:
                    by_device[path.parent.name] = by_device.get(path.parent.name, 0) + 1
        return {
            "candidates": counts[CANDIDATES],
            "needs_review": counts[NEEDS_REVIEW] + counts[LEGACY_REVIEW],
            "control": counts[CONTROL],
            "rejected_quality": counts[REJECTED_QUALITY],
            "total": sum(counts.values()),
            "candidates_by_device": by_device,
            "latest_recording_at": datetime.fromtimestamp(latest, UTC).isoformat() if latest else None,
        }

    def list(self, categories=REVIEWABLE) -> list[dict]:
        records = self.records()
        items = []
        for category in categories:
            for path in (self.root / category).glob("*/*.wav"):
                device = path.parent.name
                if not DEVICE_RE.fullmatch(device) or not FILENAME_RE.fullmatch(path.name):
                    continue
                record = records.get((device, path.name), {})
                items.append(
                    {
                        "category": category,
                        "device": device,
                        "filename": path.name,
                        "created_at": record.get("created_at")
                        or datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                        "transcript": record.get("transcript", ""),
                        "duration_ms": record.get("duration_ms"),
                        "rms_dbfs": record.get("rms_dbfs"),
                        "repetition_count": record.get("repetition_count", 0),
                        "note": record.get("note", ""),
                    }
                )
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def audio(self, category: str, device: str, filename: str) -> Path:
        return self._path(category, device, filename, CATEGORIES)

    # Review -------------------------------------------------------------------

    def review(self, category: str, device: str, filename: str, decision: str, note: str = "") -> dict:
        """accept: move to candidates; reject: move to rejected_quality (recoverable)."""
        if decision not in ("accept", "reject"):
            raise CollectorError("invalid_decision")
        if decision == "accept" and category == CANDIDATES:
            raise CollectorError("already_accepted")
        source = self._path(category, device, filename)
        target_category = CANDIDATES if decision == "accept" else REJECTED_QUALITY
        target_dir = self.root / target_category / device
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        if target.exists():
            raise CollectorError("already_reviewed")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        os.replace(source, target)
        record = {
            "reviewed_at": utc_now().isoformat(),
            "device": device,
            "decision": decision,
            "note": note,
            "filename": filename,
            "sha256": digest,
            "previous_category": category,
            "category": target_category,
        }
        self._append("reviews.jsonl", record)
        self._update_record(device, filename, {"category": target_category})
        return record

    def review_latest(self, note: str, decision: str = "note", device: str | None = None) -> dict:
        """Note or reject the newest candidate ("the last one was bad"), of one
        device or of all."""
        if decision not in ("note", "reject"):
            raise CollectorError("invalid_decision")
        if device is not None:
            device = device.lower()
            if not DEVICE_RE.fullmatch(device):
                raise CollectorError("invalid_device")
        pattern = f"{device}/*.wav" if device else "*/*.wav"
        files = sorted((self.root / CANDIDATES).glob(pattern), key=lambda p: p.stat().st_mtime)
        if not files:
            raise CollectorError("no_candidate")
        latest = files[-1]
        device = latest.parent.name
        if decision == "reject":
            return self.review(CANDIDATES, device, latest.name, "reject", note)
        self._update_record(device, latest.name, {"note": note})
        record = {
            "reviewed_at": utc_now().isoformat(),
            "device": device,
            "decision": "note",
            "note": note,
            "filename": latest.name,
            "category": CANDIDATES,
        }
        self._append("reviews.jsonl", record)
        return record

    # Editing ------------------------------------------------------------------

    def _backup(self, path: Path, device: str) -> Path:
        backup_dir = self.root / "trim_backups" / device
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{path.stem}.{utc_now():%Y%m%dT%H%M%S%fZ}.wav"
        backup.write_bytes(path.read_bytes())
        return backup

    def trim(self, category: str, device: str, filename: str, start_ms, end_ms, mode: str = "keep") -> dict:
        """keep: keep only the window; remove: cut the window out; extract: save
        the window as a new candidate and cut it out of the original."""
        if mode not in ("keep", "remove", "extract"):
            raise CollectorError("invalid_mode")
        path = self._path(category, device, filename)
        params, frames = read_wav(path)
        start, end = _window(params, frames, start_ms, end_ms)
        window = frames[start * 2 : end * 2]
        rest = frames[: start * 2] + frames[end * 2 :]
        result: dict[str, Any] = {"mode": mode}
        if mode == "extract":
            _check_duration(end - start, params.framerate)
            _check_duration(len(rest) // 2, params.framerate)
        kept = window if mode == "keep" else rest
        duration_ms = _check_duration(len(kept) // 2, params.framerate)
        result["backup"] = str(self._backup(path, device))
        if mode == "extract":
            incoming = self.root / ".incoming"
            incoming.mkdir(parents=True, exist_ok=True)
            segment = incoming / f"extract-{device}-{filename}"
            write_wav(segment, params, window)
            meta = analyse(segment)
            new_name, sha256 = self._stage(segment, CANDIDATES, device, utc_now())
            original = self.records().get((device, filename), {})
            self._append(
                "manifest.jsonl",
                {
                    "created_at": utc_now().isoformat(),
                    "device": device,
                    "category": CANDIDATES,
                    "filename": new_name,
                    "transcript": original.get("transcript", ""),
                    "sha256": sha256,
                    "extracted_from": filename,
                    "extract_start_ms": round(start * 1000 / params.framerate),
                    "extract_end_ms": round(end * 1000 / params.framerate),
                    **meta,
                },
            )
            result["new_filename"] = new_name
        write_wav(path, params, kept)
        meta = analyse(path)
        self._update_record(
            device,
            filename,
            {
                **meta,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "trimmed_at": utc_now().isoformat(),
                "trim_mode": mode,
                "trim_start_ms": round(start * 1000 / params.framerate),
                "trim_end_ms": round(end * 1000 / params.framerate),
            },
        )
        result["duration_ms"] = duration_ms
        return result
