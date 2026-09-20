"""Offline smoke checks for the website's Ultron-style behavior.

These tests never connect to the user's MongoDB or Telegram account.
"""
import asyncio
import os
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["WEBSITE_SETTINGS_FILE"] = str(Path(tmp) / "settings.json")
        from backend.admin_settings import get_settings, update_settings, reset_settings
        from backend.parser import parse_doc, normalize_query

        defaults = get_settings()
        assert defaults["verification"]["shorteners"]["1"]["name"] == ""
        assert defaults["search"]["spell_check"] is True
        updated = asyncio.run(update_settings({
            "verification": {
                "enabled": True,
                "verification_time_2": 120,
                "shorteners": {"1": {"name": "example", "api": "secret"}},
            },
            "search": {"candidate_limit": 80, "search_concurrency": 2},
        }))
        assert updated["verification"]["enabled"] is True
        assert updated["verification"]["verification_time_2"] == 120
        assert updated["ultron"]["shortner"] == "example"
        assert updated["ultron"]["verify_time"] == 120

        p = parse_doc({
            "_id": "real-file-id",
            "file_name": "Reacher S04E07 1080p WEB-DL HEVC Hindi English Dual Audio.mkv",
            "file_size": 1,
            "caption": "",
        })
        assert p["title"] == "Reacher"
        assert p["season"] == 4 and p["episode"] == 7
        assert p["quality"] == "1080P"
        assert set(p["languages"]) == {"Hindi", "English"}
        assert p["audio_type"] == "Dual Audio"

        for q, season, episode in [
            ("Reacher S04", 4, None),
            ("Reacher S04E07", 4, 7),
            ("Reacher Season 1 Episode 3", 1, 3),
            ("Reacher 1x03", 1, 3),
        ]:
            parsed = normalize_query(q)
            assert parsed["title"] == "Reacher"
            assert parsed["season"] == season
            assert parsed["episode"] == episode

        ultron_source = Path(__file__).with_name("backend") / "ultron_search.py"
        source = ultron_source.read_text(encoding="utf-8")
        assert "class UltronSearchEngine" in source
        assert "_imdb_correct" in source
        assert "_local_correction" in source
        from backend.admin_settings import remove_setting
        removed = asyncio.run(remove_setting("verification.shorteners.1"))
        assert removed["verification"]["shorteners"]["1"]["name"] == ""
        reset = asyncio.run(reset_settings())
        assert reset["verification"]["enabled"] is False

    print("Ultron DNA full smoke test: PASS")


if __name__ == "__main__":
    main()
