import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("ia-admin-telegram")
DANGEROUS_TOOLS = frozenset({"restart_service", "create_dataset"})

TOOL_DEFINITIONS = [
    {"type": "function", "function": {
        "name": "get_system_info",
        "description": "Retorna informações gerais do sistema: hostname, versão, uptime, CPU e RAM totais.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_pools",
        "description": "Lista os pools ZFS com status, espaço total, usado e livre.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_disks",
        "description": "Lista os discos físicos instalados no servidor.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_datasets",
        "description": "Lista datasets ZFS com espaço usado e disponível de cada um.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_resource_usage",
        "description": "Retorna o uso atual (tempo real) de CPU e RAM do servidor, em porcentagem.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_services",
        "description": "Lista os serviços do TrueNAS (SMB, NFS, SSH etc.) e seu estado.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_alerts",
        "description": "Lista alertas ativos do sistema (discos falhando, pool degradado, certificados expirando, etc.). Use quando o usuário perguntar se há problemas, avisos ou alertas no servidor.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_disk_temperatures",
        "description": "Retorna a temperatura atual de discos e sensores do servidor, quando disponíveis no TrueNAS.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_disk_temperature_alerts",
        "description": "Retorna alertas de temperatura dos discos. Não confundir alertas com a temperatura atual em graus Celsius.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_scrub_status",
        "description": "Status de scrub (verificação de integridade ZFS) de cada pool: se está rodando, progresso e erros encontrados.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_network_interfaces",
        "description": "Lista as interfaces de rede do servidor, seus IPs e estado do link (conectado/desconectado).",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_update_status",
        "description": "Verifica se há atualizações de sistema do TrueNAS disponíveis para instalar.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_apps",
        "description": "Lista os Apps instalados no TrueNAS (Ollama, Tailscale, etc.) e seu estado (rodando/parado).",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "restart_service",
        "description": "Reinicia um serviço do TrueNAS. AÇÃO PERIGOSA: exige confirmação do usuário.",
        "parameters": {
            "type": "object",
            "properties": {
                "service_name": {"type": "string", "description": "Ex: 'cifs' para SMB, 'ssh', 'nfs'."}
            },
            "required": ["service_name"],
        },
    }},
    {"type": "function", "function": {
        "name": "create_dataset",
        "description": "Cria um novo dataset ZFS dentro de um pool. AÇÃO PERIGOSA: exige confirmação do usuário.",
        "parameters": {
            "type": "object",
            "properties": {
                "pool_name": {"type": "string", "description": "Ex: 'tank'"},
                "dataset_name": {"type": "string", "description": "Ex: 'backups'"},
            },
            "required": ["pool_name", "dataset_name"],
        },
    }},
]


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    functions: dict[str, Callable[..., Any]],
) -> Any:
    if name not in functions:
        message = f"Ferramenta desconhecida: {name}"
        log.error(message)
        return {"error": message}
    try:
        return functions[name](**arguments)
    except Exception as exc:
        log.exception("Falha ao executar a ferramenta %s", name)
        return {"error": f"Falha na ferramenta {name}: {exc}"}
