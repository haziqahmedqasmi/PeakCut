# Preserve the fork while integrating upstream

User authorization: integrate the recommended upstream changes, preserving the fork, and make YouTube's automatic English-translation fallback optional. The user explicitly asked to proceed with the merge.

Starting fork: 37c755d6a7a714d4e3c6bf50034c26efd66874c9. Upstream: 39c32a878648619e981b57af608ba4571a82247f.

Merge upstream main into an isolated integration branch rather than replaying selected patches. This preserves ancestry for subsequent updates and incorporates the fixes accompanying the recommended features. Optional Arabic support remains opt-in; its language registry must retain the fork's multilingual Portuguese model.

Preserve Codex subscription connection/model/effort, Portuguese UI and clip text, templates, segment editing, censorship, bulk posting, optional caps, intervals and daily posting windows. Reconcile upstream model APIs with Portuguese transcription; preserve source-language detection independently of output caption language. New generated post text must follow the chosen caption language and edited segment sequence. Fixed text must stay fixed and hashtags must work in Portuguese without duplication.

Add a per-account switch, on by default, labelled "Use YouTube's automatically translated English captions". It controls both the fallback to an automatically translated English track when the original transcript is unavailable and the existing English-caption translation helper. Off still permits original-language transcription, human-written English subtitles and AI caption translation. Original-language tracks take precedence in both states. Snapshot the preference into new jobs and use that snapshot in worker threads so one account cannot affect another. Invalid non-boolean values are rejected.

Update the authorized local branch only after passing the complete project suite and an independent review. No push or deployment is requested. Keep .env, storage configuration and user data untouched. Test against temporary data only.
