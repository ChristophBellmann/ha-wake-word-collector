# Wake Word Collector

Record examples of **your own wake word** with **your own voice satellites**,
in your rooms, with your voices, train a custom wake word model
(microWakeWord, openWakeWord) and roll it out to the satellites, all from
Home Assistant.

![Review card: recordings to check, waveform editor](images/review-card.png)

The way through:

1. [Install](installation.md) the integration and add the
   [ESPHome package](satellites.md) to every satellite.
2. Record by [voice](voice-commands.md) or from the GUI, check the clips on the
   [review card](review-card.md), and let [false alarms](false-alarms.md)
   become training data.
3. [Train](training.md) with the Wake Word Trainer service, started and
   followed in Home Assistant.
4. [Roll out](rollout.md) the new model: Home Assistant writes it into your
   ESPHome configurations and installs it on every satellite through the
   ESPHome Device Builder.
5. Measure a satellite with the [loudspeaker test](loudspeaker-test.md).

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
   | not the wake word (`negatives`) | marked by you: the model learns to ignore it |
   | activation (`triggers`) | optional: what a satellite heard right before it woke up, to judge |

5. *"I am done"* ends collection mode. *"The last one was bad, a car drove by"*
   rejects the last clip (or just notes the noise).
6. On the **review card** you listen, accept or reject, and trim clips in a
   waveform editor (keep, delete or extract a selection as a new clip).
   Originals are backed up before every edit.
7. [Wake Word Trainer](https://github.com/ChristophBellmann/wake-word-trainer)
   fetches the usable clips through a token-protected export, trains a
   microWakeWord model, measures it on your held-out recordings and exports
   it for `micro_wake_word` on the satellites.

Deutsch: [Überblick auf Deutsch](deutsch.md).
