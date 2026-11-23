# Upstream integration implementation plan

> For agentic workers: implement the tasks with scoped parallel integration work, then a whole-branch review. Track steps below.

**Goal:** Merge upstream fixes/features without losing the fork and offer a persisted automatic-translation switch.
**Architecture:** Merge upstream main into the isolated audit clone's integration branch, resolve disjoint file groups, add account-scoped transcript preference and job snapshots, validate and fast-forward the user's existing local branch.
**Tech Stack:** Python, FastAPI, plain JavaScript, existing Python test runner.
**Spec:** docs/superpowers/specs/2026-11-23-upstream-integration-design.md

## Global constraints

- Preserve all fifteen fork commits and ancestry; do not overwrite user data or credentials.
- Default automatic YouTube translation on; strict boolean validation; preference belongs to the current account.
- English and Portuguese output remain selectable independently of spoken language.
- No pushes, deployments or model downloads for verification.

## Review focus

- Worker threads use the job's snapshotted translation preference, not a missing/requestless account context.
- Post text uses selected edit segments rather than discarded intervals; fixed text is not translated away.
- Portuguese model discovery and live/remote ASR do not regress into English-only decoding.
- New layouts compose with multi-segment rendering and timed censorship.
- UI labels/help and new controls work in Portuguese and persist failures visibly.

### Task 1: Establish baseline and merge history

Files: existing tracked repository, this plan/spec.
- [x] Run `python tests/run.py` on fork baseline; retain output outside user data.
- [x] Merge pinned upstream with `--no-commit --no-ff` in the isolated clone.
- [x] Resolve file groups without discarding either side's behaviors.

### Task 2: Reconcile ASR and clip processing

Files: studio/config.py, studio/fetch_model.py, studio/transcribe.py, studio/brain.py, studio/jobs.py, studio/live.py, studio/creators.py, studio/render.py, tests/test_captions.py and integration regression tests.
Interfaces: preserve fork keyword arguments including caption_language/multilingual while adopting upstream's lang-aware model registry and post_text API.
- [x] Use existing fork/upstream regression tests; add failing behavioral coverage for Portuguese ASR registry and edited-segment post text before fixes.
- [x] Preserve Portuguese models, localized text, exact fixed descriptions, edited segments and censorship while resolving conflicts.
- [x] Run targeted ASR, caption, rendering, post-text, posting and censorship tests with temporary storage.

### Task 3: Add automatic translation preference and interface

Files: studio/accounts.py, studio/media.py, studio/server.py, studio/jobs.py, web/index.html, web/pt-br.js, tests/test_youtube_translation.py, tests/run.py, README.md.
Interfaces: youtube_words(url, *, allow_translation=True); youtube_english_words(url, *, allow_translation=True); persisted setting youtube_auto_translate=True, copied into jobs.
- [x] Write tests for original track precedence, translated-only fallback on/off, manual English subtitles with switch off, account isolation, invalid types and saved job snapshots.
- [x] Run tests before implementing and confirm failure is the missing behavior.
- [x] Implement helpers, settings API, job plumbing and accessible settings switch/help in both UI languages.
- [x] Adapt all upstream UI additions to the fork's i18n; connect new layouts to templates and new publication text to batch posts.
- [x] Run translation, settings, post-text and relevant existing tests.

### Task 4: Verify, review and integrate locally

- [x] Run complete `python tests/run.py`, Python compilation, JavaScript syntax checks and `git diff --check`.
- [x] Request an independent whole-branch review; address actionable findings with regression tests.
- [x] Commit the merge and compatibility/switch changes, verify fork and upstream SHAs are ancestors.
- [x] Confirm original checkout still matches starting SHA and is clean, then fetch the tested local integration branch and fast-forward codex/codex-subscription.
- [x] Confirm original checkout branch/SHA/cleanliness and report result without pushing.

## Execution record

Baseline suite passed after using UTF-8, canonical temporary paths and the installed FFmpeg. Final complete suite: all tests passed. Node editor behavioral regressions, Python compilation, JavaScript syntax and git whitespace checks passed. Browser verification confirmed switch on/off persistence after reload and Portuguese UI.

Independent review identified spoken/transcript language confusion, obsolete embedded hashtags on remake, stale editor fields after translated renders and stale asynchronous rewrite responses. Each was reproduced and fixed with regression tests. Real external speech accuracy, YouTube responses and social uploads were not exercised; tests used local fixtures, fake model archives and temporary data.

Integration target is the existing local codex/codex-subscription branch. No push or deployment.
