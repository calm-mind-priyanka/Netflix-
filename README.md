# VYRA

VYRA is a lightweight web client for an existing Telegram AutoFilter media database. It is intentionally a lightweight AutoFilter-style site. The UI is text-first, fast, and designed for low CPU/RAM use on Koyeb Free.

## Architecture

`Telegram AutoFilter -> existing MongoDB media collection (READ ONLY) -> VYRA API -> browser`

The supplied Ultron/AutoFilter project is used as the behavioral blueprint for search, verification, shorteners, premium, and payment flows. Ultron itself is not modified.

## UI

- No poster grid, hero poster, OTT-style cards, or heavy homepage enrichment.
- Blue / white / red visual language.
- Text-only Trending, Newly Added, Movies, Web Series, and Categories.
- Ultron-style result count and compact result rows.
- Series remain Series -> Season -> Episode -> real media asset.
- Every Watch/Download action resolves to the original Telegram `file_id`.

## Access

A single server-side access gate protects playback and download. It checks premium first, then website verification settings. Verification can use up to three shortener stages and tutorial URLs. Premium can bypass verification while active. Manual payment proofs are stored in MongoDB for admin approval; automatic payments use Razorpay when configured.

## Storage

The AutoFilter media collection is never written. Website state is stored in dedicated collections in the same configured MongoDB database. There is no required `/tmp` state store.

## Koyeb

The app listens on `0.0.0.0` and `$PORT`. `/health` is lightweight. Search uses bounded Mongo candidates plus short in-process caches and does not enrich the homepage with TMDB calls.

## Main endpoints

- `/api/home`
- `/api/search?q=...`
- `/api/title/{id}`
- `/api/filter-options` and `/api/filter`
- `/api/resolve`
- `/api/access-status`
- `/api/stream-token/{file_id}`
- `/api/stream/{file_id}`
- `/api/download/{file_id}`
- `/api/premium...`
- `/api/verify`
- `/admin/`

## Environment

Copy `.env.example` to `.env` or configure the same variables in Koyeb. Keep the existing AutoFilter Mongo URI/database/collection variables. Set a long random `SITE_SECRET`. Configure Telegram credentials only when playback/download is required; website verification itself does not require Telegram.
