import logging

from ia_admin.agent.service import AgentService
from ia_admin.config import Settings
from ia_admin.infrastructure.ollama import OllamaClient
from ia_admin.infrastructure.truenas import TrueNASClient
from ia_admin.infrastructure.truenas_tools import build_tool_functions
from ia_admin.interfaces.telegram_bot import TelegramBotAdapter
from ia_admin.logging_utils import SecretRedactionFilter


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = Settings.load()
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(SecretRedactionFilter(settings.telegram_bot_token))
    logging.getLogger("httpx").setLevel(logging.WARNING)

    truenas = TrueNASClient(settings)
    agent = AgentService(
        allowed_chat_ids=settings.allowed_chat_ids,
        ollama=OllamaClient(settings),
        tool_functions=build_tool_functions(truenas),
    )
    app = TelegramBotAdapter(settings, agent).build_application()
    logging.getLogger("ia-admin-telegram").info(
        "Bot iniciado. Aguardando mensagens..."
    )
    app.run_polling()
