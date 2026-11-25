import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ia_admin.agent.service import AgentService
from ia_admin.config import Settings
from ia_admin.infrastructure.http_client import ExternalServiceError
from ia_admin.interfaces.telegram_bot import TelegramBotAdapter


def make_adapter(
    shutdown_server: Mock | None = None,
) -> TelegramBotAdapter:
    settings = Settings(
        truenas_url="https://nas.example.test",
        truenas_username="bot-user",
        truenas_api_key="api-key",
        truenas_ca_bundle="",
        ollama_model="qwen3:1.7b",
        ollama_host="http://127.0.0.1:11434",
        telegram_bot_token="telegram-token",
        allowed_chat_ids=frozenset({"123"}),
    )
    agent = AgentService(
        allowed_chat_ids=settings.allowed_chat_ids,
        ollama=Mock(),
        tool_functions={},
    )
    return TelegramBotAdapter(settings, agent, shutdown_server or Mock())


class TelegramShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_command_requires_authorized_chat(self) -> None:
        adapter = make_adapter()
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=456),
            effective_message=message,
        )

        await adapter.shutdown_command(update, Mock())

        message.reply_text.assert_awaited_once_with("Acesso não autorizado.")
        self.assertEqual(adapter.pending_shutdowns, {})

    async def test_shutdown_command_sends_explicit_confirmation(self) -> None:
        adapter = make_adapter()
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=123),
            effective_message=message,
        )

        await adapter.shutdown_command(update, Mock())

        text = message.reply_text.await_args.args[0]
        keyboard = message.reply_text.await_args.kwargs["reply_markup"]
        self.assertIn("host TrueNAS inteiro", text)
        self.assertIn("indisponíveis", text)
        buttons = keyboard.inline_keyboard[0]
        self.assertTrue(buttons[0].callback_data.startswith("shutdown:confirm:"))
        self.assertTrue(buttons[1].callback_data.startswith("shutdown:cancel:"))

    async def test_cancelled_shutdown_does_not_call_server(self) -> None:
        shutdown_server = Mock()
        adapter = make_adapter(shutdown_server)
        adapter.pending_shutdowns[123] = "deadbeef"
        query = self.make_query("shutdown:cancel:deadbeef")

        await adapter.handle_shutdown_confirmation(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(bot=Mock()),
        )

        shutdown_server.assert_not_called()
        query.edit_message_text.assert_awaited_once_with("Desligamento cancelado.")
        self.assertNotIn(123, adapter.pending_shutdowns)

    async def test_confirmation_calls_shutdown_only_for_matching_request(self) -> None:
        shutdown_server = Mock()
        adapter = make_adapter(shutdown_server)
        adapter.pending_shutdowns[123] = "deadbeef"
        query = self.make_query("shutdown:confirm:deadbeef")

        await adapter.handle_shutdown_confirmation(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(bot=Mock()),
        )

        shutdown_server.assert_called_once_with()
        query.edit_message_text.assert_awaited_once()
        self.assertIn("Enviando", query.edit_message_text.await_args.args[0])
        self.assertNotIn(123, adapter.pending_shutdowns)

    async def test_expired_confirmation_does_not_call_server(self) -> None:
        shutdown_server = Mock()
        adapter = make_adapter(shutdown_server)
        adapter.pending_shutdowns[123] = "aabbccdd"
        query = self.make_query("shutdown:confirm:deadbeef")

        await adapter.handle_shutdown_confirmation(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(bot=Mock()),
        )

        shutdown_server.assert_not_called()
        query.edit_message_text.assert_awaited_once_with(
            "Esta confirmação expirou ou já foi processada."
        )

    async def test_shutdown_api_failure_is_reported_to_chat(self) -> None:
        shutdown_server = Mock(
            side_effect=ExternalServiceError("permissão insuficiente")
        )
        adapter = make_adapter(shutdown_server)
        adapter.pending_shutdowns[123] = "deadbeef"
        query = self.make_query("shutdown:confirm:deadbeef")
        bot = SimpleNamespace(send_message=AsyncMock())

        with self.assertLogs("ia-admin-telegram", level="ERROR"):
            await adapter.handle_shutdown_confirmation(
                SimpleNamespace(callback_query=query),
                SimpleNamespace(bot=bot),
            )

        bot.send_message.assert_awaited_once()
        self.assertIn(
            "permissão insuficiente",
            bot.send_message.await_args.kwargs["text"],
        )

    @staticmethod
    def make_query(data: str) -> SimpleNamespace:
        return SimpleNamespace(
            data=data,
            message=SimpleNamespace(chat=SimpleNamespace(id=123)),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )


if __name__ == "__main__":
    unittest.main()
