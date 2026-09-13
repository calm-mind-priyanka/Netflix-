# StreamBox — standalone website connected to the existing Auto Filter Bot database

This is a **separate normal HTTPS website**, not a Telegram Mini App. It reads the existing Auto Filter Bot MongoDB media collection and automatically discovers newly indexed Telegram files. It does not rewrite or delete the bot's media records.

## Architecture

`Existing Auto Filter Bot -> Existing MongoDB -> StreamBox website -> users`

The website derives movie/series, season, episode, quality and language from the existing `file_name`/`caption` fields and groups variants automatically.

## Included

- Netflix-style dark responsive UI
- Search, movies and series grouping
- Season/episode grouping
- Quality/language variants shown inside the player settings
- Telegram-backed HTTP Range streaming
- Short-lived signed stream/download tokens
- Continue Watching in browser local storage
- Next episode
- Optional TMDB poster/metadata matching
- Server-side catalog cache with automatic refresh
- Protected `/admin` control panel
- Maintenance mode without deleting MongoDB content
- Catalog refresh button
- No MongoDB/Telegram secrets in frontend code

## Admin panel

Open `https://YOUR-KOYEB-URL/admin` and sign in with `ADMIN_USERNAME` and the the password in `ADMIN_PASSWORD`.

No password-hashing command is required for this deployment. Keep `ADMIN_PASSWORD` private in Koyeb.

The admin panel can:

- see title/movie/series counts
- turn the public website into maintenance mode
- bring it back online
- refresh the catalog/metadata cache
- log out

It intentionally does **not** delete the Koyeb service or delete the bot's MongoDB media records. To permanently remove the hosted website, use the Koyeb dashboard.

## Posters

Set `TMDB_API_KEY` for automatic poster/basic metadata matching. The current bot media schema does not contain dedicated poster fields, so this is the reliable automatic enrichment route. If TMDB has no match, the parser can use a poster URL present in the stored caption, otherwise a placeholder is shown.

## Koyeb

Deploy this directory as a Web Service. The included Procfile runs `python -m backend.app` and the app listens on `$PORT`.

Koyeb gives the service a public HTTPS `*.koyeb.app` URL. No custom domain is required. `PUBLIC_URL` is optional and is not hardcoded.

The website does not store the movie files on Koyeb disk; it streams from Telegram through the backend. Free/low-resource hosting is suitable for testing/light traffic, but sustained video traffic can require more resources.

## Security

Keep these values private in Koyeb environment variables: `DATABASE_URI`, `DATABASE_URI2`, `BOT_TOKEN`, `API_ID`, `API_HASH`, `SITE_SECRET`, `ADMIN_PASSWORD_HASH`, and `TMDB_API_KEY`.

Use a strong unique `SITE_SECRET` and strong admin password. HTTPS is provided by Koyeb. Do not commit a real `.env` file.

## Existing bot

No bot/database rewrite is required for the website itself. If you want a button in Telegram, add a normal HTTPS URL button pointing to the deployed website.
