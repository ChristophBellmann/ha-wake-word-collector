# Voice commands

- **LLM conversation agents** (with *Assist* control): nothing to do. The
  integration registers intents the agent can call: start, stop, statistics,
  a review of the last clip, and changing how recordings are announced.
  That wish ("announce it like a pirate") is kept in
  `text.<wake word>_announcement` and handed to the agent with every start.
- **Built-in conversation agent**: import the blueprint
  [voice_commands.yaml](../blueprints/automation/wake_word_collector/voice_commands.yaml)
  (*Settings → Automations → Blueprints → Import*) and enter your wake word
  and sentences in your language. It also answers the wake word itself with
  silence while collecting, so the examples do not reach the agent.
