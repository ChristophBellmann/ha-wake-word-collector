# Wake Word Collector

Record examples of **your own wake word** with **your own voice satellites**,
in your rooms, with your voices, for training a custom wake word model
(microWakeWord, openWakeWord).

[Deutsch weiter unten](#deutsch)

![Review card: recordings to check, waveform editor](docs/images/review-card.png)

## How it works

1. You say *"record the wake word"* to a satellite.
2. That satellite switches to collection mode. Its voice assistant keeps
   listening; every utterance is recorded from the satellite's own microphone
   (including the moment before listening started) and uploaded to Home
   Assistant together with the speech-to-text result.
3. You say only the wake word, as often as you like, also several times in one
   breath: *"Hey Jarvis, Hey Jarvis"* becomes two separate clips, split at the
   quietest point. The wake word is never split into single words.
4. Home Assistant sorts every clip:

   | Category | When |
   | --- | --- |
   | **usable** (`candidates`) | the transcript is only the wake word or an accepted variant |
   | **to check** (`needs_review`) | anything else: an unusual pronunciation is kept, not lost |
   | command (`control`) | contains a command word such as "done" |
   | rejected (`rejected_quality`) | too quiet, clipped or with DC offset, or rejected by you; kept for recovery |

5. *"I am done"* ends collection mode. *"The last one was bad, a car drove by"*
   rejects the last clip (or just notes the noise).
6. On the **review card** you listen, accept or reject, and trim clips in a
   waveform editor (keep, delete or extract a selection as a new clip).
   Originals are backed up before every edit.
7. [Wake Word Trainer](https://github.com/ChristophBellmann/wake-word-trainer)
   fetches the usable clips through a token-protected export, trains a
   microWakeWord model, measures it on your held-out recordings and exports
   it for `micro_wake_word` on the satellites.

## Requirements

- Home Assistant 2025.4 or newer; a voice pipeline with speech-to-text.
- ESPHome voice satellites with `voice_assistant`, `micro_wake_word`, a media
  player and `http_request` (the usual voice satellites based on ESP32-S3 with
  PSRAM). The microphone must deliver 16 or 48 kHz.

## Installation

HACS → ⋮ → *Custom repositories* →
`https://github.com/ChristophBellmann/ha-wake-word-collector`, category
*Integration*. Install *Wake Word Collector*, restart Home Assistant.

## Setup

### 1. Home Assistant

*Settings → Devices & services → Add integration → Wake Word Collector*:

- **Wake word** as your speech-to-text writes it, e.g. `Hey Jarvis`.
- **Also counts as the wake word**: transcriptions that are fine too, comma
  separated, e.g. `hey jarvas, hi jarvis`. A trailing "n"/"s" on longer words
  ("Jarvisn") is accepted anyway.
- **Command words**: recordings containing them are kept apart.
- **Storage folder**: default `<config>/wake_word_collector/<wake word>`.

The next step shows the **upload URL**, a **token** and the **command sensor**
for the satellites. They are shown again under *Configure*.

### 2. Satellites (ESPHome)

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

### 3. Voice commands

- **LLM conversation agents** (with *Assist* control): nothing to do. The
  integration registers intents the agent can call: start, stop, statistics
  and a review of the last clip.
- **Built-in conversation agent**: import the blueprint
  [voice_commands.yaml](blueprints/automation/wake_word_collector/voice_commands.yaml)
  (*Settings → Automations → Blueprints → Import*) and enter your wake word
  and sentences in your language. It also answers the wake word itself with
  silence while collecting, so the examples do not reach the agent.

### 4. Review card

The card is loaded automatically; add `type: custom:wake-word-collector-card`
to a dashboard. Texts follow your Home Assistant language (English, German).

## Services

| Service | |
| --- | --- |
| `wake_word_collector.start` / `stop` | collection mode; `device` = the satellite's `wake_word_device`, empty = the satellite running the current voice command |
| `wake_word_collector.review` | accept or reject a clip |
| `wake_word_collector.review_latest` | note on, or reject, the newest usable clip |
| `wake_word_collector.trim` | `keep`, `remove` or `extract` a window (ms) |

Entities: `sensor.<wake word>_recordings` (usable clips; attributes with the
other counts and the last upload) and `sensor.<wake word>_satellite_command`.

## Training

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
header. Rejected clips never appear there.

## Privacy

Recordings are voice data of the people in your home. They stay in the
storage folder of your Home Assistant; nothing is sent elsewhere. Keep the
folder out of shared backups, and do not publish models trained on other
people's voices without their consent. Diagnostics contain no audio and no
token.

## Storage layout

```text
<storage>/candidates|needs_review|control|rejected_quality/<device>/ha_<device>_<time>_<hash>.wav
<storage>/manifest.jsonl   one line per clip: transcript, duration, level, quality
<storage>/reviews.jsonl    one line per review
<storage>/trim_backups/
```

Folders of earlier standalone collectors with the same layout can be used as
storage folder directly (`rejected_transcript` counts as "to check").

## Development

```bash
pip install -r requirements_test.txt
pytest && node tests/card.test.cjs
esphome config tests/esphome/satellite.yaml
```

`collect.py` holds the storage and audio logic without Home Assistant imports.

## License

Apache-2.0

---

## Deutsch

Beispiele des **eigenen Aktivierungsworts** mit den **eigenen
Sprachsatelliten** aufnehmen, zum Trainieren eines eigenen Wakeword-Modells.

Ablauf: zum Satelliten „Aktivierungswort aufnehmen“ sagen, dann nur noch das
Aktivierungswort sprechen, beliebig oft, auch mehrmals hintereinander (wird
getrennt), am Ende „ich bin fertig“. Home Assistant sortiert: verwendbar, zu
prüfen, Befehl, verworfen. In der Prüf-Karte anhören, annehmen, verwerfen und
mit der Wellenform zuschneiden. Trainiert wird mit dem
[Wake Word Trainer](https://github.com/ChristophBellmann/wake-word-trainer),
der die angenommenen Aufnahmen direkt aus dem Collector holt.

Einrichtung wie oben: Integration hinzufügen (Aktivierungswort, Varianten,
Befehlswörter wie „fertig, reicht“), das ESPHome-Paket in jeden Satelliten
einbinden, mit einem LLM-Agenten sofort sprachgesteuert, mit dem eingebauten
Agenten über den Blueprint mit deutschen Sätzen. Oberfläche und Karte sind auf
Deutsch und Englisch.
