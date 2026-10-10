# Satellites (ESPHome)

Add to every satellite's configuration, with your IDs:

```yaml
packages:
  wake_word_collection:
    url: https://github.com/ChristophBellmann/ha-wake-word-collector
    ref: main
    files:
      - path: esphome/wake-word-collection.yaml
        vars:
          wake_word_device: kitchen-satellite     # lower case, - and _
          wake_word_url: http://homeassistant.local:8123/api/wake_word_collector/clips/hey_jarvis
          wake_word_token: !secret wake_word_token
          wake_word_command_entity: sensor.hey_jarvis_satellite_command
          wake_word_microphone_id: i2s_mics       # your microphone
          wake_word_microphone_channel: "0"       # the channel micro_wake_word uses
          wake_word_microphone_gain: "1"          # and its gain_factor
          wake_word_va_id: va                     # your voice_assistant
          wake_word_media_player_id: external_media_player
```

The package adds the recorder component, *Start/Stop wake word collection*
buttons and a *Wake word collection* status. The satellite needs no
permission to perform Home Assistant actions: it only reads the command
sensor and uploads over HTTP. If your Home Assistant only speaks HTTPS with a
private CA, add the CA to the satellite or use the local HTTP URL. To pin a
version, set `ref:` above and `wake_word_components:
github://ChristophBellmann/ha-wake-word-collector@<version>`.

Use the same microphone, channel and `gain_factor` as your `micro_wake_word`
configuration. On a satellite whose wake word engine listens on another
channel than the voice assistant (a Satellite1 often uses channel 1 with a
gain for the wake word and channel 0 for speech), the defaults would record a
different signal than the one the model judged: false alarms could not be
explained from the clips, and collected examples would not match what the
model hears.

For reports of every activation (false alarms as training data) see
[False alarms](false-alarms.md).
