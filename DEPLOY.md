# Putting PeakCut on a server

PeakCut runs as three containers on one Linux server: the app, PostgreSQL, and Caddy (which handles HTTPS).

## What you need

- A Linux server with Docker. Clipping is CPU work: plan on **8+ CPU cores, 16 GB RAM and 200+ GB of disk** if
  several people will use it. Transcription runs at roughly 10x real time per video on 4 cores.
- A domain name pointing at the server (an `A` record).
- An AI key (see "Choose your AI" in the README). Optional: a Google "Web application" OAuth client, and an SMTP
  service for emails.

## Steps

1. Copy this folder to the server (or `git clone` it there).
2. `cp .env.example .env` and fill it in: the AI lines, `PEAKCUT_DOMAIN`, `POSTGRES_PASSWORD`, and
   `COMPOSE_PROFILES=own-https` if the server is PeakCut's alone. Put key files, if you use files, in `./secrets/`.
3. `docker compose up -d --build`
   The first start downloads the speech model (about 480 MB) and creates the database tables.
4. Open `https://your-domain` and sign up. With Docker, accounts are on (`PEAKCUT_ACCOUNTS=1`), so everyone signs in.
   Every new account is a normal user.
5. Make yourself an admin, on the server: `docker compose exec app python -m studio.admin grant you@example.com`
   The admin dashboard is then at `https://your-domain/admin`. It answers "not found" for everyone who is not an
   admin, and no page or web request can make someone an admin: only that command can.
6. Sign in as that admin, open **Settings, AI**, and add your AI key there (or put it in `.env`).

## Connecting YouTube, Instagram and TikTok

People connect their accounts with one click in **Settings, Connected apps**: they press Connect, sign in on the
platform's own page, and come back. For that, the server needs one app registered with each platform. An admin can
do it right there: press **Set up** on a platform, follow the steps shown, and paste the app's id and secret (or put
them in `.env`, see `.env.example`). The redirect address to register is shown in the panel, with a Copy button.

- **YouTube** (Google Cloud): an OAuth client of type Web application, with YouTube Data API v3 turned on. Until
  Google verifies the app, people see a warning screen and uploads stay private.
- **Instagram** (Meta for Developers): a Business app using **Instagram API with Unified Login** (powered by
  Facebook Login for Business). Works with Creator and Business accounts. Until Meta approves your App Review,
  only accounts added as testers or business assets can connect. Instagram fetches each clip from a one-hour
  signed link on your site, so the site needs a public https address.
- **TikTok** (TikTok for Developers): an app with Login Kit and the Content Posting API (Direct Post), scopes
  `user.info.basic` and `video.publish`. Until TikTok audits the app, clips are posted as private (PeakCut does
  this by itself and says so on the account).

Instagram and TikTok only accept https redirect addresses.

## Meta / Instagram setup (Unified Login)

Meta requires new apps to use **Instagram API with Unified Login** via **Facebook Login for Business**:

1. In [Meta for Developers](https://developers.facebook.com/apps), create an app of type **Business**.
2. Under **Use cases**, choose **Manage messaging & content on Instagram**, click **Customize**, and select **API setup with Unified login**.
3. Under **Facebook Login for Business**, click **Configurations** and create or edit your configuration:
   - Name your configuration.
   - Under **Permissions**, select:
     - `instagram_content_publish` (required for posting Reels)
     - `instagram_manage_insights` (required for fetching Reel views and analytics)
     - `pages_show_list` (required for discovering connected Page and Instagram assets)
     - `business_management` (optional/recommended if managing business portfolio assets)
     - If available in your app's use case permissions, also select `instagram_basic` and `pages_read_engagement`.
   - Under **Allowed Redirect URIs**, enter your callback URL:
     `https://your-domain/oauth/instagram/callback`.
   - Copy the generated **Configuration ID**.
4. In Meta App Dashboard -> **App settings -> Basic**:
   - Copy the **App ID** (Meta App ID) and **App secret** (Meta App Secret).
5. In PeakCut, go to **Settings -> Connected apps -> Instagram -> Set up**:
   - Paste the **Meta App ID**, **Meta App Secret**, and **Login configuration ID** (or set `PEAKCUT_IG_APP_ID`, `PEAKCUT_IG_APP_SECRET`, and `PEAKCUT_IG_CONFIG_ID` in `.env`).
6. **Testing & App Review**:
   - While in **Development Mode**, add your Meta/Instagram account under **App roles** or assign the Instagram professional asset to your Business Portfolio.
   - For public production use by external creators, submit `instagram_basic` and `instagram_content_publish` for **App Review**.

### Keeping the older Instagram Login as well

Both ways of connecting an Instagram account can be set up at the same time. Settings -> Connected apps then shows
two Instagram cards: **Instagram (Unified Login)** and **Instagram (Instagram Login)**.

- **Instagram Login** is the older way: a Meta app with *API setup with Instagram login*, its own **Instagram app ID**
  and **Instagram app secret**, and the permissions `instagram_business_basic` and
  `instagram_business_content_publish`. Meta no longer offers it for newly created apps, but an existing app that
  has it keeps working. Set it up in the dashboard (**Set up** on its card) or with `PEAKCUT_IG_LOGIN_APP_ID` and
  `PEAKCUT_IG_LOGIN_APP_SECRET`. It takes no configuration ID.
- **Both use the same redirect address**, `https://your-domain/oauth/instagram/callback`. Register that one address
  in either app. PeakCut remembers which button was pressed, so each sign-in is finished the way it started.
- Accounts from either way are ordinary Instagram accounts: they are listed under Instagram, picked in a creator's
  Setup, and post Reels the same way. Each account remembers which API it uses, and Instagram accounts connected
  before Unified Login existed carry on with Instagram's own API without being connected again.
- `PEAKCUT_IG_APP_ID` and `PEAKCUT_IG_APP_SECRET` now belong to **Unified Login** (the Meta app). If you used them
  for the older Instagram Login app, move the values to `PEAKCUT_IG_LOGIN_APP_ID` and `PEAKCUT_IG_LOGIN_APP_SECRET`.

## Google setup (sign-in and YouTube)

In Google Cloud, create an OAuth client of type **Web application** with these redirect URIs:

- `https://your-domain/auth/google/callback`
- `https://your-domain/oauth/youtube/callback`

Enable **YouTube Data API v3** in the same project. Posting publicly to YouTube needs Google's API audit;
until then uploads from the API stay private.

## Email

Set the `PEAKCUT_SMTP_*` lines and `PEAKCUT_MAIL_FROM` to send password-reset and confirmation emails through any
SMTP service. On a public site also set `PEAKCUT_VERIFY_EMAIL=1`, so people confirm their address before clipping.
Without SMTP on an HTTPS site, no reset emails can be sent.

## Good to know

- **Data**: everything lives in two Docker volumes, `pgdata` (accounts, AI usage) and `data` (videos, clips).
  Back both up. `docker compose exec db pg_dump -U peakcut peakcut > backup.sql` dumps the database.
- **Capacity**: `PEAKCUT_JOB_WORKERS` is how many videos are processed at once; `PEAKCUT_MAX_LIVE` is how many live
  streams are recorded at once. Raise them only as far as the CPU allows.
- **One server only**: jobs and live recordings run inside the app process. Do not start a second copy of the app
  against the same data.
- **YouTube downloads from data centres** are sometimes refused by YouTube ("confirm you're not a bot"). If links
  fail on the server but work at home, the server's address is the cause. PeakCut then pauses YouTube links for a
  few minutes and says so; uploads and Twitch or Kick links keep working.
- **Updates**: `git pull && docker compose up -d --build`. A restart pauses videos in progress (they pick up
  where they stopped) and live recordings (clips already found are finished from the saved stream).
- **Open sign-up**: anyone who can reach the site can make an account and use your AI key. On a public address,
  keep the limits in `.env` sensible, or put the site behind your own access control.

## A server that already runs other sites

Leave `COMPOSE_PROFILES=own-https` out of `.env`. PeakCut then listens only on `127.0.0.1:8765` and the web
server that is already there forwards the domain to it: see `deploy/nginx-peakcut.conf`, or
`deploy/docker-compose.traefik.yml` for Traefik.

## Cloudflare CDN in front

With the record proxied (orange cloud):

- Set `PEAKCUT_CLIENT_IP_HEADER=CF-Connecting-IP` in `.env`, so rate limits see each visitor, not Cloudflare. The
  header is only believed for connections that really come from Cloudflare's address ranges.
- Images, videos and scripts under `/brand/` and `/assets/` are cached by Cloudflare automatically. Signed-in
  pages, clips and the API send `private` or `no-store` and are never cached by Cloudflare.
- Use SSL/TLS mode Full (strict) with a Cloudflare origin certificate in `secrets/` and
  `PEAKCUT_CADDYFILE=Caddyfile.origin`.
- Cloudflare's standard plans refuse requests over 100 MB. PeakCut sends big uploads in pieces
  (`PEAKCUT_UPLOAD_PART_MB`, 40 by default), so large files still work.

## Speech to text on a graphics card

Optional. A second machine with an NVIDIA card can do most of the transcription much faster:
`deploy/gpu/docker-compose.yml` runs it, and `PEAKCUT_ASR_URL` plus `PEAKCUT_ASR_TOKEN` on the main server point
at it. Keep its port closed to the internet. If it is unreachable, the main server carries on with its own
processor.
