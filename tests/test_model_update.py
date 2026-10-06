"""esphome/model_update.py: a trained model into ESPHome configurations."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("model_update", Path(__file__).parents[1] / "esphome" / "model_update.py")
model_update = importlib.util.module_from_spec(SPEC)
sys.modules["model_update"] = model_update  # dataclasses look up their module
SPEC.loader.exec_module(model_update)

DEVICE = """substitutions:
  name: kitchen
  wake_word_model_file: models/old.json
  wake_word_slight_cutoff_uint8: "246"    # old
  # explanation stays
  wake_word_moderate_cutoff_uint8: "217"
  wake_word_very_cutoff_uint8: "190"
micro_wake_word:
  models:
    - model: ${wake_word_model_file}
"""


@pytest.fixture
def setup(tmp_path: Path) -> Path:
    esphome = tmp_path / "esphome"
    (esphome / "sub").mkdir(parents=True)
    (esphome / "kitchen.yaml").write_text(DEVICE)
    (esphome / "sub" / "hall.yaml").write_text(DEVICE.replace("wake_word_model_file", "momo_model"))
    source = tmp_path / "wake_word_collector" / "hey_nova" / "model"
    source.mkdir(parents=True)
    (source / "hey_nova.tflite").write_bytes(b"TFL3")
    (source / "hey_nova.json").write_text(
        json.dumps({"type": "micro", "model": "hey_nova.tflite", "micro": {"probability_cutoff": 0.8}})
    )
    curve = [{"cutoff": i / 255, "recall": 1 - i / 300, "faph": max(0, (200 - i) / 10)} for i in range(0, 256, 5)]
    report = {
        "probability_cutoff": 195 / 255,
        "recall": 0.93,
        "false_accepts_per_hour": 0.5,
        "max_false_accepts_per_hour": 0.5,
        "curve": curve,
    }
    (source / "source.json").write_text(json.dumps({"ended_at": "2026-10-05T03:00:00+00:00", "report": report}))
    marker = tmp_path / "built.txt"
    config = {
        "slug": "hey_nova",
        "source": "../wake_word_collector/hey_nova/model",
        "devices": [
            {"file": "kitchen.yaml", "model": "wake_word_model_file"},
            {
                "file": "sub/hall.yaml",
                "model": "momo_model",
                "ladder": {"wake_word_slight_cutoff_uint8": 1, "wake_word_very_cutoff_uint8": 2},
            },
        ],
        "build": [sys.executable, "-c", f"open({str(marker)!r}, 'a').write('{{file}}\\n')"],
    }
    (esphome / "wake-word-model.yaml").write_text(json.dumps(config))  # JSON is YAML
    return esphome


def test_dry_run_changes_nothing(setup: Path, capsys) -> None:
    assert model_update.run(["--config", str(setup / "wake-word-model.yaml"), "--dry-run"]) == 0
    assert "kitchen.yaml: wake_word_slight_cutoff_uint8=195" in capsys.readouterr().out
    assert (setup / "kitchen.yaml").read_text() == DEVICE
    assert not (setup / "models").exists()


def test_update_and_build(setup: Path) -> None:
    assert model_update.run(["--config", str(setup / "wake-word-model.yaml"), "--build"]) == 0
    manifest = json.loads((setup / "models" / "hey_nova_20261005.json").read_text())
    assert manifest["model"] == "hey_nova_20261005.tflite"
    assert (setup / "models" / "hey_nova_20261005.tflite").read_bytes() == b"TFL3"
    kitchen = (setup / "kitchen.yaml").read_text()
    assert "wake_word_model_file: models/hey_nova_20261005.json\n" in kitchen
    # The chosen cutoff (budget), then at most 2x and 5x the false activations.
    assert 'wake_word_slight_cutoff_uint8: "195"  # p=0.765, trained 2026-10-05, recall 93%' in kitchen
    assert 'wake_word_moderate_cutoff_uint8: "190"' in kitchen
    assert 'wake_word_very_cutoff_uint8: "175"' in kitchen
    assert "# explanation stays" in kitchen
    hall = (setup / "sub" / "hall.yaml").read_text()
    assert "momo_model: ../models/hey_nova_20261005.json" in hall
    assert 'wake_word_very_cutoff_uint8: "190"' in hall  # its own ladder: 2x
    assert 'wake_word_moderate_cutoff_uint8: "217"' in hall  # not in its ladder: untouched
    built = (setup.parent / "built.txt").read_text().split()
    assert built == ["kitchen.yaml", "sub/hall.yaml"]


def test_errors(setup: Path, tmp_path: Path) -> None:
    config = setup / "wake-word-model.yaml"
    (setup / "kitchen.yaml").write_text(DEVICE.replace('  wake_word_very_cutoff_uint8: "190"\n', ""))
    with pytest.raises(model_update.UpdateError, match="wake_word_very_cutoff_uint8' found 0 times"):
        model_update.run(["--config", str(config)])
    (setup / "kitchen.yaml").write_text(DEVICE)
    model_update.run(["--config", str(config)])
    (setup.parent / "wake_word_collector" / "hey_nova" / "model" / "hey_nova.tflite").write_bytes(b"other")
    with pytest.raises(model_update.UpdateError, match="other content"):
        model_update.run(["--config", str(config)])
    (tmp_path / "bad.yaml").write_text("slug: x\n")
    with pytest.raises(model_update.UpdateError, match="'source' is missing"):
        model_update.run(["--config", str(tmp_path / "bad.yaml")])


def test_parity_blocks_unchecked_or_different_model(setup: Path) -> None:
    import hashlib

    config_path = setup / "wake-word-model.yaml"
    config = json.loads(config_path.read_text())
    config["require_parity"] = True
    config["name"] = "{slug}_{date}_{hash}"
    config_path.write_text(json.dumps(config))
    info = setup.parent / "wake_word_collector/hey_nova/model/source.json"
    source = json.loads(info.read_text())
    for parity in ({}, {"passed": False}, {"passed": True, "candidate_sha256": "other"}):
        source["report"]["parity"] = parity
        info.write_text(json.dumps(source))
        with pytest.raises(model_update.UpdateError, match="parity"):
            model_update.run(["--config", str(config_path), "--build"])
        assert (setup / "kitchen.yaml").read_text() == DEVICE
        assert not (setup / "models").exists()
    digest = hashlib.sha256(b"TFL3").hexdigest()
    source["report"]["parity"] = {
        "passed": True,
        "candidate_sha256": digest,
        "candidate_manifest_sha256": hashlib.sha256((info.parent / "hey_nova.json").read_bytes()).hexdigest(),
    }
    info.write_text(json.dumps(source))
    with pytest.raises(model_update.UpdateError, match="expected model"):
        model_update.run(["--config", str(config_path), "--expected-sha256", "wrong"])
    assert model_update.run(["--config", str(config_path), "--expected-sha256", digest, "--build"]) == 0
    assert (setup / "models" / f"hey_nova_20261005_{digest[:12]}.tflite").exists()


def test_partial_failure_retries_only_failed_device(setup: Path, monkeypatch) -> None:
    config_path = setup / "wake-word-model.yaml"
    calls = []

    def build(command, **kwargs):
        from types import SimpleNamespace

        calls.append(command[-1])
        return SimpleNamespace(returncode=int("sub/hall.yaml" in command[-1] and len(calls) == 2))

    monkeypatch.setattr(model_update.subprocess, "run", build)
    with pytest.raises(model_update.UpdateError, match="hall.yaml"):
        model_update.run(["--config", str(config_path), "--build"])
    assert len(calls) == 2
    assert model_update.run(["--config", str(config_path), "--build"]) == 0
    assert len(calls) == 3
    assert "sub/hall.yaml" in calls[-1]
    assert model_update.run(["--config", str(config_path), "--build"]) == 0
    assert len(calls) == 3
    # A firmware change must invalidate the successful verification.
    with (setup / "kitchen.yaml").open("a") as stream:
        stream.write("\n# changed firmware\n")
    model_update.run(["--config", str(config_path), "--build"])
    assert len(calls) == 4
    assert "kitchen.yaml" in calls[-1]


def test_device_address_in_build_command(setup: Path):
    command = model_update.build_command(
        ["flash", "{file}", "{address}"], {"file": "kitchen.yaml", "address": "192.0.2.10"}, setup
    )
    assert command == ["flash", "kitchen.yaml", "192.0.2.10"]
    with pytest.raises(model_update.UpdateError, match="placeholder"):
        model_update.build_command(["flash", "{address}"], {"file": "kitchen.yaml"}, setup)
