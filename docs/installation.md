# Installation und Einrichtung

Benötigt werden Home Assistant ab 2025.4, eine Assist-Spracherkennung und
ESPHome-Satelliten mit `voice_assistant`, `micro_wake_word`, Mediaplayer und
`http_request`. Übliche ESP32-S3-Geräte benötigen PSRAM; Mikrofonaudio muss
16 oder 48 kHz liefern.

HACS → Menü → Benutzerdefinierte Repositories:
`https://github.com/ChristophBellmann/ha-wake-word-collector`, Kategorie
Integration. Wake Word Collector installieren, Konfiguration prüfen und
Home Assistant neu starten.

Unter Einstellungen → Geräte & Dienste die Integration hinzufügen:

| Einstellung | Bedeutung |
| --- | --- |
| Aktivierungswort | vollständige Phrase entsprechend der gewünschten STT-Schreibweise |
| Zusätzliche Varianten | zulässige andere Transkriptionen, durch Komma getrennt |
| Steuerwörter | Aufnahmebefehle, die getrennt gespeichert werden |
| Speicherordner | standardmäßig `<config>/wake_word_collector/<slug>` |

Der nächste Schritt zeigt Upload-URL, privaten Collector-Token und
Befehlssensor für die Satelliten. Diese Angaben bleiben unter Konfigurieren
verfügbar. [Satellitenpaket](satellites.md) mit passenden Mikrofon-IDs,
Kanal und Verstärkung einbinden.

Für STT-/Teacher-Prüfung und Training zusätzlich den öffentlichen
[Wake Word Trainer](https://github.com/ChristophBellmann/wake-word-trainer)
auf der Workstation einrichten. Adresse und separaten Trainer-Token in den
Collector-Optionen hinterlegen. [Unabhängige Prüfung](independent-review.md)
beschreibt das Prüfprofil und den Knopf in der Karte.
