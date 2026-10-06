# Development

```bash
pip install -r requirements_test.txt
pytest && node tests/card.test.cjs
esphome config tests/esphome/satellite.yaml
```

`collect.py` holds the storage and audio logic without Home Assistant imports.

`model_update.py` (inside the integration, also the command line tool) holds
the rollout planning without Home Assistant imports; `rollout.py` drives the
ESPHome Device Builder.
