# PeakCut Analysis

## Repository and working mode

- The project is a FastAPI application with an embedded, plain HTML/JavaScript dashboard.
- The current branch is `local-video-input`.
- `studio/server.py` owns HTTP routes, security middleware, uploads, job control and review APIs.
- `studio/jobs.py` owns the fair queue, job state, block-by-block transcription, moment selection and clip rendering.
- `studio/media.py` owns platform metadata/download helpers, ffmpeg probing and audio extraction.
- `studio/transcribe.py` owns word-timestamped speech recognition.
- `studio/brain.py` owns AI prompts, clip selection, titles and caption fixes.
- `studio/render.py` owns 9:16 layouts, ASS captions and ffmpeg encoding.
- `studio/facecam.py` provides local frame/border analysis and optional AI-assisted webcam placement.
- `studio/quality.py`, `studio/games.py` and `studio/live.py` handle static-screen filtering, gaming metadata and live clipping.
- `studio/ai.py`, `studio/usage.py` and `studio/usage.py` manage optional external AI providers and usage.
- `studio/db.py` contains SQLAlchemy account and database setup. SQLite is the normal local database.
- `web/index.html` is the signed-in app. The other HTML files are landing, authentication and policy pages.
- `tests/run.py` starts a throwaway PeakCut copy and runs the repository's integration checks.

PeakCut has no build step. It runs directly with Python and serves the files in `web/`.

## Today’s pipeline

### 1. Input

There are two existing web input modes:

- Link mode submits a URL to `POST /api/jobs`.
- Uploaded-file mode submits a file to `POST /api/upload` or the chunked upload endpoints beginning at `POST /api/upload/start`.

`create_from_link()` is in `studio/server.py`. It validates the URL against the configured allowed sites, then calls `jobs.create({"type": "url", "url": url, "name": url}, ...)`.

`create_from_file()` accepts mp4, mov, mkv or webm. It saves the upload, calls `jobs.create({"type": "file", "path": str(dest), "name": name}, ...)`, and enqueues the job.

The existing job format already distinguishes `"url"` and `"file"`, and `_run_job()` already accepts a file source. This means local CLI work can reuse the core file path rather than adding a separate clip pipeline.

### 2. Download or local source preparation

For `"url"` jobs, `_run_job()` in `studio/jobs.py`:

1. Calls `media.info()` to obtain title, uploader and duration.
2. Rejects playlists, channels and live streams.
3. Downloads only audio with `media.download_audio()`.
4. For shorter videos, submits `media.download()` to a background thread to obtain the complete video.
5. For videos longer than 40 minutes, normally fetches only the selected clip sections later.

The exact URL/download plug-in is:

- `studio/server.py:create_from_link()` -> `jobs.create(..., "type": "url", "url": ...)`
- `studio/jobs.py:_run_job()` -> `src["type"] == "url"` branch
- `studio/jobs.py:_run_job()` -> `media.info()`, `media.download_audio()`, `media.download()`
- `studio/media.py:download_audio()` and `studio/media.py:download()` -> `yt-dlp`
- `studio/jobs.py:_fetch_piece()` -> `media.download_section()` for long-video clip stretches

For a `"file"` job, `_run_job()` skips all of those download calls. It calls `media.probe()` on the local path, uses the file as `source_path`, and goes directly to audio extraction.

### 3. Audio extraction

`studio/media.py:extract_audio()` uses ffmpeg to make a mono 16 kHz WAV stream. `media.load_audio()` loads it as 16-bit samples with `soundfile`.

The loudness for each second is calculated with `media.loudness_per_second()`.

### 4. Transcription

`studio/transcribe.py` uses sherpa-onnx and the bundled Parakeet NeMo TDT model. Its default provider is CPU. It can use a separate GPU ASR worker configured by `PEAKCUT_ASR_URL`, and it falls back to local CPU decoding if the remote worker is unavailable.

The recognizer:

- Cuts audio into 12-24 second pieces at quiet points.
- Skips silent pieces.
- Runs offline greedy decoding.
- Returns word dictionaries shaped as `{"w": word, "s": start, "e": end}`.
- Can optionally use YouTube's own transcript for URL jobs when `PEAKCUT_YT_TRANSCRIPT=1`, while still transcribing clips locally for accurate clip captions.

The model is downloaded once from GitHub into `data/models/` by `studio/fetch_model.py`. This is a model download, not a paid service.

### 5. Moment selection

`studio/brain.py:find_clips()` is the existing selector. It:

- Builds caption lines from words.
- Splits the transcript into 15-minute windows with 60-second overlap.
- Measures loudness per second.
- Sends transcript windows to an optional configured AI provider.
- Accepts timestamped JSON candidates.
- Snaps starts to transcript lines and ends to sentence boundaries.
- Removes overlapping candidates.
- Falls back to `fallback_by_loudness()` if AI is unavailable.

URL jobs may also use YouTube's transcript before local ASR. For local files, `_run_job()` never calls YouTube metadata/transcript helpers and should not need them.

The existing fallback is free and local, but it only scores audio loudness. The new heuristic selector should add hook text, speech rate, energy peaks, filler/silence penalties and sentence-boundary reasoning.

### 6. Crop and layout

`studio/render.py` exports 1080x1920 clips. Existing layouts are:

- `blur`: whole frame in a blurred background with a foreground frame.
- `zoom`: enlarged foreground with blurred fill.
- `crop`: centred 9:16 crop.
- `gameplay`: gameplay filling the screen with a detected webcam in the corner.

`studio/facecam.py` is a local image-analysis implementation that uses NumPy and ffmpeg. It looks for stable borders and stillness, and can optionally ask an already configured AI provider to identify a webcam. It does not currently use OpenCV or MediaPipe, but it is a usable existing crop foundation.

For general talking videos, the existing crop layout can be centre-cropped. Face tracking can be added later through MediaPipe or by smoothing the existing webcam/face detection, depending on the source type.

### 7. Captions

`studio/render.py:build_ass()` creates an ASS subtitle file. Existing features include:

- Word and highlighted-word timing.
- 2-4 word groups in the configured styles.
- Base, active and strong word colours.
- Uppercase mode, font style and outline settings.
- Hook text and safe caption areas.
- Caption fonts bundled under `studio/fonts/`.
- Existing safe positioning based on the layout.

`studio/jobs.py:_caption_words()` first reuses cached corrected words, then optionally calls `brain.english_captions()` to translate or repair text. `_caption_words()` subtracts the clip/source offset before rendering.

The ASS file is used as an ffmpeg subtitle burn-in filter.

### 8. Export

`studio/render.py:_render_in()` ffmpeg-encodes each clip. The export currently:

- Trims the source with `-ss` and `-t`.
- Outputs 1080x1920 through crop/scale filters.
- Uses `fps=30` (`config.OUT_FPS` is 30).
- Normalizes audio to AAC, 48 kHz, 160 kbps.
- Uses H.264 QSV when available, with libx264/yuv420p fallback.
- Uses `-movflags +faststart`.
- Writes `<clip-id>.mp4`, `<clip-id>.jpg` and `<clip-id>.ass`.

The output is already H.264, AAC, yuv420p and fast-start where supported. Naming and sidecar JSON need a CLI-specific export wrapper.

## Libraries and external tools

Direct dependencies in `requirements.txt`:

- FastAPI / Uvicorn: web server.
- HTTPX: provider and platform HTTP calls.
- NumPy: audio and image calculations.
- soundfile: WAV loading.
- sherpa-onnx: local speech recognition.
- SQLAlchemy and psycopg: database access.
- python-multipart: uploads.
- yt-dlp: existing URL downloads and platform metadata.
- Pillow: existing font measurement.

External tools:

- ffmpeg: download audio, probe media, crop/scale, encode and burn captions.
- ffprobe: video metadata.
- The Parakeet/sherpa-onnx speech model: local ASR.

Optional new dependencies for the local CLI should remain free and open-source:

- faster-whisper for an optional alternative ASR engine.
- tqdm for progress reporting.
- pytest for focused tests.
- OpenCV and/or MediaPipe only if implemented face tracking needs them.

## Paid or external services

### Existing paid/credit-based services

- The AI providers in `studio/ai.py` can require keys or paid usage: Gemini, OpenAI, xAI, Groq, OpenRouter and Anthropic.
- YouTube, Instagram and TikTok posting use their APIs and require developer accounts/app setup. These OAuth and posting paths do not need to run for local clip generation, and are outside this task.
- SMTP is optional and external, but it is not needed for CLI clipping.
- Google sign-in and OAuth may be external services, but they are not in the local CLI pipeline.

### Existing free/local services

- Local sherpa-onnx speech recognition has no API cost.
- The loudness fallback in `studio/brain.py` is local.
- Ollama is a local OpenAI-compatible model and has no API fee.
- A free Gemini tier may be used as an optional selector, but the default should remain heuristic/local.
- ffmpeg, yt-dlp, NumPy, Pillow, OpenCV and MediaPipe are free/open-source tools.

### GitHub/network services

- The speech model is downloaded once from a GitHub release.
- Optional update checks contact GitHub.
- The existing platform/profile code contacts YouTube/Twitch/Kick services.

New local mode must not call any platform downloader or scraper.

## Existing caching and resumability

- URL and file job state is saved as `job.json`.
- `words.json` caches the full word transcript.
- Restart handling keeps completed blocks and can resume.
- Fixed caption words are cached on each clip with their time span.
- Downloaded source pieces are saved under the job.
- The renderer writes clip output and rerender state through the existing job flow.

The existing cache is job-centric rather than content-addressed. A local CLI run should add a stable fingerprint cache for each input file so another run or a second file/folder invocation does not re-transcribe unchanged content.
