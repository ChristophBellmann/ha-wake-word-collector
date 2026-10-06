# Privacy and storage

Recordings are voice data of the people in your home. They stay in the
storage folder of your Home Assistant; manual recordings are sent only to your configured workstation when extraction is enabled. Keep the
folder out of shared backups, and do not publish models trained on other
people's voices without their consent. Diagnostics contain no audio and no
token.

## Storage layout

```text
<storage>/candidates|needs_review|control|rejected_quality|negatives|triggers/<device>/ha_<device>_<time>_<hash>.wav
<storage>/manifest.jsonl   one line per clip: transcript, duration, level, quality
<storage>/reviews.jsonl    one line per review
<storage>/trim_backups/
```

`model/` holds the model taken over from the trainer. Folders of earlier standalone collectors with the same layout can be used as
storage folder directly (`rejected_transcript` counts as "to check").
