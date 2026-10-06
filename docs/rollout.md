# Roll out a model to the satellites

Training, review and flashing stay in Home Assistant: the integration puts a
new model into your ESPHome configurations (see below) and installs it on
every satellite through the **ESPHome Device Builder** (the ESPHome add-on or
dashboard), one after the other. A satellite counts as done only when it is
back in Home Assistant with a new firmware compilation time.

1. Put a rollout configuration ([example](../esphome/model_update.example.yaml))
   next to your ESPHome configurations, e.g. `/config/esphome/wake-word-model.yaml`
   with the ESPHome add-on. Device files are relative to it and must be the
   configuration names the Device Builder shows.
2. Under *Configure*, enter it as *Model rollout configuration* (relative to
   the Home Assistant configuration folder, e.g. `esphome/wake-word-model.yaml`).
   *ESPHome Device Builder* may stay empty with the add-on (the one the ESPHome
   integration knows is used); otherwise enter its address, e.g.
   `http://127.0.0.1:6052`. Home Assistant must be able to write the
   configuration folder and the Device Builder must see the same files.
3. Press `button.<wake word>_rollout_model`, or turn on
   `switch.<wake word>_auto_rollout` to roll out every model taken over from
   the trainer.

`sensor.<wake word>_rollout` shows idle, running, completed, failed or
blocked, with the device being built, the step (compiling, installing,
verifying) and the result per device. `wake_word_collector.rollout_model`
does the same as the button; with `dry_run: true` it only returns the new
sensitivity steps per device. With `require_parity: true` a model without a
passed trainer comparison is *blocked* and nothing is written. A failed
device does not stop the others; rolling out again retries only the devices
not verified for this model (progress in `<storage>/model/rollout.json`). The
event `wake_word_collector_rollout_finished` and a notification report the
result. Commit the changed configurations yourself if you keep them in git.

## How a rollout runs

1. **Check and plan.** The rollout configuration is read, the model taken
   over from the trainer is loaded from the entry's storage folder
   (`<storage>/model/`) and, with `require_parity: true`, its trainer
   comparison is checked against the model's SHA256. The new file name,
   the model substitution and the sensitivity steps of every device are
   computed. Nothing is written yet; `dry_run: true` stops here and returns
   this plan.
2. **Write.** The model is copied to `<models>/<name>.tflite` and `.json`,
   and the changed device configurations are written.
3. **Build and install,** device by device: compile in the Device Builder,
   install over the air to the device's `address` (or its mDNS name). Devices
   already verified for exactly this model, configuration and address are
   skipped.
4. **Verify.** The device must reconnect to Home Assistant with a firmware
   compilation time different from the one before the install (at most five
   minutes). A device without an ESPHome entry in Home Assistant counts as
   installed, not checked.
5. **Report.** `sensor.<wake word>_rollout`, the event
   `wake_word_collector_rollout_finished` and a notification carry the result.

## ESPHome Device Builder in Docker

With the ESPHome add-on there is nothing to set up. A Device Builder in its
own container needs:

- The same configuration folder Home Assistant writes to, ideally under the
  same path in both containers, so the device paths in the rollout
  configuration mean the same file everywhere.
- Its own build folder (mount `/build`), unless only this container ever
  builds these configurations: ESP-IDF build folders of another ESPHome
  installation (e.g. a Python virtualenv on the host) cannot be shared.
- All secrets the device configurations use in its `secrets.yaml`.

```yaml
services:
  esphome:
    image: ghcr.io/esphome/esphome:2026.9.1
    network_mode: host
    user: "1000:1000"
    environment:
      - HOME=/cache/home
    command: [dashboard, --host, 127.0.0.1, --port, "6052", /srv/esphome]
    volumes:
      - ./esphome:/srv/esphome
      - ./esphome/.esphome:/cache
      - ./esphome/.esphome/docker-build:/build
```

ESPHome 2026.9 and newer may start a *version history* that creates a git
repository in the configuration folder; turn it off in the Device Builder's
settings if that folder is part of another repository.

## Troubleshooting

| Result per device | Meaning |
| --- | --- |
| `not in the ESPHome Device Builder` | the file is not among the Device Builder's configurations: device paths must be relative to the rollout configuration, which must lie in the Device Builder's folder |
| `compile failed` / `install failed` | the last lines of the build log are in `<storage>/model/rollout.json` (also when a compile takes longer than 60 minutes or an install longer than 15 minutes) |
| `ESPHome Device Builder not reachable` | address in the options, or the add-on is not running |
| `not verified` | the install reported success, but the device did not come back with new firmware within five minutes |

*Blocked* means nothing was written: usually no passed parity test for this
model. A restart of Home Assistant during a rollout stops it; roll out again
to continue with the devices not yet verified.

## On the command line

If your satellites keep their models as files next to their configurations
(and set their sensitivity steps with `set_probability_cutoff`),
[esphome/model_update.py](../esphome/model_update.py) (the same code the
integration uses for its rollout) does the whole switch,
driven by a small YAML file
([example](../esphome/model_update.example.yaml)): it copies the model, sets the
model substitution of every listed device, derives the sensitivity steps from
the trainer's evaluation (the chosen cutoff, and the most sensitive cutoffs
with at most 2x and 5x as many false activations per hour), and with
`--build` runs your build command for every device not yet verified for this model.
The command must return success only after verifying the running firmware.
Progress is kept in `source/rollout.json`; rerunning `--build` retries failed
devices and skips successful ones. A failure does not prevent the other devices
from updating. `--commit` commits only after all requested builds succeed.

Set `require_parity: true` to accept only a model with a passed trainer
comparison for its SHA256. `{hash}` in `name` avoids collisions between runs
on the same day. For automatic deployment from the trainer use
`--expected-sha256 {sha256} --wait 120 --build`: the tool waits for the
collector to receive that exact model and its comparison. Keep your device
paths, flash command and credentials in your local configuration.

```sh
python3 model_update.py --config wake-word-model.yaml --dry-run
python3 model_update.py --config wake-word-model.yaml --build
```

Run it with the Python that has ESPHome installed (it needs PyYAML).
