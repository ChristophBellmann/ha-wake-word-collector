# Services and entities

## Services

| Service | |
| --- | --- |
| `wake_word_collector.start` / `stop` | collection mode; `device` = the satellite's `wake_word_device`, empty = the satellite running the current voice command |
| `wake_word_collector.review` | accept, reject, or mark as not the wake word (`negative`) |
| `wake_word_collector.review_latest_trigger` | judge the newest reported activation (`accept` or `negative`) |
| `wake_word_collector.start_training` / `stop_training` | with a trainer service |
| `wake_word_collector.rollout_model` | put the last model into the ESPHome configurations and install it through the ESPHome Device Builder (`dry_run`: only show the change) |
| `wake_word_collector.import` | take over existing WAV recordings (e.g. from an earlier training setup) as accepted clips or negatives |
| `wake_word_collector.run_speaker_test` | loudspeaker test: play several held-out recordings next to a satellite and count the recognitions (returns the result) |
| `wake_word_collector.speaker_test` | the trainer plays one held-out recording through a loudspeaker (`route`) |
| `wake_word_collector.review_latest` | note on, or reject, the newest usable clip |
| `wake_word_collector.trim` | `keep`, `remove` or `extract` a window (ms) |
| `wake_word_collector.record` | start or stop a microphone recording on a satellite from the GUI |
| `wake_word_collector.extract` / `auto_extract` | cut recognized wake words out of a manual recording; switch automatic extraction |

## Entities

| Entity | |
| --- | --- |
| `sensor.<wake word>_recordings` | usable clips; the other counts and the last upload as attributes |
| `sensor.<wake word>_satellite_command` | the command the satellites listen to |
| `text.<wake word>_announcement` | how recordings are announced (LLM agents) |
| `switch.<wake word>_auto_learn_false_positives` | learn from activations without input |
| `sensor.<wake word>_training`, `sensor.<wake word>_training_progress` | with a trainer service |
| `select.<wake word>_training_profile`, `button.<wake word>_start_training`, `button.<wake word>_stop_training` | with a trainer service |
| `binary_sensor.<wake word>_trainer`, `sensor.<wake word>_model` | training computer reachable; the last model taken over |
| `select.<wake word>_speaker_test_device`, `select.<wake word>_speaker_test_route`, `number.<wake word>_speaker_test_clips`, `button.<wake word>_run_speaker_test`, `sensor.<wake word>_speaker_test` | loudspeaker test |
| `button.<wake word>_rollout_model`, `switch.<wake word>_auto_rollout`, `sensor.<wake word>_rollout` | with a rollout configuration |

Events: `wake_word_collector_model_ready` (a model was taken over from the
trainer), `wake_word_collector_rollout_finished` (a rollout ended).
