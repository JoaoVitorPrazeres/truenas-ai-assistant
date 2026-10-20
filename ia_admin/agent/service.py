import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ia_admin.agent.prompts import SYSTEM_PROMPT
from ia_admin.agent.tools import (
    DANGEROUS_TOOLS,
    TOOL_DEFINITIONS,
    execute_tool,
)
from ia_admin.agent.ports import LanguageModelPort, ToolFunction


log = logging.getLogger("ia-admin-telegram")
MAX_CONVERSATION_MESSAGES = 12
MAX_TOOL_CALLS = 5


@dataclass(frozen=True)
class ToolConfirmation:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentResponse:
    text: str = ""
    confirmation: ToolConfirmation | None = None


class AgentService:
    def __init__(
        self,
        allowed_chat_ids: frozenset[str],
        ollama: LanguageModelPort,
        tool_functions: dict[str, ToolFunction],
    ):
        self.allowed_chat_ids = allowed_chat_ids
        self.ollama = ollama
        self.tool_functions = tool_functions
        self.conversations: dict[int, list[dict[str, Any]]] = {}
        self.pending_confirmations: dict[int, dict[str, Any]] = {}

    def is_allowed(self, chat_id: int) -> bool:
        return str(chat_id) in self.allowed_chat_ids

    def start_conversation(self, chat_id: int) -> None:
        self.conversations[chat_id] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def reset_conversation(self, chat_id: int) -> None:
        self.conversations.pop(chat_id, None)
        self.pending_confirmations.pop(chat_id, None)

    async def answer(self, chat_id: int, text: str) -> AgentResponse:
        if chat_id in self.pending_confirmations:
            return AgentResponse(
                "Você tem uma ação pendente de confirmação. Use os botões acima."
            )

        direct_tool = direct_readonly_tool(text)
        if direct_tool:
            result = await asyncio.to_thread(
                execute_tool, direct_tool, {}, self.tool_functions
            )
            if direct_tool == "get_disk_temperatures" and result == []:
                result = "O TrueNAS não forneceu temperaturas de discos ou sensores."
            return AgentResponse(format_direct_result(direct_tool, result))

        messages = self.conversations.setdefault(
            chat_id, [{"role": "system", "content": SYSTEM_PROMPT}]
        )
        messages.append({"role": "user", "content": text})
        return await self._process_turn(chat_id, messages)

    async def confirm(self, chat_id: int, approved: bool) -> AgentResponse:
        pending = self.pending_confirmations.pop(chat_id, None)
        if not pending:
            return AgentResponse("Nada pendente para confirmar.")

        messages = pending["messages"]
        name = pending["tool_name"]
        arguments = pending["tool_args"]
        if approved:
            result = await asyncio.to_thread(
                execute_tool, name, arguments, self.tool_functions
            )
        else:
            result = {"error": "Ação cancelada pelo usuário."}

        messages.append({
            "role": "tool",
            "content": json.dumps(result, ensure_ascii=False),
        })
        response = await self._process_turn(chat_id, messages)
        return AgentResponse(
            text=response.text,
            confirmation=response.confirmation,
        )

    async def _process_turn(
        self, chat_id: int, messages: list[dict[str, Any]]
    ) -> AgentResponse:
        for _ in range(MAX_TOOL_CALLS):
            try:
                reply = await asyncio.to_thread(
                    self.ollama.chat, messages, TOOL_DEFINITIONS
                )
            except Exception as exc:
                return AgentResponse(
                    f"Não foi possível consultar o modelo local. Erro: {exc}"
                )

            messages.append(reply)
            tool_calls = reply.get("tool_calls")
            if not tool_calls:
                self.conversations[chat_id] = trim_conversation(messages)
                return AgentResponse(reply.get("content", "").strip() or "(sem resposta)")

            for tool_call in tool_calls:
                try:
                    name = tool_call["function"]["name"]
                    arguments = tool_call["function"].get("arguments", {}) or {}
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("Os argumentos da ferramenta não são um objeto.")
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    messages.append({
                        "role": "tool",
                        "content": json.dumps({
                            "error": f"Falha ao interpretar chamada da ferramenta: {exc}"
                        }, ensure_ascii=False),
                    })
                    continue

                if name in DANGEROUS_TOOLS:
                    self.pending_confirmations[chat_id] = {
                        "messages": messages,
                        "tool_name": name,
                        "tool_args": arguments,
                    }
                    self.conversations[chat_id] = trim_conversation(messages)
                    return AgentResponse(
                        confirmation=ToolConfirmation(name, arguments)
                    )

                result = await asyncio.to_thread(
                    execute_tool, name, arguments, self.tool_functions
                )
                messages.append({
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False),
                })

        self.conversations[chat_id] = trim_conversation(messages)
        return AgentResponse(
            "Limite de chamadas de ferramenta atingido. Tente reformular a pergunta."
        )

def direct_readonly_tool(text: str) -> str | None:
    normalized = text.casefold()
    if re.search(r"\b(dataset|datasets)\b", normalized) and re.search(
        r"\b(criar|crie|adicionar|adicione|novo|nova)\b", normalized
    ):
        return None
    if re.search(r"\b(pool|pools|armazenamento|espaço em disco)\b", normalized):
        return "get_pools"
    if re.search(r"\b(dataset|datasets)\b", normalized):
        return "get_datasets"
    if re.search(r"\b(cpu|processador|ram|mem[oó]ria)\b", normalized) and re.search(
        r"\b(uso|utiliza[cç][aã]o|consumo|ocupad[ao])\b", normalized
    ):
        return "get_resource_usage"
    if re.search(r"temperatura", normalized) and re.search(
        r"\b(disco|discos|sensor|sensores|servidor)\b", normalized
    ):
        return "get_disk_temperatures"
    if re.search(r"\b(rede|redes|interface|interfaces|ip|endere[cç]o)", normalized):
        return "get_network_interfaces"
    return None


def format_direct_result(tool_name: str, result: Any) -> str:
    if tool_name == "get_resource_usage" and isinstance(result, dict):
        cpu = result.get("cpu_percent")
        available = result.get("memory_available_gib")
        memory = result.get("memory_metric_gib")
        if available is not None:
            memory_text = f"RAM disponível (média): {available:.2f} GiB"
        elif memory is not None:
            memory_text = f"RAM (métrica retornada): {memory:.2f} GiB"
        else:
            memory_text = "RAM: métrica indisponível"
        if isinstance(cpu, (int, float)):
            return f"Uso atual:\n- CPU: {cpu:.2f}%\n- {memory_text}"
    if tool_name == "get_network_interfaces" and isinstance(result, list):
        if not result:
            return "O TrueNAS não retornou interfaces de rede."
        lines = ["Interfaces de rede:"]
        for interface in result:
            name = interface.get("name") or "(sem nome)"
            state = interface.get("state") or "estado desconhecido"
            addresses = interface.get("addresses") or []
            lines.append(f"- {name}: {state}")
            if addresses:
                lines.extend(f"  - {address}" for address in addresses)
            else:
                lines.append("  - sem endereços")
        return "\n".join(lines)
    return json.dumps(result, ensure_ascii=False, indent=2)


def trim_conversation(
    messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(messages) <= MAX_CONVERSATION_MESSAGES:
        return messages
    first = [messages[0]] if messages else []
    return first + messages[-MAX_CONVERSATION_MESSAGES:]
