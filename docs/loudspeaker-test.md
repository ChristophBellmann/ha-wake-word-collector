# Lautsprechertest

Der Trainer spielt getrennte Prüfaufnahmen über einen Lautsprecher neben dem
Satelliten ab. Die Integration zählt reale Erkennungen. Mit
`select.<slug>_speaker_test_device`, `select.<slug>_speaker_test_route` und
`number.<slug>_speaker_test_clips` Gerät, Route und Anzahl wählen;
`button.<slug>_run_speaker_test` startet. Der Sensor zeigt Anzahl, Treffer und
Prozent. Routen bleiben je Satellit gespeichert.

```mermaid
flowchart LR
  P[Private Prüfaufnahme] --> L[Workstation-Lautsprecher]
  L --> M[Raum und echtes Satellitenmikrofon]
  M --> W[Wakeword-Erkennung]
  W --> H[HA-Zähler und Ergebnis]
```

Erkennung wird über den Auslösungszähler des Pakets oder den Übergang des
Assist-Satelliten aus `idle` erkannt. Der ESPHome-Knotenname aus dem Upload
ordnet das Gerät zu; andernfalls `satellite` im Dienstaufruf angeben. Ohne
diese Entitäten kann ein Auslösungsbericht zählen; dann bis zu 30 Sekunden
pro Aufnahme einplanen. Wiedergabeclips werden nicht neu gesammelt oder trainiert.

Eine normale Erkennung beginnt eine echte Assist-Sitzung. Der Test wartet auf
erneutes `idle`. Ein vorhandener Geräteschalter, der nur zählt und Assist
unterdrückt, kann einmal als `test_switch` angegeben werden; die Integration
merkt ihn und schaltet ihn nach jedem Test wieder aus.

Für Modellvergleiche das Audio einmal aufnehmen und danach offline wiederholt
bewerten. Unterschiedliche Live-Audios vermischen Modellunterschiede mit der
Streuung der Übertragungsstrecke.
