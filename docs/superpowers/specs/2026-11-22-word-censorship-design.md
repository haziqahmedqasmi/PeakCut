# Word censorship

Add a Censorship section to Settings, using the existing form styles and palette.
Each user saves a list of individual words and chooses Off, Captions, Audio, or Both.
Off is the default. Separate words with commas or new lines. Match whole words,
ignoring case and accents; keep punctuation. Replace matching caption and opening
text words with asterisks. Silence matching speech using the original transcript's
timestamps, even when captions are disabled or translated. Support edited sequences.
These are implementation choices; the request was for a word censorship setting.

## Approaches

Caption-only replacement is small but leaves the spoken word audible. Changing the
transcript in place loses the original and can change AI results. Apply censorship
at rendering instead, keeping the original transcript and adding optional audio
filtering. Reuse per-user settings and their existing sync notifications.

Apply the current settings when a clip is rendered or remade; existing exports
stay as they are. Do not add a default profanity list, AI calls, or dependencies.
Audio timing follows speech recognition, so it is not guaranteed to catch speech
that was missed or incorrectly transcribed.
