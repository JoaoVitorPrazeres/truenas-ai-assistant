import asyncio
import json
import logging
import re
from collections.abc import Callable
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
SERVER_OVERVIEW_TOOLS = (
    "get_system_info",
    "get_resource_usage",
    "get_network_interfaces",
    "get_network_configuration",
    "get_services",
    "get_pools",
    "get_disks",
    "get_disk_temperatures",
    "get_alerts",
    "get_scrub_status",
    "get_apps",
)


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

        if is_server_overview_request(text):
            combined = {}
            for tool_name in SERVER_OVERVIEW_TOOLS:
                result = await asyncio.to_thread(
                    execute_tool, tool_name, {}, self.tool_functions
                )
                combined[tool_name] = result
            content = format_server_overview(combined)
            messages = self.conversations.setdefault(
                chat_id, [{"role": "system", "content": SYSTEM_PROMPT}]
            )
            messages.append({"role": "user", "content": text})
            messages.append({"role": "assistant", "content": content})
            self.conversations[chat_id] = trim_conversation(messages)
            return AgentResponse(content)

        direct_tool = direct_readonly_tool(text)
        if direct_tool:
            result = await asyncio.to_thread(
                execute_tool, direct_tool, {}, self.tool_functions
            )
            if direct_tool == "get_disk_temperatures" and result == []:
                result = "O TrueNAS não forneceu temperaturas de discos ou sensores."
            if direct_tool == "get_system_info" and isinstance(result, dict):
                result = {
                    **result,
                    "network_interfaces": await asyncio.to_thread(
                        execute_tool,
                        "get_network_interfaces",
                        {},
                        self.tool_functions,
                    ),
                }
            if direct_tool == "get_resource_usage" and isinstance(result, dict):
                system_info = await asyncio.to_thread(
                    execute_tool,
                    "get_system_info",
                    {},
                    self.tool_functions,
                )
                if isinstance(system_info, dict) and not system_info.get("error"):
                    result = {
                        **result,
                        "memory_total_bytes": (
                            system_info.get("memory_total_bytes")
                            or system_info.get("memory_bytes")
                        ),
                        "memory_total_human": (
                            system_info.get("memory_total_human")
                            or system_info.get("memory_human")
                        ),
                    }
            if direct_tool in {
                "get_network_interfaces",
                "get_system_info",
                "get_resource_usage",
            }:
                return AgentResponse(format_direct_result(direct_tool, result))
            messages = self.conversations.setdefault(
                chat_id, [{"role": "system", "content": SYSTEM_PROMPT}]
            )
            messages.append({"role": "user", "content": text})
            messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False),
            })
            try:
                reply = await asyncio.to_thread(
                    self.ollama.chat, messages, []
                )
            except Exception as exc:
                return AgentResponse(
                    f"Não foi possível formatar os dados pelo Ollama. Erro: {exc}"
                )
            content = reply.get("content", "").strip()
            messages.append({"role": "assistant", "content": content})
            self.conversations[chat_id] = trim_conversation(messages)
            return AgentResponse(
                content or "O Ollama não conseguiu gerar uma resposta."
            )

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

def is_server_overview_request(text: str) -> bool:
    normalized = text.casefold()
    server_context = re.search(r"\b(servidor|sistema|truenas|nas)\b", normalized)
    if not server_context:
        return False
    return bool(re.search(
        r"\b(resumo|estado geral|situa[cç][aã]o geral|diagn[oó]stico|"
        r"tudo sobre|todas?\s+(?:as\s+)?informa(?:ções|coes)|"
        r"vis[aã]o (?:geral|completa))\b",
        normalized,
    ))


def direct_readonly_tool(text: str) -> str | None:
    normalized = text.casefold()
    if re.search(r"\b(dataset|datasets)\b", normalized) and re.search(
        r"\b(criar|crie|adicionar|adicione|novo|nova)\b", normalized
    ):
        return None
    if re.search(
        r"\b(ip(?:v4|v6)?|endere[cç]o(?:s)? ip(?:v4|v6)?|"
        r"endere[cç]o(?:s)?(?: da)? rede)\b",
        normalized,
    ):
        if re.search(r"\bgateway|dns|servidor dns", normalized):
            return "get_network_configuration"
        return "get_network_interfaces"
    if re.search(r"\b(pool|pools|armazenamento|espaço em disco)\b", normalized):
        return "get_pools"
    if re.search(r"\b(dataset|datasets)\b", normalized):
        return "get_datasets"
    if re.search(r"\bsnapshot|snapshots\b", normalized):
        return "get_snapshots"
    if re.search(r"\b(cpu|processador|ram|mem[oó]ria)\b", normalized) and re.search(
        r"\b(uso|utiliza[cç][aã]o|consumo|ocupad[ao])\b", normalized
    ):
        return "get_resource_usage"
    if re.search(r"\b(sistema|servidor|hostname|vers[aã]o|uptime|tempo ligado)\b", normalized):
        return "get_system_info"
    if re.search(r"\b(disco|discos|hd|ssd)\b", normalized):
        if re.search(r"\b(sa[uú]de|problema|falhando|health)\b", normalized):
            return "get_alerts"
        return "get_disks"
    if re.search(r"temperatura", normalized) and re.search(
        r"\b(disco|discos|sensor|sensores|servidor)\b", normalized
    ):
        return "get_disk_temperatures"
    if re.search(r"\b(alerta|alertas|problemas|avisos)\b", normalized):
        return "get_alerts"
    if re.search(r"\b(gateway|dns|servidores dns)\b", normalized):
        return "get_network_configuration"
    if re.search(r"\b(rede|redes|interface|interfaces|ip|endere[cç]o)", normalized):
        return "get_network_interfaces"
    if re.search(r"\b(aplica[cç][aã]o|aplica[cç][õo]es|apps?|containers?|docker)\b", normalized):
        if re.search(r"\b(porta|portas)\b", normalized):
            return "get_app_ports"
        return "get_apps"
    if re.search(r"\b(servi[cç]os?|servi[cç]os ativos|ports?|portas utilizadas)\b", normalized):
        if re.search(r"\b(porta|portas)\b", normalized):
            return "get_app_ports"
        return "get_services"
    if re.search(r"\b(tarefas|jobs?|hist[oó]rico)\b", normalized):
        return "get_task_history"
    if re.search(r"\b(atualiza[cç][aã]o|atualiza[cç][õo]es)\b", normalized):
        return "get_update_status"
    if re.search(r"\bscrub\b", normalized):
        return "get_scrub_status"
    return None


def format_direct_result(tool_name: str, result: Any) -> str:
    if tool_name == "get_resource_usage" and isinstance(result, dict):
        if result.get("error"):
            return str(result["error"])
        cpu = result.get("cpu_percent")
        available = result.get("memory_available_gib")
        total_bytes = result.get("memory_total_bytes")
        available_bytes = result.get("memory_available_bytes")
        if (
            isinstance(total_bytes, (int, float))
            and isinstance(available_bytes, (int, float))
            and total_bytes > 0
            and 0 <= available_bytes <= total_bytes
        ):
            used_bytes = total_bytes - available_bytes
            used_percent = used_bytes / total_bytes * 100
            memory_lines = [
                f"- RAM utilizada estimada: {_format_gib(used_bytes)} "
                f"({_format_percent(used_percent)})",
                f"- RAM disponível (média): {_format_gib(available_bytes)}",
                f"- RAM total: {result.get('memory_total_human') or _format_gib(total_bytes)}",
            ]
        elif available is not None:
            memory_lines = [f"- RAM disponível (média): {available:.2f} GiB"]
        elif result.get("memory_metric_gib") is not None:
            memory_lines = [
                f"- RAM (métrica retornada): "
                f"{result['memory_metric_gib']:.2f} GiB"
            ]
        else:
            memory_lines = ["- RAM: métrica indisponível"]
        cpu_text = (
            f"{_format_percent(cpu)} (média no período consultado)"
            if isinstance(cpu, (int, float))
            else "métrica indisponível"
        )
        return "\n".join(["📊 Uso de Recursos:", f"- CPU: {cpu_text}", *memory_lines])
    if tool_name == "get_system_info" and isinstance(result, dict):
        if result.get("error"):
            return str(result["error"])
        fields = (
            ("Hostname", result.get("hostname")),
            ("Versão", result.get("version")),
            ("Idade do servidor", result.get("uptime_human")),
            ("Modelo de CPU", result.get("cpu_model")),
            ("Núcleos de CPU", result.get("cpu_cores")),
            ("Clock da CPU", (
                f"{result['cpu_clock_mhz']} MHz"
                if result.get("cpu_clock_mhz") is not None else None
            )),
            ("Memória total", result.get("memory_total_human") or result.get("memory_human")),
            ("Memória ECC", result.get("ecc_memory")),
            ("Fabricante", result.get("system_manufacturer")),
            ("Produto", result.get("system_product_name")),
            ("Número de série", result.get("system_serial_number")),
        )
        lines = ["📅 Informações do Servidor:"]
        lines.extend(
            f"- {label}: {value if value is not None else 'Não informado'}"
            for label, value in fields
        )
        load_average = result.get("load_average")
        if isinstance(load_average, list) and len(load_average) >= 3:
            lines.append(
                "- Load average (1/5/15 min): "
                + " / ".join(str(value) for value in load_average[:3])
            )
        interfaces = result.get("network_interfaces")
        if interfaces is not None:
            lines.extend(["", format_direct_result("get_network_interfaces", interfaces)])
        return "\n".join(lines)
    if tool_name == "get_network_interfaces" and isinstance(result, list):
        if not result:
            return "O TrueNAS não retornou interfaces de rede."
        lines = ["Interfaces de rede:"]
        for interface in result:
            name = interface.get("name") or "(sem nome)"
            description = interface.get("description") or ""
            state = interface.get("state") or "estado desconhecido"
            addresses = interface.get("addresses") or []
            interface_label = "Tailscale" if "tailscale" in str(name).lower() or "tailscale" in str(description).lower() else "Rede"
            lines.append(f"- {name}: {state} ({interface_label})")
            if addresses:
                lines.extend(f"  - {address}" for address in addresses)
            else:
                lines.append("  - sem endereços")
        return "\n".join(lines)
    if tool_name == "get_network_interfaces" and isinstance(result, dict):
        if result.get("error"):
            return str(result["error"])
    return json.dumps(result, ensure_ascii=False, indent=2)


def format_server_overview(results: dict[str, Any]) -> str:
    system_info = results.get("get_system_info")
    resource_usage = results.get("get_resource_usage")
    if isinstance(system_info, dict) and isinstance(resource_usage, dict):
        resource_usage = {
            **resource_usage,
            "memory_total_bytes": (
                system_info.get("memory_total_bytes")
                or system_info.get("memory_bytes")
            ),
            "memory_total_human": (
                system_info.get("memory_total_human")
                or system_info.get("memory_human")
            ),
        }

    lines = [format_direct_result("get_system_info", system_info), ""]
    lines.extend(format_direct_result("get_resource_usage", resource_usage).splitlines())
    lines.extend(["", "🌐 Rede e Interfaces:"])
    lines.extend(
        format_direct_result(
            "get_network_interfaces",
            results.get("get_network_interfaces"),
        ).splitlines()
    )
    network_configuration = results.get("get_network_configuration")
    if isinstance(network_configuration, dict) and network_configuration.get("error"):
        lines.append(f"- Falha ao consultar gateway/DNS: {network_configuration['error']}")
    elif isinstance(network_configuration, dict):
        gateway4 = network_configuration.get("ipv4_gateway")
        gateway6 = network_configuration.get("ipv6_gateway")
        nameservers = network_configuration.get("nameservers")
        if nameservers is None:
            nameservers = [
                network_configuration.get(f"nameserver{index}")
                for index in range(1, 4)
                if network_configuration.get(f"nameserver{index}")
            ]
        if isinstance(nameservers, str):
            nameservers = [nameservers]
        if gateway4:
            lines.append(f"- Gateway IPv4: {gateway4}")
        if gateway6:
            lines.append(f"- Gateway IPv6: {gateway6}")
        if isinstance(nameservers, list) and nameservers:
            lines.append(f"- DNS: {', '.join(str(item) for item in nameservers)}")
        if not gateway4 and not gateway6 and not nameservers:
            lines.append("- Gateway e DNS não informados.")
    lines.extend(["", "⚙️ Serviços:"])
    services = results.get("get_services")
    if isinstance(services, list) and services:
        lines.extend(
            f"- {item.get('service') or 'Serviço sem nome'}: "
            f"{item.get('state') or 'estado não informado'}"
            for item in services
            if isinstance(item, dict)
        )
    elif isinstance(services, dict) and services.get("error"):
        lines.append(f"- Falha: {services['error']}")
    else:
        lines.append("- Nenhum serviço foi informado.")

    _append_list_section(
        lines,
        "💾 Pools:",
        results.get("get_pools"),
        lambda item: (
            f"{item.get('name') or 'Pool sem nome'}: "
            f"{item.get('status') or 'estado não informado'}"
            + (
                f", livre {item['free_human']}"
                if item.get("free_human") is not None else ""
            )
        ),
        "Nenhum pool foi informado.",
    )
    _append_list_section(
        lines,
        "💿 Discos:",
        results.get("get_disks"),
        lambda item: (
            f"{item.get('name') or 'Disco sem nome'}"
            + (f" — {item['model']}" if item.get("model") else "")
            + (f" — {item['status']}" if item.get("status") else "")
        ),
        "Nenhum disco foi informado.",
    )
    _append_list_section(
        lines,
        "🌡️ Temperaturas:",
        results.get("get_disk_temperatures"),
        lambda item: (
            f"{item.get('name') or 'Sensor sem nome'}: "
            f"{item.get('temperature')} °{item.get('unit') or 'C'}"
        ),
        "Temperaturas não informadas pelo TrueNAS.",
    )
    _append_list_section(
        lines,
        "🚨 Alertas:",
        results.get("get_alerts"),
        lambda item: (
            f"{item.get('level') or 'Alerta'}: "
            f"{item.get('text') or 'descrição não informada'}"
        ),
        "Nenhum alerta ativo foi detectado.",
    )

    scrubs = results.get("get_scrub_status")
    active_scrubs = (
        [
            item for item in scrubs
            if isinstance(item, dict)
            and item.get("scan_function")
            and str(item.get("scan_state") or "").upper()
            not in {"FINISHED", "CANCELED", "CANCELLED", "ERROR", "NONE"}
        ]
        if isinstance(scrubs, list)
        else scrubs
    )
    _append_list_section(
        lines,
        "🔄 Scrub:",
        active_scrubs,
        lambda item: (
            f"{item.get('pool') or 'Pool sem nome'}: "
            f"{item.get('scan_function')} — {item.get('scan_state') or 'estado desconhecido'}"
            + (
                f" ({item['percentage']}%)"
                if item.get("percentage") is not None else ""
            )
        ),
        "Nenhum scrub em andamento.",
    )
    _append_list_section(
        lines,
        "📦 Aplicativos:",
        results.get("get_apps"),
        lambda item: (
            f"{item.get('name') or 'App sem nome'}: "
            f"{item.get('state') or 'estado não informado'}"
        ),
        "Nenhum aplicativo foi informado.",
    )
    return "\n".join(lines)


def _append_list_section(
    lines: list[str],
    title: str,
    result: Any,
    render_item: Callable[[dict[str, Any]], str],
    empty_message: str,
) -> None:
    lines.extend(["", title])
    if isinstance(result, dict) and result.get("error"):
        lines.append(f"- Falha: {result['error']}")
    elif isinstance(result, list) and result:
        lines.extend(
            f"- {render_item(item)}"
            for item in result
            if isinstance(item, dict)
        )
    else:
        lines.append(f"- {empty_message}")


def _format_gib(value: int | float) -> str:
    return f"{value / (1024 ** 3):.2f} GiB"


def _format_percent(value: int | float) -> str:
    return f"{value:.2f}%"


def trim_conversation(
    messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(messages) <= MAX_CONVERSATION_MESSAGES:
        return messages
    first = [messages[0]] if messages else []
    return first + messages[-MAX_CONVERSATION_MESSAGES:]
