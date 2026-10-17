import time
from typing import Any

import requests


HTTP_RETRIES = 2
RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


class ExternalServiceError(RuntimeError):
    """Indica uma falha conhecida ao acessar um serviço externo."""


def request_json(method: str, url: str, service: str, timeout: int, **kwargs: Any) -> Any:
    for attempt in range(HTTP_RETRIES + 1):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            if attempt >= HTTP_RETRIES:
                raise ExternalServiceError(
                    f"{service} indisponível após {HTTP_RETRIES + 1} tentativas: {exc}"
                ) from exc
            time.sleep(2 ** attempt)
            continue

        if response.status_code in RETRYABLE_HTTP_STATUS and attempt < HTTP_RETRIES:
            time.sleep(2 ** attempt)
            continue

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text.strip()[:300]
            message = f"{service} respondeu HTTP {response.status_code}"
            if detail:
                message += f": {detail}"
            raise ExternalServiceError(message) from exc

        if not response.text:
            return {}

        try:
            return response.json()
        except ValueError as exc:
            raise ExternalServiceError(
                f"{service} retornou uma resposta JSON inválida"
            ) from exc

    raise ExternalServiceError(f"Falha inesperada ao acessar {service}")
