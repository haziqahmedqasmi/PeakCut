# PeakCut

Turn long videos and live streams into captioned vertical clips, ready to post. Free and open source: run it on
your own computer or server, with your own AI key.

Paste a YouTube, Twitch or Kick link (or upload a file) and PeakCut finds the moments worth clipping, cuts them to
9:16, adds word-by-word captions and a hook, and can post them to your connected accounts. It can also record a
creator's live stream and clip the highlights while the stream is still running.

## What it does

- **Clips from any video.** Transcribes the audio, asks an AI which moments would hold a stranger's attention, and
  renders each one with captions in one of three styles.
- **Live clipping.** Records a stream into a rolling buffer and clips as it happens, using loudness, chat activity
  and the transcript.
- **Workspaces per creator.** Channels, clip settings, posting rules and an activity log for each creator you clip.
- **Posting.** Connect YouTube, Instagram and TikTok in one click, then queue or auto-post finished clips, with
  spacing and daily caps (see [DEPLOY.md](DEPLOY.md#connecting-youtube-instagram-and-tiktok) for the one-time setup).
- **No sign-in on your own computer.** It opens straight into the dashboard. On a shared server, switch accounts on
  (`PEAKCUT_ACCOUNTS=1`) for email or Google sign-in and an admin dashboard.
- **Your own AI.** Gemini, OpenAI, Grok, Groq, OpenRouter, Claude, a local model through Ollama, or anything that
  speaks the OpenAI chat format. No key at all also works: clips are then picked by loudness.

## Quick start

You need Python 3.10 or newer and `ffmpeg`. On Windows: `winget install Gyan.FFmpeg`. On a Mac: `brew install ffmpeg`.
On Linux: `sudo apt install ffmpeg`. PeakCut finds it by itself and tells you in the dashboard if it is missing.

Put PeakCut in a short folder that stays put: `C:\peakcut` on Windows, `~/peakcut` on a Mac or Linux. Not inside a
temporary folder or an AI assistant's workspace: those get cleaned up, and on Windows a folder that deep stops the
speech model from loading (Windows can't open files whose full path is over 260 characters). If an AI assistant
installs it for you, it should ask you where to put it first ([AGENTS.md](AGENTS.md) tells it to).

On Windows:

```bash
git clone <this repository> C:\peakcut
```

```bash
cd C:\peakcut
```

On a Mac or Linux:

```bash
git clone <this repository> ~/peakcut
```

```bash
cd ~/peakcut
```

```bash
pip install -r requirements.txt
```

Start the server:

```bash
python -m studio.server
```

The first start downloads the speech model by itself (about 480 MB, once).

Open http://127.0.0.1:8765. There is no sign-in: it opens straight into the dashboard. Go to **Settings, AI**, choose
your AI service, paste your key and press **Save and test** (see [Choose your AI](#choose-your-ai)).

## Choose your AI

PeakCut needs a chat model that can return JSON. The easy way is in the dashboard: **Settings, AI**. Pick the
service, paste your key, press **Find models** to choose from the service's own list, then **Save and test**. The
key is kept in the data folder on your machine and is never shown again.

If you prefer a file (or run a server), the same three things can go in `.env`. What is saved in the dashboard wins:

```ini
PEAKCUT_AI_PROVIDER=gemini
PEAKCUT_AI_MODELS=gemini-2.5-flash
PEAKCUT_AI_KEY=your-key-here
```

| `PEAKCUT_AI_PROVIDER` | Service | Where to get a key |
|---|---|---|
| `gemini` | Google Gemini | https://aistudio.google.com/apikey |
| `openai` | OpenAI | https://platform.openai.com/api-keys |
| `xai` | Grok (xAI) | https://console.x.ai |
| `groq` | Groq | https://console.groq.com/keys |
| `openrouter` | OpenRouter (many models, one key) | https://openrouter.ai/keys |
| `anthropic` | Claude | https://console.anthropic.com |
| `ollama` | A model on your own machine, no key | https://ollama.com |
| `custom` | Any OpenAI-compatible address: also set `PEAKCUT_AI_BASE_URL` | |

- `PEAKCUT_AI_MODELS` is the model id exactly as the service names it. Model names change often, so check the
  service's current list. Several ids separated by commas are tried in order, which gives you a fallback.
- A fast, inexpensive model is the right choice. A one-hour video is read in about four requests.
- You pay the AI service directly for what you use. The **AI usage** page in the app shows how many requests and
  tokens each video took.
- Speech to text does not use the AI service. It runs on your own machine, so audio never leaves it.

## Settings

Everything is set through environment variables, normally in a `.env` file next to this README.
[`.env.example`](.env.example) lists every setting with a short explanation. The ones that matter first:

| Setting | What it is for |
|---|---|
| `PEAKCUT_AI_PROVIDER`, `PEAKCUT_AI_MODELS`, `PEAKCUT_AI_KEY` | The AI that picks clips and writes titles (or set it in the dashboard) |
| `PEAKCUT_ACCOUNTS` | `1` makes everyone sign in. Use it on any server other people can reach |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional: Sign in with Google, and connecting YouTube |
| `PEAKCUT_SMTP_*`, `PEAKCUT_MAIL_FROM` | Optional: confirmation and password emails |
| `PEAKCUT_DATA` | Where videos, clips and the database are kept (default `./data`) |

`.env`, the `secrets/` folder and all user data are ignored by git. Keep keys there, never in the code.

## Admins

This only matters with accounts switched on (`PEAKCUT_ACCOUNTS=1`); without them you are the owner and can do
everything. An account is an admin only when the database says so. No email address is special in the code and no web request
can change a role. Do it on the machine that runs PeakCut:

```bash
python -m studio.admin grant you@example.com
```

`revoke` and `list` work the same way. The admin dashboard is at `/admin` and answers "not found" to everyone else.

## How it is built

| Part | What it is |
|---|---|
| `studio/` | The server: FastAPI, the clip pipeline, live recording, accounts |
| `studio/ai.py` | The only file that talks to an AI service. Add a provider here |
| `web/` | The pages: landing, sign-in, the app, the admin dashboard (plain HTML, CSS and JavaScript, no build step) |
| `tests/` | Tests that run against a throwaway copy of the app |
| `deploy/`, `Dockerfile`, `docker-compose.yml` | One-server deployment with PostgreSQL and automatic HTTPS |

Video work is done with `ffmpeg` and `yt-dlp`. Speech is transcribed with `sherpa-onnx` and the Parakeet model.
Data is kept per user: accounts in the database (SQLite on your own computer, PostgreSQL on a server), videos and
clips in each user's own folder. [AGENTS.md](AGENTS.md) is a short map of the code for contributors and AI coding
assistants.

## Where your videos and clips are kept

Everything PeakCut keeps is in one folder: videos, clips, recordings, creators, settings and the speech model. By
default it is the `data` folder inside PeakCut. **Settings, Storage** shows the folder and how much room its drive
has, opens it in one click, and lets you move it to another drive: choose the new folder, and PeakCut moves
everything there the next time it starts. If the drive is nearly full, PeakCut says so and stops taking new videos
until there is room. (`PEAKCUT_DATA` in `.env` still works and takes priority.)

## Updating

PeakCut checks GitHub once a minute while it runs. When a newer version is out, the dashboard shows a notice
within a minute or two (no restart needed), with what changed
and how to update: ask your AI coding assistant to do it (the notice has a sentence to paste), or run
`git pull && pip install -r requirements.txt` in the PeakCut folder and restart it (with Docker:
`git pull && docker compose up -d --build`). Your `.env`, keys and clips are not touched. Only the two commit ids
are sent to GitHub; set `PEAKCUT_UPDATE_CHECK=0` to turn the check off.

## Deploy

[DEPLOY.md](DEPLOY.md) walks through a single server with Docker: the app, PostgreSQL, and Caddy for HTTPS.

```bash
docker compose up -d --build
```

## How it behaves under load

- The page asks the server one small question every few seconds. When nothing has changed, the answer comes
  straight from memory.
- Each person's videos wait in their own line and the lines take turns, so one person cannot hold everyone up.
- The server measures its own processor, memory and disk every second. Extra videos and recordings start only
  while there is room, wait briefly when it is nearly full, and continue the moment it frees up.
- There are no limits on upload size, video length, recordings, creators or storage unless you set them in
  `.env`. Five videos can be sent at once. Request sizes are always capped, so junk can't fill the memory.

## Tests

```bash
python tests/run.py
```

This starts a copy of the app on a spare port with an empty temporary data folder, runs the permission, abuse and
queue tests against it, then removes it. It needs no keys and never touches real data.

## Security

- With sign-in off, PeakCut listens on this computer only (`127.0.0.1`), refuses changes sent by other websites,
  and only answers to its own address. Do not expose it to the internet that way: turn accounts on.
- Passwords are stored only as salted scrypt hashes. Sessions are random tokens stored hashed; cookies are HTTP-only.
- Every route works out who is asking from the session, never from the request, so users only reach their own data.
- Only known video sites are fetched, and text from videos is treated as data when it is shown to the AI.
- Found a problem? Please report it privately through the repository's security advisories instead of opening a
  public issue.

## Using it responsibly

Clip videos you made, or have permission to use. You are responsible for the rights to what you download, clip and
post, and for following the terms of the sites you fetch from and post to.

## Licence

[MIT](LICENSE). Caption fonts in `studio/fonts/` (Anton, Archivo Black, Poppins) are under the SIL Open Font
License; the licence texts are in the same folder.


## Local LACY clipping workflow

The creator Setup page supports fixed live intervals (`live_clip_interval_sec`), second-based post spacing (`post_gap_seconds`), and a local daily cap of 0 for no PeakCut cap. Fixed interval clips receive AI-generated titles and descriptions and can auto-post regardless of their highlight score. Platform quotas and processing time still apply.

Every upload requires a creator mention and hashtag. TikTok has its own mention field. `credit_name` adds the creator name to the rendered heading; an older clip without that rendered name is blocked until rendered again. Required credit and hashtags are preserved when shortening descriptions, and missing credit blocks the upload before any platform request.

For this installation the owner requested LACY on screen, @LacyHimself on YouTube, @lacy on TikTok, #LACY, and the supplied GTA tags. These are creator settings, not AI guesses.

The supplied campaign guidelines require content featuring Lacy during the GTA marathon starting November 18, 2026, with Lacy as the primary focus; posting only on TikTok, Instagram Reels, YouTube Shorts or X; at least 50% of the audience from the USA, Canada, Australia, UK or New Zealand; and an account with a related audience niche. Only approved videos meeting the campaign's total view requirement qualify for its revenue share. Attribution checks do not verify audience geography, content eligibility, campaign approval or revenue-share qualification. Review those separately in channel analytics and the campaign rules.

## TikTok through Buffer and Cloudinary

Settings → Connected apps → TikTok via Buffer accepts a personal Buffer API key and signed Cloudinary upload credentials. Connect the TikTok channel in Buffer first, save the keys locally, find the channel, then select it in the creator's Setup. API credentials are kept in that user's ignored accounts folder with private file permissions; the dashboard only receives whether a key is saved.

Buffer fetches selected MP4 clips from stable public Cloudinary HTTPS URLs. This does not require exposing the local server. TikTok uses its own five-hashtag selection, including the creator hashtag; YouTube retains its full tag list. Generated hashtags elsewhere in the TikTok caption are removed so they cannot exceed that selection. Buffer publishes these posts publicly and automatically. Do not use this destination for a creator configured for private publishing.

The default TikTok pace is one post every 180 minutes, at most eight per day, independently of the YouTube schedule. At most one TikTok clip waits in PeakCut; additional automatic clips remain available locally and are not added to a growing backlog. A post stays “Publishing in Buffer” until Buffer reports it sent. Ambiguous submissions are not automatically retried, and posts already submitted must be checked or retried in Buffer to avoid duplicates.

The local upload budget defaults to 1000 MB per UTC month, with a 100 MB per-file limit. It counts attempted transfers and does not measure Cloudinary's separate storage, transformation or delivery credits. Monitor the provider dashboards: free plans and platform quotas still apply. Hosted videos are retained; removing a PeakCut account or cancelling a local post does not delete hosted assets or posts already accepted by Buffer.

References: [Buffer video hosting](https://developers.buffer.com/guides/hosting-media.html), [Buffer API](https://developers.buffer.com/reference.html), [TikTok with Buffer](https://support.buffer.com/articles/using-tiktok-with-buffer-oGEroY9Of2), [Cloudinary authenticated uploads](https://cloudinary.com/documentation/upload_images).

The Home post queue has separate platform pause controls as well as the global pause. Pausing YouTube leaves its clips queued while TikTok continues. A platform pause is saved per user and survives restarting the studio. Resuming a platform releases its overdue queue too; review the waiting clips before resuming.

An explicitly requested archive can queue more than one TikTok clip by setting `backlog: true` on `POST /api/posts`. This preserves the normal automatic one-clip queue limit while scheduling the requested saved clips with TikTok's separate spacing and daily cap. Repeating the archive request skips clips already queued, posted or submitted with an uncertain response.

TikTok spacing can be set to 0 to start the next clip after Buffer confirms the previous publication. The queue enforces one upload/publication at a time and a rolling 24-hour cap of at most 15 even after downtime or across midnight. Saving a new spacing or cap also recalculates all waiting Buffer clips without changing post IDs or completed uploads. Publication checks start after 30 seconds and back off when needed. A rejected or uncertain submission pauses TikTok; resolve it in Buffer before resuming. High-volume automated posting can reduce reach: Buffer recommends 1–3 posts daily, and TikTok's account-specific limits can be lower than the configured cap.


### Buffered live quality review

In a creator's Setup, enable **Review buffered video for quality** to collect a 10- or 15-minute batch before selecting a clip. The AI reviews timestamped video frames sampled every ten seconds together with the transcript. It proposes at most one strong moment, then a second review samples that proposed clip every two seconds. Both reviews must score at least 90 and meet your higher score threshold, if set. The clip uses the configured length range (30–60 seconds is a useful starting point). Nothing is forced when a batch has no suitable hook or a review fails.

Set **Content to look for** to the game and moments you want. Reviews reject ads, breaks, idle/absent creators and unrelated footage. These are sampled AI judgments, so they can miss details and cannot guarantee virality. Fixed intervals, automatic spoken triggers and forced-clip settings are ignored in this mode; manual clip buttons remain available. One minute overlaps consecutive batches and existing clips are checked to prevent duplicates. The live card shows when the next batch is due and the latest decisions.

Quality review keeps at least the batch length plus five minutes in the rolling buffer; set a longer retention if desired. Selected source pieces are preserved separately until rendering completes, including across restarts. Unselected footage expires with the rolling buffer. Old unreviewed clips do not automatically queue in quality mode, but posts already waiting in the publishing queue must be cancelled or paused separately. Publishing pace and account pauses remain separate settings.

Maintainer walkthrough: [buffered live review and publishing setup](docs/buffered-live-walkthrough.md).
