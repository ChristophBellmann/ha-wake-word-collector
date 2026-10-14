# Dienste und Entitäten

## Dienste

| Dienst | Zweck |
| --- | --- |
| `wake_word_collector.start` / `stop` | Sammelmodus; `device` ist die Paketkennung, ohne Gerät das des aktuellen Sprachbefehls |
| `wake_word_collector.review` | akzeptieren, ablehnen oder als negativ bestätigen |
| `wake_word_collector.review_latest_trigger` | neueste Auslösung als echt oder negativ bewerten |
| `wake_word_collector.start_training` / `stop_training` | konfiguriertes Trainer-Profil starten oder stoppen |
| `wake_word_collector.rollout_model` | geprüftes Modell über ESPHome ausrollen; `dry_run` zeigt nur den Plan |
| `wake_word_collector.import` | externe WAVs als bestätigte Positive oder Negative übernehmen |
| `wake_word_collector.run_speaker_test` | mehrere Prüfaufnahmen abspielen und reale Erkennungen zählen |
| `wake_word_collector.speaker_test` | eine Prüfaufnahme über eine Trainer-Route wiedergeben |
| `wake_word_collector.review_latest` | neueste verwendbare Aufnahme kommentieren oder ablehnen |
| `wake_word_collector.trim` | Bereich in Millisekunden behalten, entfernen oder ausschneiden |
| `wake_word_collector.record` | Mikrofonaufnahme per GUI starten oder beenden |
| `wake_word_collector.extract` / `auto_extract` | vollständige Aufrufe aus manuellen Aufnahmen ausschneiden oder automatische Extraktion einstellen |

## Entitäten

| Entität | Zweck |
| --- | --- |
| `sensor.<slug>_recordings` | verwendbare Clips und zusätzliche Bestandsattribute |
| `sensor.<slug>_satellite_command` | Gerätesteuerung des Pakets |
| `text.<slug>_announcement` | Aufnahmeansage für LLM-Agenten |
| `switch.<slug>_auto_learn_false_positives` | optionale Negative aus Auslösungen ohne Eingabe |
| `sensor.<slug>_training`, `…_training_progress` | Workstationzustand und Fortschritt |
| `select.<slug>_training_profile`, `button.…_start_training`, `button.…_stop_training` | Profilbedienung |
| `binary_sensor.<slug>_trainer`, `sensor.<slug>_model` | Erreichbarkeit und zuletzt übernommenes Modell |
| `select.…_speaker_test_device`, `select.…_speaker_test_route`, `number.…_speaker_test_clips`, `button.…_run_speaker_test`, `sensor.…_speaker_test` | Lautsprechertest |
| `button.…_rollout_model`, `switch.…_auto_rollout`, `sensor.…_rollout` | separater Modellrollout |

Ereignisse: `wake_word_collector_model_ready` nach Modellübernahme und
`wake_word_collector_rollout_finished` nach einem beendeten Rollout.
Die [Prüfkarte](independent-review.md) verwendet vorhandene Profile und Dienste;
Prüftranskripte kommen aus dem angemeldeten WebSocket, nicht aus Sensorattributen.
