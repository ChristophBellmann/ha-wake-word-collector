#!/usr/bin/env python3
"""Put a trained wake word model into your ESPHome configurations.

After a training, the integration keeps the model, its manifest and the
trainer's evaluation report in `<storage>/model/`. This tool, driven by a
small YAML file next to your ESPHome configurations:

1. copies the model as `<models>/<name>.tflite` and `.json` (the manifest
   names the new .tflite),
2. sets the model substitution in each listed device configuration,
3. sets each device's sensitivity substitutions from the report's curve:
   a substitution with factor 1 gets the cutoff the trainer chose (within
   its false activation budget); factor 2 the most sensitive cutoff with at
   most twice as many false activations per hour, and so on; never below
   `min_cutoff`. Values are 0-255, as `set_probability_cutoff` takes them,
4. with --build, runs your build command for every device not yet verified for this model; failed devices can be retried.

Run it with the Python that has ESPHome installed (it needs PyYAML):

    python3 model_update.py --config wake-word-model.yaml --dry-run
    python3 model_update.py --config wake-word-model.yaml [--build] [--commit]

See model_update.example.yaml for the configuration.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

DEFAULT_LADDER = {
    "wake_word_slight_cutoff_uint8": 1,
    "wake_word_moderate_cutoff_uint8": 2,
    "wake_word_very_cutoff_uint8": 5,
}


class UpdateError(Exception):
    pass


def load_config(path: Path) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("slug", "source", "devices"):
        if not config.get(key):
            raise UpdateError(f"{path}: '{key}' is missing")
    if not re.fullmatch(r"[a-z0-9_]+", str(config["slug"])):
        raise UpdateError("slug: only a-z, 0-9 and _")
    for device in config["devices"]:
        if not isinstance(device, dict) or not device.get("file") or not device.get("model"):
            raise UpdateError("every device needs 'file' and 'model' (the model substitution)")
    return config


def load_model(source: Path, slug: str) -> tuple[dict, bytes, dict, str]:
    manifest_path, model_path = source / f"{slug}.json", source / f"{slug}.tflite"
    if not manifest_path.is_file() or not model_path.is_file():
        raise UpdateError(f"no model in {source} ({slug}.json and {slug}.tflite)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report, ended = {}, ""
    info = source / "source.json"  # written by the integration
    if info.is_file():
        data = json.loads(info.read_text(encoding="utf-8"))
        report, ended = data.get("report") or {}, data.get("ended_at") or ""
    elif (source.parent / "report.json").is_file():  # a trainer project's export folder
        report = json.loads((source.parent / "report.json").read_text(encoding="utf-8"))
    return manifest, model_path.read_bytes(), report, ended


def ladder(manifest: dict, report: dict, factors: dict[str, float], min_cutoff: float) -> list[tuple[str, float, str]]:
    """(substitution, cutoff 0-1, note) from the least to the most sensitive step."""
    chosen = report.get("probability_cutoff") or manifest.get("micro", {}).get("probability_cutoff")
    if chosen is None:
        raise UpdateError("neither report nor manifest name a cutoff")
    budget = report.get("max_false_accepts_per_hour")
    curve = sorted(report.get("curve") or [], key=lambda point: point["cutoff"])
    steps, floor = [], float(chosen)
    for key, factor in sorted(factors.items(), key=lambda item: item[1]):
        cutoff = floor
        if factor > 1 and budget and curve:
            fits = [p for p in curve if min_cutoff <= p["cutoff"] < floor and p["faph"] <= factor * budget]
            if fits:
                cutoff = fits[0]["cutoff"]
        note = ""
        if factor <= 1 and report.get("recall") is not None:
            note = f"recall {report['recall']:.0%}, {report['false_accepts_per_hour']:.2f} false/h"
        else:
            point = min(curve, key=lambda p: abs(p["cutoff"] - cutoff)) if curve else None
            if point and abs(point["cutoff"] - cutoff) < 0.003:
                note = f"recall {point['recall']:.0%}, {point['faph']:.2f} false/h"
        steps.append((key, cutoff, note))
        floor = cutoff
    return steps


def set_substitution(text: str, key: str, value: str, comment: str, where: str) -> str:
    pattern = re.compile(rf"^(\s*){re.escape(key)}:.*$", re.MULTILINE)
    found = len(pattern.findall(text))
    if found != 1:
        raise UpdateError(f"{where}: '{key}' found {found} times, expected once")
    line = f"{key}: {value}" + (f"  # {comment}" if comment else "")
    return pattern.sub(lambda match: match.group(1) + line, text)


def build_command(build: list[str], device: dict, root: Path) -> list[str]:
    values = {**device, "file": os.path.relpath((root / device["file"]).resolve(), root)}
    try:
        return [part.format(**values) for part in build]
    except KeyError as err:
        raise UpdateError(f"{device['file']}: build placeholder {err} is missing in the device configuration") from err


def write_progress(path: Path, ledger: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(ledger, indent=2) + "\n")
    os.replace(temporary, path)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--name", help="file name without extension (default from the configuration)")
    parser.add_argument("--dry-run", action="store_true", help="only show what would change")
    parser.add_argument("--build", action="store_true", help="build and install unverified devices")
    parser.add_argument("--expected-sha256", help="wait for this exact model from the trainer")
    parser.add_argument("--wait", type=float, default=0, help="seconds to wait for the expected model and parity")
    parser.add_argument("--commit", action="store_true", help="git commit the changed files (no push)")
    args = parser.parse_args(argv)

    config_path = args.config.expanduser().resolve()
    config = load_config(config_path)
    root = config_path.parent
    slug = config["slug"]
    source = (root / Path(config["source"]).expanduser()).resolve()
    deadline = time.monotonic() + args.wait
    while True:
        try:
            manifest, model, report, ended = load_model(source, slug)
            digest = hashlib.sha256(model).hexdigest()
            if args.expected_sha256 and args.expected_sha256 != digest:
                raise UpdateError("the collector has not received the expected model yet")
            if config.get("require_parity"):
                parity = report.get("parity") or {}
                manifest_digest = hashlib.sha256((source / f"{slug}.json").read_bytes()).hexdigest()
                if (
                    parity.get("passed") is not True
                    or parity.get("candidate_sha256") != digest
                    or parity.get("candidate_manifest_sha256") != manifest_digest
                ):
                    raise UpdateError("no passed parity test for this model; nothing changed")
            break
        except (UpdateError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    day = (ended or dt.datetime.now(dt.UTC).date().isoformat())[:10]
    name = args.name or str(config.get("name", "{slug}_{date}")).format(
        slug=slug, date=day.replace("-", ""), hash=digest[:12]
    )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise UpdateError(f"invalid file name {name!r}")
    models = root / config.get("models", "models")
    target_model, target_manifest = models / f"{name}.tflite", models / f"{name}.json"
    if target_model.exists() and target_model.read_bytes() != model:
        raise UpdateError(f"{target_model.name} exists with other content; choose --name")
    manifest["model"] = target_model.name
    factors = config.get("ladder", DEFAULT_LADDER)
    min_cutoff = float(config.get("min_cutoff", 0.5))

    print(f"Model: {target_model.relative_to(root)}")
    if report.get("recall") is not None:
        print(
            f"Report: recall {report['recall']:.1%} of {report.get('positives')} recordings, "
            f"{report['false_accepts_per_hour']:.2f} false activations/h "
            f"({report.get('ambient_hours')} h background)"
        )
    stamp = f"trained {day}"
    changed: dict[Path, str] = {}
    for device in config["devices"]:
        path = (root / device["file"]).resolve()
        text = path.read_text(encoding="utf-8")
        reference = Path(os.path.relpath(target_manifest, path.parent)).as_posix()
        new = set_substitution(text, device["model"], reference, "", device["file"])
        steps = ladder(manifest, report, device.get("ladder", factors), min_cutoff)
        for key, cutoff, note in steps:
            comment = f"p={cutoff:.3f}, {stamp}" + (f", {note}" if note else "")
            new = set_substitution(new, key, f'"{round(cutoff * 255)}"', comment, device["file"])
        print(f"  {device['file']}: " + ", ".join(f"{key}={round(c * 255)}" for key, c, _ in steps))
        if new != text:
            changed[path] = new
    if args.dry_run:
        print("Dry run: nothing written.")
        return 0

    models.mkdir(parents=True, exist_ok=True)
    target_model.write_bytes(model)
    target_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for path, text in changed.items():
        path.write_text(text, encoding="utf-8")
    print(f"Written: model and {len(changed)} device configuration(s).")

    build = config.get("build")
    if args.build:
        if not isinstance(build, list) or not build or not all(isinstance(part, str) for part in build):
            raise UpdateError("--build needs a command argument list in 'build'")
        # Runtime data belongs beside the downloaded model, outside version control.
        ledger_path = source / "rollout.json"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.is_file() else {}
        if ledger.get("model_sha256") != digest:
            ledger = {"model_sha256": digest, "devices": {}}
        failures = []
        for device in config["devices"]:
            path = (root / device["file"]).resolve()
            command = build_command(build, device, root)
            # A changed firmware configuration or command needs verification again.
            fingerprint = hashlib.sha256(
                path.read_bytes() + target_manifest.read_bytes() + json.dumps(command).encode()
            ).hexdigest()
            previous = ledger["devices"].get(device["file"], {})
            if previous.get("verified") and previous.get("fingerprint") == fingerprint:
                print(f"Already verified: {device['file']}")
                continue
            entry = {"fingerprint": fingerprint, "verified": False, "started_at": dt.datetime.now(dt.UTC).isoformat()}
            ledger["devices"][device["file"]] = entry
            write_progress(ledger_path, ledger)
            print(f"Building: {device['file']}", flush=True)
            try:
                result = subprocess.run(command, cwd=root, check=False)
                entry.update(verified=result.returncode == 0, exit_code=result.returncode)
            except OSError as err:
                entry["error"] = type(err).__name__
            entry["ended_at"] = dt.datetime.now(dt.UTC).isoformat()
            write_progress(ledger_path, ledger)
            if not entry["verified"]:
                failures.append(device["file"])
        if failures:
            raise UpdateError(
                "build or device verification failed: " + ", ".join(failures) + "; rerun --build to retry"
            )
    elif build and changed:
        print("Next, build and install:")
        for device in config["devices"]:
            if (root / device["file"]).resolve() in changed:
                print("  " + " ".join(build_command(build, device, root)))
    if args.commit:
        # Include all configured devices on a retry, even if their YAML already matches.
        files = [str(target_model), str(target_manifest), *[str(root / d["file"]) for d in config["devices"]]]
        subprocess.run(["git", "-C", str(root), "add", "--", *files], check=True)
        diff = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--quiet", "--", *files], check=False)
        if diff.returncode == 1:
            subprocess.run(
                ["git", "-C", str(root), "commit", "-m", f"Wake word model {name}", "--", *files], check=True
            )
        elif diff.returncode:
            raise UpdateError("could not check the staged model files")
    return 0


def main() -> None:
    try:
        sys.exit(run())
    except UpdateError as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
