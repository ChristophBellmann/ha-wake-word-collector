# Review card and recording from the GUI

The card is loaded automatically; add `type: custom:wake-word-collector-card`
to a dashboard. Texts follow your Home Assistant language (English, German).

In the card you listen, accept or reject, and trim clips in a waveform editor
(keep, delete or extract a selection as a new clip). Originals are backed up
before every edit.

## Direct microphone recording

For a GUI microphone test without Assist or the training computer, call
`wake_word_collector.record` with the satellite's `device` identifier: once to
start, again to stop and save; `wake_word_collector.stop` also saves.
Recording continues until stopped. Long sessions are split into segments of
`wake_word_manual_duration` (default 30 seconds, configurable up to 120
seconds), preserving the beginning of each segment. The clip has no transcript
until optional workstation recognition runs: listen under **To check** and
accept extracted examples manually. Quality warnings remain visible and clips
wait for manual acceptance. This requires the matching ESPHome package.

The card updates when recordings change; optional `recordings_entity` selects
the statistics sensor and `device_names` maps device identifiers to room names.

## Automatic extraction from manual recordings

With an updated Wake Word Trainer and its optional `segment` extra installed,
enable `extraction.enabled: true` in the trainer service configuration. New
manual microphone recordings are then processed in the background: complete
configured wake-word phrases and variants become individual clips under
**To check**. The original recording is kept byte for byte. No clip is accepted
for training automatically. Listen and accept only suitable examples.

The card offers **Automatically extract new microphone recordings** (persisted
per collector) and **Extract wake words** on saved manual clips, also for older
recordings and retries. Progress, no matches and workstation errors appear on
the original. Interrupted work resumes on integration reload; unavailable
workstations leave the original playable and a retry button. Successfully
processed originals reuse their clips on retries instead of duplicating them.
The `extract` and `auto_extract` services expose the same actions.

Phrase and variants come from the Collector configuration; model, language,
padding and confidence are workstation configuration. Recognition does not use
the satellite's wake-word model and does not require it to detect your examples.
Allow roughly one second between repetitions. Recognition can miss difficult
pronunciations; manual editing remains available. Long sessions retain the
ESPHome package's segment boundaries and short upload pauses.
