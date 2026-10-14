# Datenschutz und Speicherung

Aufnahmen enthalten Stimmen der Menschen im Haushalt. Sie bleiben im privaten
HA-Speicher; bei eingerichteter Prüfung oder Extraktion erhält nur die
konfigurierte Workstation eine Kopie. Keine Stimmdaten oder Transkripte in Git
übernehmen. Diagnoseausgaben enthalten weder Audio noch Token.

```text
<storage>/<category>/<device>/ha_<device>_<time>_<hash>.wav
<storage>/manifest.jsonl
<storage>/reviews.jsonl
<storage>/trim_backups/
<storage>/model/
```

Das Manifest hält Herkunft, Dauer, Pegel, Qualität und vorhandenes Transkript
fest. Prüfentscheidungen und Schnittsicherungen bleiben nachvollziehbar.
`model/` enthält das vom Trainer übernommene Modell und Rollout-Fortschritt.
Ein kompatibler vorhandener Speicherordner kann weiterverwendet werden;
`rejected_transcript` zählt zur Prüfung.

Collector- und Trainer-Token bleiben privat und getrennt. Die neuen Review-
Routen sind tokenisiert und nicht cachebar. Analyse-Details gelangen über den
angemeldeten WebSocket zur Karte; neue STT-Texte werden nicht in
Training-Sensorattribute geschrieben. Die Workstation hält private Snapshots,
Hashes und Berichte außerhalb des öffentlichen Repositories.

Wiederherstellung und Sicherungen vor dem Löschen prüfen. Modelle und Quellen
anderer Sprecher nur mit passender Berechtigung weitergeben. Eine öffentliche
Programmanleitung enthält keine privaten Aktivierungswörter oder konkreten
Installationshistorien.
