import json
import ssl
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websocket

from ia_admin.config import Settings
from ia_admin.infrastructure.http_client import ExternalServiceError


def bytes_to_human(value: int | float | None) -> str | None:
    if value is None:
        return None
    tb = float(value) / (1024 ** 4)
    if tb >= 1:
        return f"{round(tb, 2)} TB"
    gb = float(value) / (1024 ** 3)
    return f"{round(gb, 2)} GB"


class TrueNASClient:
    def __init__(self, settings: Settings):
        parsed_url = urlsplit(settings.truenas_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ExternalServiceError(
                "TRUENAS_URL deve ser uma URL HTTP ou HTTPS válida"
            )
        scheme = "wss" if parsed_url.scheme == "https" else "ws"
        self.websocket_url = urlunsplit(
            (scheme, parsed_url.netloc, "/api/current", "", "")
        )
        self.username = settings.truenas_username
        self.api_key = settings.truenas_api_key
        self.ssl_options: dict[str, Any] = {
            "cert_reqs": ssl.CERT_REQUIRED,
            "check_hostname": True,
        }
        if settings.truenas_ca_bundle:
            self.ssl_options["ca_certs"] = settings.truenas_ca_bundle

    @staticmethod
    def _receive_result(
        connection: websocket.WebSocket, request_id: int, method: str
    ) -> Any:
        try:
            response = json.loads(connection.recv())
        except json.JSONDecodeError as exc:
            raise ExternalServiceError(
                f"TrueNAS retornou uma resposta JSON inválida para {method}"
            ) from exc

        if not isinstance(response, dict) or response.get("id") != request_id:
            raise ExternalServiceError(
                f"TrueNAS retornou uma resposta inesperada para {method}"
            )
        if "error" in response:
            error = response["error"]
            message = "erro JSON-RPC"
            if isinstance(error, dict):
                message = error.get("message") or message
                details = error.get("data")
                if isinstance(details, dict):
                    message = details.get("reason") or message
            raise ExternalServiceError(f"TrueNAS falhou em {method}: {message}")
        if "result" not in response:
            raise ExternalServiceError(
                f"TrueNAS não retornou resultado para {method}"
            )
        return response["result"]

    def _rpc(self, method: str, params: list[Any] | None = None) -> Any:
        connection: websocket.WebSocket | None = None
        try:
            connection = websocket.create_connection(
                self.websocket_url,
                timeout=30,
                sslopt=self.ssl_options,
            )
            connection.send(json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "auth.login_ex",
                "params": [{
                    "mechanism": "API_KEY_PLAIN",
                    "username": self.username,
                    "api_key": self.api_key,
                }],
            }))
            authentication = self._receive_result(
                connection, 1, "auth.login_ex"
            )
            if (
                not isinstance(authentication, dict)
                or authentication.get("response_type") != "SUCCESS"
            ):
                response_type = (
                    authentication.get("response_type")
                    if isinstance(authentication, dict)
                    else "resposta inválida"
                )
                raise ExternalServiceError(
                    f"Autenticação no TrueNAS falhou ({response_type}); "
                    "confira TRUENAS_USERNAME e TRUENAS_API_KEY"
                )

            connection.send(json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "method": method,
                "params": params or [],
            }))
            return self._receive_result(connection, 2, method)
        except (websocket.WebSocketException, OSError, TimeoutError) as exc:
            raise ExternalServiceError(
                f"Não foi possível conectar à API WebSocket do TrueNAS: {exc}"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def get_system_info(self) -> Any:
        return self._rpc("system.info")

    def get_pools(self) -> list[dict[str, Any]]:
        pools = self._rpc("pool.query")
        result = []
        for pool in pools:
            size = pool.get("size") or 0
            allocated = pool.get("allocated") or 0
            result.append({
                "name": pool.get("name"),
                "status": pool.get("status"),
                "size_bytes": size,
                "size_human": bytes_to_human(size),
                "allocated_bytes": allocated,
                "allocated_human": bytes_to_human(allocated),
                "free_bytes": pool.get("free"),
                "free_human": bytes_to_human(pool.get("free")),
                "percent_used": round((allocated / size) * 100, 1) if size else None,
                "fragmentation": pool.get("fragmentation"),
            })
        return result

    def get_datasets(self) -> list[dict[str, Any]]:
        datasets = self._rpc("pool.dataset.query")

        def flatten(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            result = []
            for dataset in items:
                used = (dataset.get("used") or {}).get("parsed")
                available = (dataset.get("available") or {}).get("parsed")
                result.append({
                    "name": dataset.get("name"),
                    "used_bytes": used,
                    "used_human": bytes_to_human(used),
                    "available_bytes": available,
                    "available_human": bytes_to_human(available),
                    "mountpoint": dataset.get("mountpoint"),
                })
                if dataset.get("children"):
                    result.extend(flatten(dataset["children"]))
            return result

        return flatten(datasets)

    def get_resource_usage(self) -> dict[str, Any]:
        data = self._rpc(
            "reporting.get_data",
            [[{"name": "cpu"}, {"name": "memory"}], {
                "unit": "HOUR",
                "page": 1,
                "aggregate": True,
            }],
        )
        if not isinstance(data, list):
            raise ExternalServiceError(
                "TrueNAS retornou um formato inesperado para uso de recursos"
            )

        summary = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            samples = entry.get("data")
            if isinstance(samples, list) and samples:
                aggregations = samples[-1].get("aggregations") or {}
            else:
                aggregations = entry.get("aggregations") or {}
            mean = aggregations.get("mean")
            if isinstance(mean, dict):
                mean_key, mean = next(iter(mean.items()), (None, None))
            elif isinstance(mean, list) and mean:
                mean_key, mean = None, mean[-1]
            else:
                mean_key = None

            name = entry.get("name")
            if name == "memory" and isinstance(mean, (int, float)) and mean > 100:
                gib = round(mean / (1024 ** 3), 2)
                if str(mean_key).casefold() == "available":
                    summary["memory_available_bytes"] = mean
                    summary["memory_available_gib"] = gib
                else:
                    summary["memory_metric_bytes"] = mean
                    summary["memory_metric_gib"] = gib
            elif name == "cpu":
                summary["cpu_percent"] = round(float(mean), 2) if mean is not None else None
            else:
                summary[name] = mean
        return summary

    def get_disks(self) -> list[dict[str, Any]]:
        return [
            {
                "name": disk.get("name"),
                "model": disk.get("model"),
                "size_bytes": disk.get("size"),
                "size_human": bytes_to_human(disk.get("size")),
                "type": disk.get("type"),
                "serial": disk.get("serial"),
            }
            for disk in self._rpc("disk.query")
        ]

    def get_services(self) -> list[dict[str, Any]]:
        return [
            {
                "service": service.get("service"),
                "state": service.get("state"),
                "enable": service.get("enable"),
            }
            for service in self._rpc("service.query")
        ]

    def restart_service(self, service_name: str) -> Any:
        return self._rpc("service.restart", [service_name, {}])

    def create_dataset(self, pool_name: str, dataset_name: str) -> Any:
        return self._rpc(
            "pool.dataset.create",
            [{"name": f"{pool_name}/{dataset_name}"}],
        )

    def get_alerts(self) -> list[dict[str, Any]]:
        return [
            {
                "level": alert.get("level"),
                "text": alert.get("formatted") or alert.get("text"),
                "dismissed": alert.get("dismissed"),
                "datetime": (alert.get("datetime") or {}).get("$date"),
            }
            for alert in self._rpc("alert.list")
        ]

    def get_disk_temperatures(self) -> list[dict[str, Any]]:
        disks = self._rpc("disk.query")
        names = [disk.get("name") for disk in disks if disk.get("name")]
        if not names:
            return []
        items = self._rpc("disk.temperatures", [names])
        if isinstance(items, dict):
            items = items.get("temperatures") or items.get("data") or items
        if isinstance(items, dict):
            items = [
                {"name": name, "temperature": value}
                for name, value in items.items()
            ]
        if not isinstance(items, list):
            return []

        return [
            {
                "name": item.get("name") or item.get("disk"),
                "temperature": item.get("temperature", item.get("temp")),
                "unit": item.get("unit") or "C",
            }
            for item in items
            if isinstance(item, dict)
            and item.get("temperature", item.get("temp")) is not None
        ]

    def get_disk_temperature_alerts(self) -> list[dict[str, Any]]:
        disks = self._rpc("disk.query")
        names = [disk.get("name") for disk in disks if disk.get("name")]
        if not names:
            return []
        alerts = self._rpc("disk.temperature_alerts", [names])
        return [
            {
                "id": alert.get("id"),
                "level": alert.get("level"),
                "formatted": alert.get("formatted") or alert.get("text"),
                "dismissed": alert.get("dismissed"),
                "datetime": alert.get("datetime"),
                "args": alert.get("args"),
            }
            for alert in alerts
            if isinstance(alert, dict)
        ]

    def get_scrub_status(self) -> list[dict[str, Any]]:
        return [
            {
                "pool": pool.get("name"),
                "scan_function": (pool.get("scan") or {}).get("function"),
                "scan_state": (pool.get("scan") or {}).get("state"),
                "percentage": (pool.get("scan") or {}).get("percentage"),
                "errors": (pool.get("scan") or {}).get("errors"),
            }
            for pool in self._rpc("pool.query")
        ]

    def get_network_interfaces(self) -> list[dict[str, Any]]:
        return [
            {
                "name": interface.get("name"),
                "description": interface.get("description"),
                "state": (interface.get("state") or {}).get("link_state"),
                "addresses": [
                    address.get("address")
                    for address in (interface.get("state") or {}).get("aliases", [])
                    if address.get("address")
                ],
            }
            for interface in self._rpc("interface.query")
            if isinstance(interface, dict)
        ]

    def get_update_status(self) -> Any:
        return self._rpc("update.available_versions")

    def get_apps(self) -> list[dict[str, Any]]:
        return [
            {
                "name": app.get("name"),
                "state": app.get("state"),
                "version": (app.get("metadata") or {}).get("app_version"),
            }
            for app in self._rpc("app.query")
        ]
