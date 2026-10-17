from typing import Any

from ia_admin.config import Settings
from ia_admin.infrastructure.http_client import ExternalServiceError, request_json


class OllamaClient:
    def __init__(self, settings: Settings):
        self.host = settings.ollama_host
        self.model = settings.ollama_model

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        data = request_json(
            "POST",
            f"{self.host}/api/chat",
            "Ollama",
            120,
            json={
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "think": False,
            },
        )
        message = data.get("message")
        if not isinstance(message, dict):
            raise ExternalServiceError("Ollama retornou uma resposta sem o campo message")
        return message
