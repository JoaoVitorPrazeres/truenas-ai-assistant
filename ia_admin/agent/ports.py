from collections.abc import Callable
from typing import Any, Protocol


class LanguageModelPort(Protocol):
    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


ToolFunction = Callable[..., Any]
