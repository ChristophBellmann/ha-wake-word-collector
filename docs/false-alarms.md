# Fehlalarme

Auslösungen während Gesprächen, TV oder Geräteansagen zunächst als Prüfclips
behalten. Echte vollständige Aufrufe positiv freigeben; bestätigte Sprache oder
Geräusche ohne Aufruf negativ markieren. Unklare Clips bleiben zur Prüfung.
[Unabhängige STT und Teacher-Prüfung](independent-review.md) ergänzen das Anhören.
Leere STT beweist gerade bei gefilterten Mikrofonkanälen keinen Fehlalarm.

Mit `wake_word_report_triggers: "true"` im Satellitenpaket werden auch außerhalb
des Sammelmodus die letzten Sekunden vor einer Aktivierung erfasst. Der Puffer
wird 300 ms nach Auslösung eingefroren und nach Ende von Assist hochgeladen,
spätestens nach 20 Sekunden. Das verzögert die normale Antwort nicht. Der Clip
enthält den auslösenden Ton, keinen nachträglich aufgenommenen leeren Raum.

In der Ansicht Auslösungen **War das Aktivierungswort** oder **Fehlalarm** wählen.
Eine Sprachmeldung unmittelbar nach einer falschen Auslösung kann ebenfalls
als Bestätigung dienen. Bestätigte Negative sind beim nächsten Training eine
eigene Satellitenquelle; ein neues Modell muss trotzdem Regression bestehen.

## Auslösungen ohne Eingabe

Die Option zum Lernen aus Auslösungen mit leerer Eingabe ist standardmäßig aus.
Das passende Paket meldet `trigger_no_input` nur bei bestätigtem leerem
Assist-STT (`stt-no-text-recognized`). Bei aktivierter Option wird der vorhandene
Puffer als negativ mit `auto_negative: true` gespeichert. Technische Fehler,
Verbindungsabbrüche und normale ungeprüfte Auslösungen bleiben zur Prüfung.
Lautsprechertests sind vom Lernen ausgeschlossen.

Kein nachfolgender Befehl ist ein Signal, aber kein Beweis für fehlendes
Aktivierungswort. Die optionale Funktion bleibt getrennt von der stärkeren
Workstation-Prüfung; der gemeinsame Prüfbestand verhindert schlechtere
Modellfreigaben.
