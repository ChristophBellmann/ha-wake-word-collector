# Loudspeaker test

Measures a satellite with real sound, without anyone speaking: the trainer
plays held-out recordings through a loudspeaker next to the satellite
(routes in the service's `speaker_test.routes`), and the integration counts
the recognitions. Choose satellite, route and number of recordings with
`select.<wake word>_speaker_test_device`, `select.<wake word>_speaker_test_route`
and `number.<wake word>_speaker_test_clips`, press
`button.<wake word>_run_speaker_test`; the result (percent, played,
recognized) is `sensor.<wake word>_speaker_test`. The route is remembered per
satellite.

A recognition is seen when the satellite's *Wake word detections* sensor
(part of the ESPHome package) goes up or its assist satellite entity leaves
*idle*. The integration finds both through the ESPHome node name the
firmware sends with every upload; set `satellite` in the service call if it
cannot. Without them, the activation report (`wake_word_report_triggers`)
counts, which takes up to 30 s per recording. Reports of played-back
recordings are not stored: they are evaluation clips, not new examples.

Each recognition normally starts a real conversation, and the test waits
until the satellite is idle again. If a satellite has a switch that only
counts detections without starting the voice assistant, pass it once as
`test_switch` to `run_speaker_test`: it is remembered for that satellite,
turned on for every test and always off again afterwards.
