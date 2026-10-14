# Sprachbefehle

LLM-Assist-Agenten mit Assist-Steuerung können registrierte Intents für
Aufnahmestart, Stopp, Statistik, Prüfung der letzten Aufnahme und Aufnahmeansage
verwenden. Die Ansage steht unter `text.<slug>_announcement` und wird dem Agenten
bei jedem Start übergeben.

Für den eingebauten Gesprächsagenten den
[Blueprint](../blueprints/automation/wake_word_collector/voice_commands.yaml)
unter Einstellungen → Automationen → Blueprints importieren. Aktivierungswort
und passende deutsche Sätze eintragen. Im Sammelmodus beantwortet der Blueprint
reine Aktivierungswort-Aufrufe mit Stille, damit Wiederholungen nicht als
normale Assist-Befehle verarbeitet werden.

Beispiele sind „Nimm das Aktivierungswort auf“, „Ich bin fertig“ und
„Die letzte Aufnahme war schlecht“. Ein bestätigter Fehlalarm kann direkt nach
der Auslösung als solcher gemeldet werden. Unklare Clips später in der Karte
anhören; Sprache und niedrige Modellantworten ersetzen keine sauberen Labels.
