# Entwicklung

```bash
pip install -r requirements_test.txt ruff
ruff check .
ruff format --check .
pytest -q
node tests/card.test.cjs
esphome config tests/esphome/satellite.yaml
```

Die HA-Tests benötigen eine zur jeweiligen Test-HA-Version passende Python-
Version und zusätzliche Anforderungen des Gesprächsmoduls für den Blueprint-
Test. Die CI liest diese aus dessen Manifest. Tests verwenden künstliches
Audio; echte Stimmdaten werden nicht eingecheckt.

`collect.py` enthält Speicher-, Hash- und Audiologik ohne HA-Importe.
`views.py` stellt die begrenzten Export-/Prüfrouten bereit. `trainer.py`
koordiniert Workstationstatus und private Detailberichte. Die Kartenprüfung
läuft unabhängig von HA in Node und prüft unter anderem Maskierung von Text,
Abspielen, Schnittbedienung und Aktualisierung.

`model_update.py` enthält die Rollout-Planung auch für die Kommandozeile;
`rollout.py` steuert den ESPHome Device Builder. Firmware-Prüfung und
Modellfreigabe bleiben getrennt von Training und Audioanalyse.

Fachliche Änderungen im selben Arbeitsgang unter `docs/` und in `SUMMARY.md`
erklären. Beide GitBook-Konfigurationen müssen auf diesen Ordner zeigen.
Öffentliche Texte auf Deutsch und ohne persönliche Projekthistorien halten.
