"""Small offline smoke checks for the Ultron-style settings layer."""
import asyncio
import os
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["WEBSITE_SETTINGS_FILE"] = str(Path(tmp) / "settings.json")
        # Import after the env var is set because the settings module resolves its path at import time.
        from backend.admin_settings import get_settings, update_settings, reset_settings
        defaults = get_settings()
        assert defaults["verification"]["shorteners"]["1"]["name"] == ""
        updated = asyncio.run(update_settings({"verification": {"enabled": True, "verification_time_2": 120}}))
        assert updated["verification"]["enabled"] is True
        assert updated["verification"]["verification_time_2"] == 120
        assert updated["verification"]["verification_time_3"] == 0
        reset = asyncio.run(reset_settings())
        assert reset["verification"]["enabled"] is False
    print("Ultron DNA settings smoke test: PASS")


if __name__ == "__main__":
    main()
