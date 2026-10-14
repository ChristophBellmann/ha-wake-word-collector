# Satelliten mit ESPHome

Das Paket in jedes Gerät einbinden, dessen reale IDs verwenden:

```yaml
packages:
  wake_word_collection:
    url: https://github.com/ChristophBellmann/ha-wake-word-collector
    ref: main
    files:
      - path: esphome/wake-word-collection.yaml
        vars:
          wake_word_device: kitchen-satellite
          wake_word_url: http://homeassistant.local:8123/api/wake_word_collector/clips/my_word
          wake_word_token: !secret wake_word_token
          wake_word_command_entity: sensor.my_word_satellite_command
          wake_word_microphone_id: i2s_mics
          wake_word_microphone_channel: "0"
          wake_word_microphone_gain: "1"
          wake_word_va_id: va
          wake_word_media_player_id: external_media_player
```

Gerätekennungen sind klein geschrieben und erlauben Bindestrich sowie
Unterstrich. Das Paket liefert Aufnahme-Start/-Stopp, Sammelstatus und
Auslösungszähler. Das Gerät liest den Befehlssensor und lädt per HTTP hoch;
es braucht dafür keine Berechtigung zum Ausführen von HA-Aktionen.

Mikrofon, Kanal und Gain müssen genau dem Wakeword-Pfad entsprechen.
Assist kann einen anderen Kanal oder Filter verwenden. Wird stattdessen
Assist-Audio aufgenommen, passt der Clip nicht zum Signal, auf das das
Wakeword-Modell reagiert hat. Das erschwert Fehlalarmprüfung und Training.

Private HTTPS-Zertifikate müssen auf dem Gerät vertraut sein; alternativ die
lokale HTTP-Adresse verwenden. Für eine feste Version sowohl `ref` als auch
`wake_word_components: github://ChristophBellmann/ha-wake-word-collector@<version>`
auf denselben Stand setzen.

`wake_word_report_triggers: "true"` aktiviert Aufnahmen unmittelbar vor
Auslösungen außerhalb des Sammelmodus. [Fehlalarme](false-alarms.md) erklären
Puffer, Upload und Prüfung. Das Modell wird erst durch einen separat geprüften
[Rollout](rollout.md) ersetzt.
