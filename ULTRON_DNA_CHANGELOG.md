# Ultron DNA A→Z rebuild

This build integrates the applicable behavior from the supplied Ultron/AutoFilter project into the Netflix-style website.

## Search
- AutoFilter-style Mongo regex search first.
- Filename + caption search.
- Bounded candidate retrieval and no full collection scan per request.
- Local fuzzy/spelling fallback only after normal search fails.
- Optional one-shot IMDb/Cinemagoer correction only after local fuzzy search fails.
- External correction candidates are accepted only when real Mongo media exists for the corrected title.
- Exact title/year ranking and logical movie/series grouping.
- Season/episode tokens are search context, not new catalog identities.
- `S01E01`, `S1E1`, `Season 1 Episode 1`, `1x01` and related forms are supported by the parser.
- Single-flight duplicate searches, bounded concurrency, and TTL/size caches.
- Two configured AutoFilter media databases are searched concurrently and merged without duplicate file IDs.

## OTT catalog behavior
- One logical movie card can contain many real physical Telegram assets.
- One logical series contains real seasons and real episodes only.
- No Cartesian-product quality/language generation.
- Every playable variant retains the original Mongo `_id`/`file_id`.
- Year/sequel identity is preserved to avoid merging distinct movies.

## Admin panel
- Nested Ultron-compatible settings tree.
- 1st/2nd/3rd shortener name + API.
- Verification timing and tutorial links.
- File/All-files/Not-copy mode configuration.
- Max results and result mode.
- Fuzzy/spell-check/external correction controls.
- Search candidate/cache/concurrency controls are live, not decorative.
- TMDB/poster controls are live.
- Maintenance mode is live.
- Admin remove/reset/save actions are live and validated.
- Shortener test action uses the configured Shortzy provider when available.
- Verification can protect website stream-token issuance with staged shortlink verification.

## Safety/performance
- AutoFilter media MongoDB is read-only from the website.
- `/health` stays lightweight.
- TMDB and IMDb work are outside the normal fast search path and are bounded/time-limited.
- Stream disconnect exceptions remain non-fatal.
- No generated `__pycache__`/`.pyc` files are included in the deployment archive.

## Telegram-only settings
Force-subscription membership checks, Telegram bot callback commands, Telegram message deletion, and Telegram `/start` verification are Telegram-runtime features. The website does not fake these as browser-native operations.
