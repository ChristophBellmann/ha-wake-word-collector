"""Constants for Wake Word Collector."""

DOMAIN = "wake_word_collector"

CONF_PHRASE = "phrase"
CONF_VARIANTS = "variants"
CONF_CONTROL = "control_words"
CONF_STORAGE = "storage"
CONF_TOKEN = "token"
CONF_SLUG = "slug"

CONTROL_DEFAULTS = {
    "en": "stop, finished, done, enough, start, recording",
    "de": "fertig, reicht, genug, beenden, aufhören, aufnahme, starte",
}

UPLOAD_URL = "/api/wake_word_collector/clips/{slug}"
AUDIO_URL = "/api/wake_word_collector/audio/{entry_id}/{category}/{device}/{filename}"
EXPORT_URL = "/api/wake_word_collector/export/{slug}/{device}/{filename}"
CARD_URL = "/wake_word_collector/wake-word-collector-card.js"

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"

# The command the ESPHome package listens to: "<action>:<target>:<time>".
# target "*" = the satellite that is running the current voice command.
COMMAND_IDLE = "idle"
