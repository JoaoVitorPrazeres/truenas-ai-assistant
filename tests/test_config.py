import os
import unittest
from unittest.mock import patch

from ia_admin.config import Settings


class SettingsTests(unittest.TestCase):
    def test_temperature_monitor_defaults_match_safe_polling_interval(self) -> None:
        environment = {
            "TRUENAS_URL": "https://nas.example.test",
            "TRUENAS_USERNAME": "bot-user",
            "TRUENAS_API_KEY": "api-key",
            "TELEGRAM_BOT_TOKEN": "telegram-token",
            "TELEGRAM_ALLOWED_CHAT_ID": "123",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.load()

        self.assertEqual(settings.temperature_monitor_interval_minutes, 5)
        self.assertEqual(settings.disk_critical_temp_c, 50)
        self.assertEqual(settings.system_critical_temp_c, 85)

    def test_temperature_monitor_rejects_interval_under_api_cache_limit(self) -> None:
        environment = {
            "TRUENAS_URL": "https://nas.example.test",
            "TRUENAS_USERNAME": "bot-user",
            "TRUENAS_API_KEY": "api-key",
            "TELEGRAM_BOT_TOKEN": "telegram-token",
            "TELEGRAM_ALLOWED_CHAT_ID": "123",
            "TEMPERATURE_MONITOR_INTERVAL_MINUTES": "4",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(SystemExit, "pelo menos 5"):
                Settings.load()


if __name__ == "__main__":
    unittest.main()
