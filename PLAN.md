# PeakCut Local CLI Plan

## Goal

Add a free, resumable Windows CLI for local video files while leaving the existing URL and web flows unchanged. The CLI will accept a file or folder, produce the existing 9:16 clipped format, and also produce candidate metadata and a review page.

## Reuse unchanged

- Keep `studio/server.py` URL routes and existing web upload flow intact.
- Keep `studio/jobs.py` job and queue logic for web jobs.
- Reuse `studio/media.py:probe()`, `extract_audio()` and `load_audio()` for local inputs.
- Reuse `studio/transcribe.py` for local ASR initially, because it already returns word timestamps and can run locally.
- Reuse `studio/brain.py` data cleaning, overlap removal concepts, and AI JSON parsing as reference, but isolate the new selector in a new module.
- Reuse `studio/render.py` 1080x1920 layouts and ASS generation. Improve it only for the export wrapper and timestamp/safe-margin requirements.
- Reuse `studio/facecam.py` and the existing centre-crop layout before introducing a second crop system.
- Reuse `studio/quality.py` only if static-range filtering is explicitly enabled.
- Keep existing AI providers behind `--selector llm` or an equivalent config flag. Ollama will be the default local optional LLM.

## New modules

Suggested addition:

- `peakcut_cli.py`: argparse entry point, file/folder discovery, validation, logging, progress and output orchestration.
- `studio/local_clip.py`: local input handling and per-run pipeline state.
- `studio/candidates.py`: heuristic scoring, LLM candidate validation, overlap removal and top-N merging.
- `studio/cache.py`: content-fingerprint ASR/audio/analysis cache.
- `studio/review.py`: local `review.html` and `keep.json` handling.
- `studio/export_manifest.py`: `candidates.json`, `captions.txt`, filenames and output validation.

The exact module names may be adjusted in implementation, but core logic should not be added to `studio/server.py` or `studio/jobs.py`.

## Heuristic selector

The first selector will be completely local and use:

- Questions, exclamations, bold claims and numbers in the transcript.
- Sentence-boundary starts and ends.
- Per-second loudness and speech-rate peaks.
- Filler-word penalties.
- Gap/long-silence penalties.
- Window duration limits of `--min` to `--max`.
- A single no-overlap pass based on selected windows.

The existing AI selector remains available behind an explicit selector/provider option. An optional LLM call will:

- Request strict JSON.
- Validate types and fields.
- Clamp scores and timestamps.
- Retry malformed or unavailable responses.
- Apply backoff for 429/5xx responses.
- Fall back to the heuristic result when it cannot produce valid candidates.

Top candidates from multiple sources will be merged and de-overlapped.

## ASR engine choice

The existing sherpa-onnx engine is already local and free, so it will remain the compatibility default and preserve URL behaviour.

The local CLI will add an `PEAKCUT_ASR_ENGINE=sherpa|faster-whisper` setting and a CLI equivalent. Implement faster-whisper support as an optional local adapter:

- Prefer CUDA if available.
- Otherwise use CPU.
- `small` or `base` model.
- int8 on CPU.
- Support `--language` passthrough, including Hinglish/non-English.
- Provide a clear error for missing dependencies instead of silently changing URL jobs.

Auto-detect GPU and engine availability, but never require a GPU. Tests must verify that the CPU fallback is selected.

## Caching

For every accepted local file:

- Create a SHA-256 content fingerprint.
- Cache transcript words and audio-analysis data outside the source file.
- Cache per-configuration results for language and ASR engine/model.
- Reuse completed transcripts on later runs.
- Save partial block results where possible.
- Make a rerun resume after interrupted transcription/rendering.

No cache entry should depend on a mutable absolute path. Clip output can depend on source fingerprint, time range and render settings.

## Cropping

Reuse `studio/render.py` center-crop and existing blur layout first.

For general talking-head clips:

- Keep center-crop as the reliable fallback.
- Reuse `studio/facecam.py` where it can identify a facecam or stable region.
- Add MediaPipe/OpenCV only if needed for frame-by-frame face tracking and smoothing.
- Add optional blurred-background mode using the existing blur filter as a local implementation.

The crop calculation must remain independent of caption timing and ASS generation.

## Captions

Reuse `studio/render.py:build_ass()` and improve it only where the local export requirements are not met:

- 2-4 word groups.
- Current-word highlight.
- Bold configured font.
- Safe Reels/TikTok margins.
- Configurable style through `.env` and CLI/config options.
- Word timestamps validated against clip start/end.
- Clamp negative, inverted, non-finite and out-of-bounds timestamps.
- Preserve language text and do not force English for local clips.

Existing caption translation/AI cleanup will not run in the local default. ASR text remains the source unless explicitly enabled.

## Export

The CLI will write one output folder per input or one shared run folder, depending on the folder layout chosen during implementation. Each clip will use:

- `01_<slug>.mp4`, `02_<slug>.mp4`, and onward.
- H.264.
- AAC.
- yuv420p.
- 30 fps.
- `+faststart`.
- 1080x1920 resolution.

It will also write:

- `candidates.json` with start, end, score, reason, snippet, suggested title and source filename/fingerprint.
- `<clip>.captions.txt` or a documented per-run text file with title and hashtags.
- `review.html`.
- `keep.json` for selected clip IDs.

The existing `.ass` sidecar will remain available.

## Review page

Generate a static local HTML file with no server dependency. It will contain:

- Embedded relative paths to previews.
- Score and reason.
- Start/end and suggested title.
- Checkboxes or equivalent controls.
- JavaScript that saves selections to `keep.json`.

The page must work offline and must not introduce posting or upload code.

## Requirements and documentation

- Update `requirements.txt` with pinned versions for faster-whisper, tqdm, pytest, and optional OpenCV/MediaPipe only after implementation confirms the selected crop method.
- Update `.env.example` with ASR, selector, crop and caption settings.
- Update `README.md` with Windows 3.11 setup, ffmpeg installation, local CLI usage, GPU/CPU behavior and troubleshooting.
- Add or update a Windows entry-point script only if it provides a clear benefit; do not replace `python -m studio.server`.

## Approved feature order

1. CLI input validation and file/folder discovery.
2. Simple transcript cache keyed by file hash, with no partial-block resume.
3. Heuristic selector with overlap removal.
4. Reuse existing crop and caption code for export; skip face tracking unless output looks bad.
5. `candidates.json` and `captions.txt` export.

Skipped for now: faster-whisper adapter, `review.html`/`keep.json`, and extra test coverage beyond the scorer, overlap, timestamp clamping and one end-to-end test.

Each numbered step will be one commit and stop for confirmation before the next step.

## Test plan

Before and after changes to `studio/`, run `python tests/run.py`.

Add focused pytest tests for:

- Heuristic text and audio scoring.
- Sentence and silence boundaries.
- Overlap removal and top-N merging.
- ASS 2-4 word grouping.
- Current-word highlight timing.
- Negative, reversed, non-finite and out-of-bounds timestamps.
- Fast GPU-unavailable fallback.
- File/folder extension validation.
- Cache hit and resume behavior.
- End-to-end export from a small local fixture.

No test should require an API key, network service or paid account.
