# Ultron DNA — A→Z integration changelog

This build integrates the supplied Ultron AutoFilter behavior into the website where the concepts apply to a web/OTT application.

## Admin settings tree

The admin panel now mirrors the supplied `plugins/settings.py` hierarchy:

- Verification / shortlink enable state
- 1st / 2nd / 3rd shortener website/name and API key
- Verification time 1/2 and 2/3
- Tutorial 1/2/3
- File mode (`file`, `allfiles`, `notcopy`)
- File mode type and shortlink mode
- Result mode (`buttons` / `links`)
- Max results
- File secure
- Auto delete + delete time
- Welcome
- IMDb/poster switch
- Log channel
- Force-sub channel list
- Custom file caption
- Search candidate/cache/concurrency controls
- Fuzzy/spelling fallback
- External correction toggle
- TMDB/poster fallback
- Maintenance mode
- Individual remove/reset actions

The settings module also maintains an `ultron` compatibility map using the original setting names such as `is_verify`, `button`, `max_btn`, `shortner`, `api`, `shortner_two`, `api_two`, `shortner_three`, `api_three`, `verify_time`, `third_verify_time`, `tutorial`, `tutorial_2`, `tutorial_3`, `caption`, `log`, `fsub_id`, `auto_delete`, and `auto_del_time`.

## Security

Shortener API values are write-only in the admin API. The browser never receives stored API keys. Leaving an API field blank keeps the existing secret. Individual remove actions restore the setting to its default.

## Search

Normal Mongo search remains the first path. Local fuzzy search is fallback-only. External correction remains optional/fallback-only. The visible result count now honors the configured Ultron-style max-results value, while the underlying title grouping still prevents physical file/episode duplicates.

## Database safety

The AutoFilter media MongoDB remains read-only. Website settings are stored separately using `WEBSITE_SETTINGS_FILE`.

## Streaming safety

The existing exact Telegram media resolution/streaming path was retained. The website still resolves the stored media record and its exact Telegram file reference rather than inventing file IDs.

## Validation performed

- Python `compileall` — PASS
- settings smoke test — PASS
- settings compatibility/redaction/remove test — PASS
- parser sample tests for movie/series/season/episode cases — PASS
- JavaScript syntax checks for admin/app/api/player — PASS
