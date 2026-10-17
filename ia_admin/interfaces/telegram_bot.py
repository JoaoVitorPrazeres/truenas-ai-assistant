import asyncio
import logging
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ia_admin.agent.service import AgentResponse, AgentService
from ia_admin.config import Settings


log = logging.getLogger("ia-admin-telegram")
MAX_TELEGRAM_MESSAGE_LENGTH = 4096


def normalize_telegram_text(text: Any) -> str:
    if text is None:
        return ""
    text = str(text).replace("*", "").replace("_", "").replace("`", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_telegram_text(text: str) -> list[str]:
    return [
        text[index:index + MAX_TELEGRAM_MESSAGE_LENGTH]
        for index in range(0, len(text), MAX_TELEGRAM_MESSAGE_LENGTH)
    ] or ["(sem resposta)"]


async def safe_send_message(target: Any, text: str, keyboard: Any = None) -> None:
    clean_text = normalize_telegram_text(text) or "(sem resposta)"

    async def send(part: str, reply_markup: Any = None) -> None:
        if hasattr(target, "reply_text"):
            await target.reply_text(part, reply_markup=reply_markup)
        else:
            await target.send_message(
                chat_id=target.chat_id,
                text=part,
                reply_markup=reply_markup,
            )

    try:
        for index, part in enumerate(split_telegram_text(clean_text)):
            await send(part, keyboard if index == 0 else None)
    except BadRequest as exc:
        log.warning("Telegram rejeitou a mensagem: %s", exc)
        try:
            await send(clean_text[:MAX_TELEGRAM_MESSAGE_LENGTH])
        except TelegramError:
            log.exception("Falha no fallback de envio ao Telegram")
    except TelegramError:
        log.exception("Falha de comunicação com o Telegram")
    except Exception:
        log.exception("Falha inesperada ao enviar mensagem")


class TelegramBotAdapter:
    def __init__(self, settings: Settings, agent: AgentService):
        self.settings = settings
        self.agent = agent
        self.chat_locks: dict[int, asyncio.Lock] = {}

    def build_application(self) -> Application:
        app = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            .concurrent_updates(True)
            .build()
        )
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("status", self.status_command))
        app.add_handler(CommandHandler("reset", self.reset_command))
        app.add_handler(CallbackQueryHandler(self.handle_confirmation))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        app.add_error_handler(self.handle_error)
        return app

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        return self.chat_locks.setdefault(chat_id, asyncio.Lock())

    async def _send_response(self, target: Any, response: AgentResponse) -> None:
        if response.confirmation:
            confirmation = response.confirmation
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Confirmar", callback_data="confirm"),
                InlineKeyboardButton("❌ Cancelar", callback_data="cancel"),
            ]])
            await safe_send_message(
                target,
                f"🚨 Atenção: a IA quer executar "
                f"{confirmation.name}({confirmation.arguments}). Confirma?",
                keyboard,
            )
        elif response.text:
            await safe_send_message(target, response.text)

    async def handle_error(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        error = context.error
        if error:
            log.error(
                "Erro não tratado ao processar update",
                exc_info=(type(error), error, error.__traceback__),
            )
        message = getattr(update, "effective_message", None)
        if message:
            await safe_send_message(
                message,
                "Ocorreu um erro inesperado ao processar sua solicitação. "
                "Tente novamente em instantes.",
            )

    async def start(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if not chat or not message:
            return
        if not self.agent.is_allowed(chat.id):
            await message.reply_text("Acesso não autorizado.")
            return
        self.agent.start_conversation(chat.id)
        await safe_send_message(
            message,
            f"🖥️ IA-Admin conectado ao TrueNAS ({self.settings.truenas_url}), "
            f"modelo {self.settings.ollama_model}.\n"
            "Pode perguntar sobre CPU, RAM, discos, pools, datasets, alertas, "
            "temperatura, scrub, rede, updates, apps e serviços.",
        )

    async def help_command(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if not chat or not message:
            return
        if not self.agent.is_allowed(chat.id):
            await message.reply_text("Acesso não autorizado.")
            return
        await safe_send_message(
            message,
            "Comandos disponíveis:\n"
            "- /start: iniciar o bot\n"
            "- /help: mostrar ajuda\n"
            "- /status: mostrar o estado do agente\n"
            "- /reset: limpar o histórico desta conversa\n"
            "- Mensagem normal: pergunta ao assistente sobre o TrueNAS",
        )

    async def status_command(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if not chat or not message:
            return
        if not self.agent.is_allowed(chat.id):
            await message.reply_text("Acesso não autorizado.")
            return
        count = len(self.agent.conversations.get(chat.id, []))
        allowed = ", ".join(sorted(self.settings.allowed_chat_ids))
        await safe_send_message(
            message,
            "Status do agente:\n"
            f"- Chat atual: {chat.id}\n"
            f"- Chat IDs permitidos: {allowed}\n"
            f"- Histórico atual: {count} mensagens\n"
            f"- Modelo Ollama: {self.settings.ollama_model}\n"
            f"- TrueNAS: {self.settings.truenas_url}",
        )

    async def reset_command(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if not chat or not message:
            return
        if not self.agent.is_allowed(chat.id):
            await message.reply_text("Acesso não autorizado.")
            return
        self.agent.reset_conversation(chat.id)
        await safe_send_message(message, "Histórico desta conversa foi limpo.")

    async def handle_message(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if not chat or not message or not self.agent.is_allowed(chat.id):
            if message:
                await message.reply_text("Acesso não autorizado.")
            return

        async with self._lock_for(chat.id):
            response = await self.agent.answer(chat.id, message.text or "")
            await self._send_response(message, response)

    async def handle_confirmation(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if not query or not query.message:
            return
        chat_id = query.message.chat.id
        await query.answer()
        if not self.agent.is_allowed(chat_id):
            await query.edit_message_text("Acesso não autorizado.")
            return

        async with self._lock_for(chat_id):
            approved = query.data == "confirm"
            response = await self.agent.confirm(chat_id, approved)
            await query.edit_message_text(
                "Ação autorizada." if approved else "Ação cancelada."
            )
            await self._send_response(
                _BotMessageTarget(context.bot, chat_id), response
            )


class _BotMessageTarget:
    def __init__(self, bot: Any, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id

    async def send_message(
        self, chat_id: int, text: str, reply_markup: Any = None
    ) -> None:
        await self.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )
