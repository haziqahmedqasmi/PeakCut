# Word Censorship Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let each user configure word censorship for exported captions and audio.
**Architecture:** Validate and persist preferences in accounts; use a small censorship
module at rendering and pass the original speech separately from translated captions.
**Tech Stack:** Existing Python, ffmpeg, plain JavaScript and HTML.
**Spec:** ../specs/2026-11-22-word-censorship-design.md

## Global Constraints

- Follow AGENTS.md: per-user context, sync bumps, short render paths, existing palette.
- No dependencies, automatic re-exports, or changes to original transcripts.
- Run `.venv/Scripts/python.exe tests/run.py` before and after studio changes.

## Review Focus

- Whole-word matching must not censor larger words containing a listed term.
- Case, accents and punctuation must work in Brazilian Portuguese.
- Invalid settings must not overwrite a valid saved configuration.
- Caption translation or disabling must not change speech timing for audio censorship.
- Edited segments must mute at their positions on the assembled timeline.

### Task 1: Settings and matching

Files: studio/accounts.py, studio/censorship.py, tests/test_censorship.py.
Interfaces: normalize_words(value: str) -> str; mask_text(text: str, words: str) -> str;
mute_ranges(speech: list, words: str, start: float, end: float) -> list[tuple].

- [x] Add failing tests for persistence, isolation, validation and whole-word matching.
- [x] Implement validation, matching, masking and merged/clamped mute ranges.
- [x] Run the focused tests and confirm they pass.

### Task 2: Render and settings interface

Files: studio/render.py, studio/jobs.py, web/index.html, web/pt-br.js, tests/run.py.
Interface: render_clip(..., speech_words=None) accepts original timestamped speech.

- [x] Add tests for rendered captions, translated/disabled captions and sequences.
- [x] Apply caption masks and ffmpeg audio filtering without modifying the transcript.
- [x] Add the settings route, mode selector, word list and explicit Save action.
- [x] Translate the new controls and errors into Brazilian Portuguese.
- [x] Register the test, run the full suite, inspect the diff and check JavaScript syntax.

## Verification

- Baseline and final full suites passed with the existing virtual environment and `PYTHONUTF8=1`.
- Nine censorship tests passed, including real ffmpeg exports and audio measurements.
- Browser check: Portuguese settings route opens, saving persists the chosen mode and removes duplicates.
- Dashboard and translation JavaScript syntax checks passed; `git diff --check` passed.
- Independent code review found no actionable issues.
