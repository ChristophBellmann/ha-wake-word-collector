# Deutsch

Beispiele des **eigenen Aktivierungsworts** mit den **eigenen
Sprachsatelliten** aufnehmen, zum Trainieren eines eigenen Wakeword-Modells.

Ablauf: zum Satelliten „Aktivierungswort aufnehmen“ sagen, dann nur noch das
Aktivierungswort sprechen, beliebig oft, auch mehrmals hintereinander (wird
getrennt), am Ende „ich bin fertig“. Home Assistant sortiert: verwendbar, zu
prüfen, Befehl, verworfen. In der Prüf-Karte anhören, annehmen, verwerfen und
mit der Wellenform zuschneiden. Trainiert wird mit dem
[Wake Word Trainer](https://github.com/ChristophBellmann/wake-word-trainer),
der die angenommenen Aufnahmen direkt aus dem Collector holt. Läuft er als
Dienst auf dem Trainingsrechner, startet und verfolgt man das Training aus
Home Assistant, und das fertige Modell liefert Home Assistant direkt an die
Satelliten aus. Mit `wake_word_report_triggers` melden die Satelliten jede
Auslösung; Fehlalarme („Fehlalarm“ sagen oder in der Karte markieren) lernt
das nächste Training zu ignorieren. Der Lautsprechertest misst einen
Satelliten mit echtem Schall (der Trainer spielt zurückgehaltene Aufnahmen
ab, Home Assistant zählt die Erkennungen), und ein neues Modell rollt Home
Assistant samt Empfindlichkeitsstufen in die ESPHome-Konfigurationen aus und
flasht es über den ESPHome Device Builder auf die Satelliten
(`button.<aktivierungswort>_rollout_model`, auf Wunsch automatisch nach jedem
Training; Fortschritt in `sensor.<aktivierungswort>_rollout`). Dasselbe geht
auf der Kommandozeile mit `esphome/model_update.py`. Wie Aufnahmen
angekündigt werden, lässt sich per Sprache ändern
(`text.<aktivierungswort>_announcement`).

Einrichtung wie oben: Integration hinzufügen (Aktivierungswort, Varianten,
Befehlswörter wie „fertig, reicht“), das ESPHome-Paket in jeden Satelliten
einbinden, mit einem LLM-Agenten sofort sprachgesteuert, mit dem eingebauten
Agenten über den Blueprint mit deutschen Sätzen. Oberfläche und Karte sind auf
Deutsch und Englisch.

## Direkte Mikrofonaufnahme aus der GUI

Die Aktion `wake_word_collector.record` startet die Mikrofonaufnahme am gewählten
`device`; derselbe Aufruf stoppt und speichert sie. `wake_word_collector.stop`
beendet sie ebenfalls mit Speichern. Assist und Workstation werden nicht benötigt.
Die Aufnahme läuft bis zum Stopp; längere Sitzungen werden in Abschnitte von
`wake_word_manual_duration` geteilt (Standard 30 Sekunden, bis 120 konfigurierbar).
Alle manuellen Aufnahmen bleiben zum Anhören und Freigeben unter **Zu prüfen**,
auch bei Qualitätswarnungen. **Alle** zeigt zusätzlich verworfene Aufnahmen.
Dafür muss auch das ESPHome-Paket auf demselben Stand sein.

Die Aufnahmekarte aktualisiert sich bei Änderungen automatisch. Optional wählt
`recordings_entity` den Bestandssensor und `device_names` ordnet Gerätekennungen
den eigenen Raumnamen zu.

## Aktivierungswörter automatisch ausschneiden

Neue Mikrofonaufnahmen automatisch schneiden lässt sich in der Karte
an- und ausschalten. **Aktivierungswörter ausschneiden** verarbeitet vorhandene
Aufnahmen oder wiederholt einen fehlgeschlagenen Versuch. Die Workstation
benötigt das Trainer-Extra `segment` und `extraction.enabled: true`. Erkannte
Wörter werden mit etwas Vor- und Nachlauf kopiert; das Original bleibt erhalten.
Alle Ausschnitte erscheinen unter **Zu prüfen** und werden erst nach **Annehmen**
zum Trainingsmaterial. Verarbeitungsstand und Fehler stehen an der Originalaufnahme.

## Modell auf die Satelliten ausrollen

In den Optionen des Eintrags die Rollout-Konfiguration (Format wie
`esphome/model_update.example.yaml`, neben den ESPHome-Konfigurationen) und,
ohne ESPHome-Add-on, die Adresse des ESPHome Device Builders eintragen.
`button.<aktivierungswort>_rollout_model` schreibt das zuletzt übernommene
Modell samt Empfindlichkeitsstufen in die Gerätekonfigurationen und flasht
die Satelliten nacheinander. Ein Gerät gilt erst als fertig, wenn es mit
neuer Firmware wieder in Home Assistant ist; ohne bestandenen Parity-Test
bleibt der Rollout gesperrt. Details (englisch): [Roll out](rollout.md).
