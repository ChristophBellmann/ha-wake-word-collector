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

The integration uses the same steps for its rollout through the ESPHome
Device Builder (button "Roll out model"); this file is also the command line
tool. Run it with the Python that has ESPHome installed (it needs PyYAML):

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_LADDER = {
    "wake_word_slight_cutoff_uint8": 1,
    "wake_word_moderate_cutoff_uint8": 2,
    "wake_word_very_cutoff_uint8": 5,
}
LEDGER = "rollout.json"


class UpdateError(Exception):
    pass


@dataclass
class DevicePlan:
    """One device configuration with the new model and sensitivity steps."""

    device: dict[str, Any]
    path: Path
    steps: list[tuple[str, float, str]]
    text: str
    changed: bool

    @property
    def file(self) -> str:
        return self.device["file"]


@dataclass
class Plan:
    """Everything an update would write, computed without writing."""

    root: Path
    config: dict[str, Any]
    source: Path
    digest: str
    name: str
    day: str
    model: bytes
    manifest: dict[str, Any]
    report: dict[str, Any]
    target_model: Path
    target_manifest: Path
    devices: list[DevicePlan] = field(default_factory=list)

    @property
    def changed(self) -> list[DevicePlan]:
        return [device for device in self.devices if device.changed]

    def summary(self) -> dict[str, Any]:
        return {
            "model": self.target_manifest.name,
            "model_sha256": self.digest,
            "recall": self.report.get("recall"),
            "false_accepts_per_hour": self.report.get("false_accepts_per_hour"),
            "devices": {
                device.file: {key: round(cutoff * 255) for key, cutoff, _ in device.steps} for device in self.devices
            },
            "changed": [device.file for device in self.changed],
        }


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as err:
        raise UpdateError(f"{path}: {err.strerror or err}") from err
    except yaml.YAMLError as err:
        raise UpdateError(f"{path}: not valid YAML") from err
    if not isinstance(config, dict):
        raise UpdateError(f"{path}: not a mapping")
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


def check_parity(report: dict, source: Path, slug: str, digest: str) -> None:
    parity = report.get("parity") or {}
    manifest_digest = hashlib.sha256((source / f"{slug}.json").read_bytes()).hexdigest()
    if (
        parity.get("passed") is not True
        or parity.get("candidate_sha256") != digest
        or parity.get("candidate_manifest_sha256") != manifest_digest
    ):
        raise UpdateError("no passed parity test for this model; nothing changed")


def prepare(
    config_path: Path,
    *,
    name: str | None = None,
    source: Path | None = None,
    expected_sha256: str | None = None,
    wait: float = 0,
) -> Plan:
    """Read configuration, model and devices; compute every change without writing.

    `source` overrides the configuration's `source` (the integration knows where it
    keeps the model, also when ESPHome sees that folder under another path)."""
    config_path = config_path.expanduser().resolve()
    config = load_config(config_path)
    root = config_path.parent
    slug = config["slug"]
    source = (source or (root / Path(config["source"]).expanduser())).resolve()
    deadline = time.monotonic() + wait
    while True:
        try:
            manifest, model, report, ended = load_model(source, slug)
            digest = hashlib.sha256(model).hexdigest()
            if expected_sha256 and expected_sha256 != digest:
                raise UpdateError("the collector has not received the expected model yet")
            if config.get("require_parity"):
                check_parity(report, source, slug, digest)
            break
        except (UpdateError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    day = (ended or dt.datetime.now(dt.UTC).date().isoformat())[:10]
    name = name or str(config.get("name", "{slug}_{date}")).format(
        slug=slug, date=day.replace("-", ""), hash=digest[:12]
    )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise UpdateError(f"invalid file name {name!r}")
    models = root / config.get("models", "models")
    target_model, target_manifest = models / f"{name}.tflite", models / f"{name}.json"
    if target_model.exists() and target_model.read_bytes() != model:
        raise UpdateError(f"{target_model.name} exists with other content; choose another name")
    manifest["model"] = target_model.name
    plan = Plan(
        root=root,
        config=config,
        source=source,
        digest=digest,
        name=name,
        day=day,
        model=model,
        manifest=manifest,
        report=report,
        target_model=target_model,
        target_manifest=target_manifest,
    )
    factors = config.get("ladder", DEFAULT_LADDER)
    min_cutoff = float(config.get("min_cutoff", 0.5))
    stamp = f"trained {day}"
    for device in config["devices"]:
        path = (root / device["file"]).resolve()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as err:
            raise UpdateError(f"{device['file']}: {err.strerror or err}") from err
        reference = Path(os.path.relpath(target_manifest, path.parent)).as_posix()
        new = set_substitution(text, device["model"], reference, "", device["file"])
        steps = ladder(manifest, report, device.get("ladder", factors), min_cutoff)
        for key, cutoff, note in steps:
            comment = f"p={cutoff:.3f}, {stamp}" + (f", {note}" if note else "")
            new = set_substitution(new, key, f'"{round(cutoff * 255)}"', comment, device["file"])
        plan.devices.append(DevicePlan(device=device, path=path, steps=steps, text=new, changed=new != text))
    return plan


def write(plan: Plan) -> None:
    """Write the model files and the changed device configurations."""
    plan.target_model.parent.mkdir(parents=True, exist_ok=True)
    plan.target_model.write_bytes(plan.model)
    plan.target_manifest.write_text(json.dumps(plan.manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for device in plan.changed:
        device.path.write_text(device.text, encoding="utf-8")


# Rollout progress: runtime data beside the downloaded model, outside version control.


def load_ledger(plan: Plan) -> dict[str, Any]:
    try:
        ledger = json.loads((plan.source / LEDGER).read_text())
    except (OSError, json.JSONDecodeError):
        ledger = {}
    if ledger.get("model_sha256") != plan.digest:
        ledger = {"model_sha256": plan.digest, "devices": {}}
    ledger.setdefault("devices", {})
    return ledger


def write_progress(path: Path, ledger: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(ledger, indent=2) + "\n")
    os.replace(temporary, path)


def fingerprint(plan: Plan, device: DevicePlan, method: Any) -> str:
    """A changed firmware configuration, model or install method needs verification again."""
    return hashlib.sha256(
        device.path.read_bytes() + plan.target_manifest.read_bytes() + json.dumps(method).encode()
    ).hexdigest()


def is_verified(ledger: dict, device: DevicePlan, device_fingerprint: str) -> bool:
    previous = ledger["devices"].get(device.file, {})
    return bool(previous.get("verified")) and previous.get("fingerprint") == device_fingerprint


# Command line -------------------------------------------------------------------


def build_command(build: list[str], device: dict, root: Path) -> list[str]:
    values = {**device, "file": os.path.relpath((root / device["file"]).resolve(), root)}
    try:
        return [part.format(**values) for part in build]
    except KeyError as err:
        raise UpdateError(f"{device['file']}: build placeholder {err} is missing in the device configuration") from err


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

    plan = prepare(args.config, name=args.name, expected_sha256=args.expected_sha256, wait=args.wait)
    root, report = plan.root, plan.report
    print(f"Model: {plan.target_model.relative_to(root)}")
    if report.get("recall") is not None:
        print(
            f"Report: recall {report['recall']:.1%} of {report.get('positives')} recordings, "
            f"{report['false_accepts_per_hour']:.2f} false activations/h "
            f"({report.get('ambient_hours')} h background)"
        )
    for device in plan.devices:
        print(f"  {device.file}: " + ", ".join(f"{key}={round(c * 255)}" for key, c, _ in device.steps))
    if args.dry_run:
        print("Dry run: nothing written.")
        return 0

    write(plan)
    print(f"Written: model and {len(plan.changed)} device configuration(s).")

    build = plan.config.get("build")
    if args.build:
        if not isinstance(build, list) or not build or not all(isinstance(part, str) for part in build):
            raise UpdateError("--build needs a command argument list in 'build'")
        ledger_path = plan.source / LEDGER
        ledger = load_ledger(plan)
        failures = []
        for device in plan.devices:
            command = build_command(build, device.device, root)
            device_fingerprint = fingerprint(plan, device, command)
            if is_verified(ledger, device, device_fingerprint):
                print(f"Already verified: {device.file}")
                continue
            entry = {
                "fingerprint": device_fingerprint,
                "verified": False,
                "started_at": dt.datetime.now(dt.UTC).isoformat(),
            }
            ledger["devices"][device.file] = entry
            write_progress(ledger_path, ledger)
            print(f"Building: {device.file}", flush=True)
            try:
                result = subprocess.run(command, cwd=root, check=False)
                entry.update(verified=result.returncode == 0, exit_code=result.returncode)
            except OSError as err:
                entry["error"] = type(err).__name__
            entry["ended_at"] = dt.datetime.now(dt.UTC).isoformat()
            write_progress(ledger_path, ledger)
            if not entry["verified"]:
                failures.append(device.file)
        if failures:
            raise UpdateError(
                "build or device verification failed: " + ", ".join(failures) + "; rerun --build to retry"
            )
    elif build and plan.changed:
        print("Next, build and install:")
        for device in plan.changed:
            print("  " + " ".join(build_command(build, device.device, root)))
    if args.commit:
        # Include all configured devices on a retry, even if their YAML already matches.
        files = [str(plan.target_model), str(plan.target_manifest), *[str(d.path) for d in plan.devices]]
        subprocess.run(["git", "-C", str(root), "add", "--", *files], check=True)
        diff = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--quiet", "--", *files], check=False)
        if diff.returncode == 1:
            subprocess.run(
                ["git", "-C", str(root), "commit", "-m", f"Wake word model {plan.name}", "--", *files], check=True
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
