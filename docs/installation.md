# Installation and setup

## Requirements

- Home Assistant 2025.4 or newer; a voice pipeline with speech-to-text.
- ESPHome voice satellites with `voice_assistant`, `micro_wake_word`, a media
  player and `http_request` (the usual voice satellites based on ESP32-S3 with
  PSRAM). The microphone must deliver 16 or 48 kHz.

## Installation

HACS → ⋮ → *Custom repositories* →
`https://github.com/ChristophBellmann/ha-wake-word-collector`, category
*Integration*. Install *Wake Word Collector*, restart Home Assistant.

## Add the integration

*Settings → Devices & services → Add integration → Wake Word Collector*:

- **Wake word** as your speech-to-text writes it, e.g. `Hey Jarvis`.
- **Also counts as the wake word**: transcriptions that are fine too, comma
  separated, e.g. `hey jarvas, hi jarvis`. A trailing "n"/"s" on longer words
  ("Jarvisn") is accepted anyway.
- **Command words**: recordings containing them are kept apart.
- **Storage folder**: default `<config>/wake_word_collector/<wake word>`.

The next step shows the **upload URL**, a **token** and the **command sensor**
for the satellites. They are shown again under *Configure*.

To record from the GUI without a voice command, see
[Review card and GUI recording](review-card.md).

Next: [add the ESPHome package to your satellites](satellites.md).
