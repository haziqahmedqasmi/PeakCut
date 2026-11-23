# Posting window implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Let each creator set daily start and end times and spread their daily account limit across that window.

**Architecture:** Keep settings in creator.json and scheduling in studio/posting.py. An optional same-day window uses the existing server local time zone. With a daily limit, schedule at start + index × window length / limit, excluding the end time. Without a daily limit, keep the existing interval inside the window. Recheck the window before sending overdue posts and apply changed settings to waiting posts. Explicit Post now continues to bypass the queue rules.

**Tech stack:** Python, FastAPI, plain HTML/JavaScript, existing unittest suite. No new dependencies.

**Spec:** User request and accepted recommendation in this chat (2026-11-22).

## Constraints and decisions

- Preserve current settings and behavior by default; window disabled, default times 09:00–21:00.
- Validate HH:MM and require the end time to be later than the start time. Show this requirement in the form.
- Count reservations and successful posts per connected account, including shared creators; preserve user isolation.
- A late arriving clip waits for the next available slot; overflow goes to the next day.
- Use existing form components, palette and Portuguese translations; no shadows or new visual system.
- Work in the supplied checkout and execute directly, following the user's request to use the recommendation.

## Review focus

1. Before start, exactly at end, month/year changes and more than 30 full days.
2. Paused/stopped app and retries must not send outside the window or burst within one slot.
3. Shared accounts and different users must keep independent reservations and correct caps.
4. Settings edits must update queued posts and the page sync counter.
5. Disabled controls and invalid times must behave consistently in the browser and API.

## Task 1: Settings and scheduling

Files: studio/creators.py, studio/posting.py, studio/server.py, tests/test_posts.py.

- [x] Add failing tests for persistence, validation, six slots from 09:00 to 21:00, late arrivals, overflow, shared accounts, disabled caps/windows, long queues and settings changes.
- [x] Run `.venv/Scripts/python.exe tests/test_posts.py`; verify failures concern the new behavior.
- [x] Add validated settings, account slot selection, queue rescheduling and dispatch checks (including retries).
- [x] Run the posting tests; all must pass.

## Task 2: Form and verification

Files: web/index.html, web/pt-br.js.

- [x] Add an optional daily window and native time fields beside the daily limit. Disable manual interval only when automatic spacing is active. Show the server time zone and a calculated schedule preview.
- [x] Validate enabled fields before saving and preserve the existing save/discard behavior.
- [x] Check the actual form at desktop and mobile sizes with temporary data.
- [x] Run `python tests/run.py` using the existing virtual environment and UTF-8 output, then inspect the diff and review the complete change.

## Verification record

- Initial system Python lacked SQLAlchemy; existing .venv has the project dependencies.
- Initial .venv suite hit Windows cp1252 output errors; rerun with PYTHONIOENCODING=utf-8 before production changes.
- UTF-8 baseline: full suite passed before production changes.
- Posting tests: new cases failed before implementation; all 21 now pass, including the retry cooldown regression.
- Independent review found two issues; fixed retry cooldown preservation and omission of disabled time fields from the form payload.
- Browser checks with temporary data: save/reload, schedule preview, invalid end times, disabling after clearing a time, daily limit off/on, desktop and 390px mobile (no horizontal overflow), no console errors.
- Full suite passed after the initial implementation and again after the review fixes (exit 0, All tests passed).
