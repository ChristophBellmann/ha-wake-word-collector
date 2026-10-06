"""Constants for Wake Word Collector."""

DOMAIN = "wake_word_collector"

CONF_PHRASE = "phrase"
CONF_VARIANTS = "variants"
CONF_CONTROL = "control_words"
CONF_STORAGE = "storage"
CONF_TOKEN = "token"
CONF_SLUG = "slug"
CONF_TRAINER_URL = "trainer_url"
CONF_TRAINER_TOKEN = "trainer_token"
# Model rollout: model_update configuration and ESPHome Device Builder.
CONF_ROLLOUT_CONFIG = "rollout_config"
CONF_ESPHOME_URL = "esphome_url"

CONTROL_DEFAULTS = {
    "en": "stop, finished, done, enough, start, recording",
    "de": "fertig, reicht, genug, beenden, aufhören, aufnahme, starte",
}

UPLOAD_URL = "/api/wake_word_collector/clips/{slug}"
AUDIO_URL = "/api/wake_word_collector/audio/{entry_id}/{category}/{device}/{filename}"
EXPORT_URL = "/api/wake_word_collector/export/{slug}/{device}/{filename}"
NEGATIVES_URL = "/api/wake_word_collector/export/{slug}/negatives"
NEGATIVE_AUDIO_URL = "/api/wake_word_collector/export/{slug}/negatives/{category}/{device}/{filename}"
MODEL_URL = "/api/wake_word_collector/model/{slug}/{filename}"
CARD_URL = "/wake_word_collector/wake-word-collector-card.js"

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"

# The command the ESPHome package listens to: "<action>:<target>:<time>".
# target "*" = the satellite that is running the current voice command.
COMMAND_IDLE = "idle"
