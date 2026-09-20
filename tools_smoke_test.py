"""Offline smoke checks for VYRA.

No MongoDB, Telegram, payment provider, or network access is required.
"""
import asyncio


def main():
    from backend.admin_settings import get_settings
    from backend.parser import parse_doc, normalize_query
    from backend.verification import required_stage

    defaults = get_settings()
    assert defaults["verification"]["shorteners"]["1"]["enabled"] is False
    assert defaults["verification"]["validity_hours"] == 24

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

    for q, season, episode in [(
        "Reacher S04", 4, None), (
        "Reacher S04E07", 4, 7), (
        "Reacher Season 1 Episode 3", 1, 3), (
        "Reacher 1x03", 1, 3),
    ]:
        parsed = normalize_query(q)
        assert parsed["title"] == "Reacher"
        assert parsed["season"] == season
        assert parsed["episode"] == episode

    # Stage 1 is always the first gate. Later stages activate only when their
    # corresponding shortener is enabled in the persisted/admin settings.
    assert required_stage(0, 0, 100) == 1


    print("VYRA A-Z offline smoke test: PASS")


if __name__ == "__main__":
    main()
