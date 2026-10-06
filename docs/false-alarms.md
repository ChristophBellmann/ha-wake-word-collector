# False alarms

With `wake_word_report_triggers: "true"` in the package vars, every satellite
also reports its wake word activations outside collection mode: the last
seconds before the detection, uploaded after the voice assistant finished.
In the card (view *Activations*) mark each one as *Was the wake word* (more
real examples) or *False alarm*; or simply say *"I did not call you"* /
*"Fehlalarm"* right after a wrong activation (intent for LLM agents, sentences
in the blueprint). False alarms, clips marked *Not the wake word*, and command
recordings without the wake word are exported as negatives: the next
training learns to ignore exactly what woke your satellites up.

## Learn from activations without input

Enable the integration's **Learn from activations without input** switch
(disabled by default). With the matching ESPHome package, a confirmed empty
speech-to-text result (`stt-no-text-recognized`) marks the existing pre-trigger
recording as `trigger_no_input`. The Collector stores it as a negative, with
`auto_negative: true`, and exports it for training without manual review.
Technical errors, disconnected satellites and ordinary unlabelled triggers
continue to wait for review. Speaker-test recordings are excluded from learning.

The trigger clip keeps the last three seconds of the original buffer, including
the sound that caused the activation; it does not record a later empty room.
No command text is sent with these clips. They remain local in the Collector's
private storage and can be reviewed like other negatives. Absence of a command
is a useful learning signal, not proof that the wake word was never said: keep
the trainer's held-out comparison enabled to prevent degraded models from
reaching devices.

The ESPHome package keeps the last seconds of microphone audio in a ring
buffer (`wake_word_max_duration`); on a detection it waits 300 ms for the end
of the wake word, freezes the buffer and uploads it after the voice assistant
finished (at the latest after 20 s), so answering is never delayed.
