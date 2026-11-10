import unittest

from ia_admin.config import Settings
from ia_admin.interfaces.temperature_monitor import TemperatureMonitor


def make_settings() -> Settings:
    return Settings(
        truenas_url="https://nas.example.test",
        truenas_username="bot-user",
        truenas_api_key="api-key",
        truenas_ca_bundle="",
        ollama_model="qwen3:1.7b",
        ollama_host="http://127.0.0.1:11434",
        telegram_bot_token="telegram-token",
        allowed_chat_ids=frozenset({"123", "456"}),
    )


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class TemperatureMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifies_on_critical_transition_and_recovery(self) -> None:
        readings = {
            "get_disk_temperatures": [
                {
                    "name": "sda",
                    "temperature": 51,
                    "unit": "C",
                    "critical_temperature": 60,
                },
            ],
            "get_system_temperatures": [
                {"name": "Core 0", "temperature": 86, "unit": "C"},
            ],
        }
        monitor = TemperatureMonitor(
            make_settings(),
            {
                name: (lambda key=name, **_: readings[key])
                for name in readings
            },
        )
        bot = FakeBot()

        await monitor.check_once(bot)

        self.assertEqual(len(bot.messages), 4)
        self.assertEqual(
            {chat_id for chat_id, _ in bot.messages},
            {123, 456},
        )
        self.assertTrue(all("Temperatura crítica" in text for _, text in bot.messages))
        self.assertTrue(any("sda" in text for _, text in bot.messages))
        self.assertTrue(any("Core 0" in text for _, text in bot.messages))

        await monitor.check_once(bot)
        self.assertEqual(len(bot.messages), 4)

        readings["get_disk_temperatures"] = [{
            "name": "sda",
            "temperature": 40,
            "unit": "C",
        }]
        readings["get_system_temperatures"] = [{
            "name": "Core 0",
            "temperature": 65,
            "unit": "C",
        }]
        await monitor.check_once(bot)

        self.assertEqual(len(bot.messages), 8)
        self.assertTrue(all("Temperatura normalizada" in text for _, text in bot.messages[4:]))

    async def test_uses_reported_disk_critical_threshold_when_lower(self) -> None:
        bot = FakeBot()
        monitor = TemperatureMonitor(
            make_settings(),
            {
                "get_disk_temperatures": lambda: [{
                    "name": "nvme0n1",
                    "temperature": 46,
                    "critical_temperature": 45,
                }],
                "get_system_temperatures": lambda: [],
            },
        )

        await monitor.check_once(bot)

        self.assertEqual(len(bot.messages), 2)
        self.assertTrue(all("limite: 45.0 °C" in text for _, text in bot.messages))

    async def test_missing_sensor_data_does_not_send_or_clear_critical_state(self) -> None:
        disk_readings: list[dict[str, object]] = [{
            "name": "sda",
            "temperature": 51,
        }]
        bot = FakeBot()
        monitor = TemperatureMonitor(
            make_settings(),
            {
                "get_disk_temperatures": lambda: disk_readings,
                "get_system_temperatures": lambda: [],
            },
        )

        await monitor.check_once(bot)
        disk_readings.clear()
        await monitor.check_once(bot)

        self.assertEqual(len(bot.messages), 2)
        self.assertTrue(all("Temperatura crítica" in text for _, text in bot.messages))


if __name__ == "__main__":
    unittest.main()
