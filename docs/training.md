# Training

## Training from Home Assistant

With [Wake Word Trainer](https://github.com/ChristophBellmann/wake-word-trainer)
running as a service on the training computer (`wake-word-trainer serve`),
enter its address and token under *Configure*. You get:

| Entity | |
| --- | --- |
| `sensor.<wake word>_training` | idle, running, completed, failed …; round, step, best recall and false activations per hour as attributes |
| `sensor.<wake word>_training_progress` | percent |
| `select.<wake word>_training_profile`, `button.<wake word>_start_training`, `button.<wake word>_stop_training` | start and stop with a profile of the service |
| `binary_sensor.<wake word>_trainer` | the training computer is reachable |
| `sensor.<wake word>_model` | the last finished model, taken over automatically |

Since Trainer 0.5.0 the service only starts a run when the training computer
has enough free memory, CPU and (after pausing the configured GPU services)
VRAM. Otherwise the start button reports why, for example
`not enough free system resources: only 3.1 GB memory available, need 8 GB`.
During a run the Trainer's status carries a warning when memory runs short;
see the Trainer's `resource_check` setting.

The finished model is served by Home Assistant for the satellites:

```yaml
micro_wake_word:
  models:
    - model: http://homeassistant.local:8123/api/wake_word_collector/model/hey_jarvis/hey_jarvis.json
```

ESPHome downloads it when compiling. The model contains no audio and is
served without login. A notification and the event
`wake_word_collector_model_ready` tell you about a new model.

To put the model into your ESPHome configurations and flash the satellites
from Home Assistant, [roll it out](rollout.md), or turn on
`switch.<wake word>_auto_rollout` to do that after every training.

## Unattended training

For unattended training configure `automatic_training` in the public
Trainer's private `service.yaml`. It checks the night window, desktop
inactivity, CPU/GPU activity and new sample hashes; it stops its own run if
the user returns and restores paused GPU services. With the integration's
automatic rollout the loop from new negative to verified firmware closes in
Home Assistant; the Trainer's own parity-gated `deployment` is the
alternative without it.

## On the command line, or with another pipeline

[Wake Word Trainer](https://github.com/ChristophBellmann/wake-word-trainer)
is made for these recordings: put the collector URL and token into its
project and run it; it fetches the accepted clips, keeps a stable share of
them for an honest evaluation, adds synthetic speech and returns a model for
ESPHome. Clips you reject here disappear from training on the next run.

Any other pipeline can use the export directly:

```bash
curl -H "X-Wakeword-Token: $TOKEN" http://homeassistant.local:8123/api/wake_word_collector/export/hey_jarvis
```

returns the usable clips with an `audio_url` each, downloadable with the same
header. Rejected clips never appear there. `/export/<wake word>/negatives`
lists the clips that are not the wake word, in the same way.
