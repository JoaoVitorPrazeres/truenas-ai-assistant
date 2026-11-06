import json
import ssl
from threading import RLock
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


def memory_bytes_to_human(value: int | float | None) -> str | None:
    if not isinstance(value, (int, float)) or value < 0:
        return None
    gib = float(value) / (1024 ** 3)
    if gib >= 1024:
        return f"{round(gib / 1024, 2)} TiB"
    return f"{round(gib, 2)} GiB"


class TrueNASClient:
    def __init__(self, settings: Settings, persistent: bool = False):
        parsed_url = urlsplit(settings.truenas_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ExternalServiceError(
                "TRUENAS_URL deve usar HTTPS e incluir o endereço do TrueNAS"
            )
        self.websocket_url = urlunsplit(
            (
                "wss",
                parsed_url.netloc,
                "/api/current",
                "",
                "",
            )
        )
        self.username = settings.truenas_username
        self.api_key = settings.truenas_api_key
        self.ssl_options: dict[str, Any] = {
            "cert_reqs": ssl.CERT_REQUIRED,
            "check_hostname": True,
        }
        if settings.truenas_ca_bundle:
            self.ssl_options["ca_certs"] = settings.truenas_ca_bundle
        self.persistent = persistent
        self._connection: websocket.WebSocket | None = None
        self._connection_lock = RLock()

    @staticmethod
    def _receive_result(
        connection: websocket.WebSocket, request_id: int, method: str
    ) -> Any:
        try:
            response = json.loads(connection.recv())
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
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

    def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except (websocket.WebSocketException, OSError):
                pass

    def _connect_and_authenticate(self) -> websocket.WebSocket:
        connection = websocket.create_connection(
            self.websocket_url,
            timeout=30,
            sslopt=self.ssl_options,
        )
        self._connection = connection
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
        return connection

    def _require_dict_list(self, value: Any, method: str) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ExternalServiceError(
                f"TrueNAS retornou uma lista inválida para {method}"
            )
        if not all(isinstance(item, dict) for item in value):
            raise ExternalServiceError(
                f"TrueNAS retornou um item inválido para {method}"
            )
        return value

    def _rpc(self, method: str, params: list[Any] | None = None) -> Any:
        if self.persistent:
            return self._rpc_persistent(method, params)

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
                try:
                    connection.close()
                except (websocket.WebSocketException, OSError):
                    pass

    def _rpc_persistent(
        self, method: str, params: list[Any] | None = None
    ) -> Any:
        with self._connection_lock:
            try:
                connection = self._connection
                if connection is None:
                    connection = self._connect_and_authenticate()

                connection.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": method,
                    "params": params or [],
                }))
                return self._receive_result(connection, 2, method)
            except ExternalServiceError:
                self._close_connection()
                raise
            except (websocket.WebSocketException, OSError, TimeoutError) as exc:
                self._close_connection()
                try:
                    connection = self._connect_and_authenticate()
                    connection.send(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": method,
                        "params": params or [],
                    }))
                    return self._receive_result(connection, 2, method)
                except ExternalServiceError:
                    self._close_connection()
                    raise
                except (
                    websocket.WebSocketException,
                    OSError,
                    TimeoutError,
                ) as retry_exc:
                    self._close_connection()
                    raise ExternalServiceError(
                        "Não foi possível conectar à API WebSocket do TrueNAS "
                        f"mesmo após reconectar: {retry_exc}"
                    ) from retry_exc

    def close(self) -> None:
        if not self.persistent:
            return
        with self._connection_lock:
            self._close_connection()

    def get_system_info(self) -> Any:
        data = self._rpc("system.info")
        if not isinstance(data, dict):
            raise ExternalServiceError(
                "TrueNAS retornou um formato inesperado para system.info"
            )

        uptime_seconds = (
            data.get("uptime_seconds")
            or data.get("uptime")
        )
        if isinstance(uptime_seconds, dict):
            uptime_seconds = uptime_seconds.get("seconds")
        if isinstance(uptime_seconds, str):
            try:
                uptime_seconds = float(uptime_seconds)
            except ValueError:
                uptime_seconds = None

        uptime_human = None
        if isinstance(uptime_seconds, (int, float)):
            total_seconds = int(uptime_seconds)
            days, remainder = divmod(total_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            parts = []
            if days:
                parts.append(f"{days} dia{'s' if days != 1 else ''}")
            if hours or days:
                parts.append(f"{hours} hora{'s' if hours != 1 else ''}")
            if minutes or hours or days:
                parts.append(f"{minutes} minuto{'s' if minutes != 1 else ''}")
            parts.append(f"{seconds} segundo{'s' if seconds != 1 else ''}")
            uptime_human = ", ".join(parts)

        memory_bytes = (
            data.get("physmem")
            or data.get("physical_memory")
            or data.get("memory_size")
            or data.get("total_memory")
        )
        cpu_cores = (
            data.get("cores")
            or data.get("physical_cores")
            or data.get("cpu_cores")
        )
        loadavg = data.get("loadavg")
        if not isinstance(loadavg, list):
            loadavg = None

        return {
            "hostname": data.get("hostname"),
            "version": data.get("version") or data.get("system_version"),
            "uptime_seconds": uptime_seconds,
            "uptime_human": uptime_human,
            "cpu_model": (
                data.get("cpu_model")
                or data.get("model")
                or data.get("cpu_name")
            ),
            "cpu_cores": cpu_cores,
            "cpu_clock_mhz": (
                data.get("cpu_speed")
                or data.get("cpu_clock_mhz")
            ),
            "memory_bytes": memory_bytes,
            "memory_human": memory_bytes_to_human(memory_bytes),
            "memory_total_bytes": memory_bytes,
            "memory_total_human": memory_bytes_to_human(memory_bytes),
            "load_average": loadavg,
            "ecc_memory": data.get("ecc_memory"),
            "system_manufacturer": data.get("system_manufacturer"),
            "system_product_name": data.get("system_product_name"),
            "system_serial_number": data.get("system_serial_number"),
        }

    def get_pools(self) -> list[dict[str, Any]]:
        pools = self._require_dict_list(self._rpc("pool.query"), "pool.query")
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
        datasets = self._require_dict_list(
            self._rpc("pool.dataset.query"), "pool.dataset.query"
        )

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
                "status": (
                    disk.get("status")
                    or disk.get("health")
                    or disk.get("smart_status")
                ),
            }
            for disk in self._require_dict_list(
                self._rpc("disk.query"), "disk.query"
            )
        ]

    def get_services(self) -> list[dict[str, Any]]:
        return [
            {
                "service": service.get("service"),
                "state": service.get("state"),
                "enable": service.get("enable"),
            }
            for service in self._require_dict_list(
                self._rpc("service.query"), "service.query"
            )
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
            for alert in self._require_dict_list(
                self._rpc("alert.list"), "alert.list"
            )
        ]

    def get_disk_temperatures(self) -> list[dict[str, Any]]:
        disks = self._require_dict_list(
            self._rpc("disk.query"), "disk.query"
        )
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
        disks = self._require_dict_list(
            self._rpc("disk.query"), "disk.query"
        )
        names = [disk.get("name") for disk in disks if disk.get("name")]
        if not names:
            return []
        alerts = self._require_dict_list(
            self._rpc("disk.temperature_alerts", [names]),
            "disk.temperature_alerts",
        )
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
            for pool in self._require_dict_list(
                self._rpc("pool.query"), "pool.query"
            )
        ]

    def get_network_interfaces(self) -> list[dict[str, Any]]:
        interfaces = self._require_dict_list(
            self._rpc("interface.query"), "interface.query"
        )
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
                "link_speed_mbps": (
                    interface.get("state") or {}
                ).get("link_speed"),
            }
            for interface in interfaces
        ]

    def get_update_status(self) -> Any:
        return self._rpc("update.available_versions")

    def get_apps(self) -> list[dict[str, Any]]:
        return [
            {
                "name": app.get("name"),
                "state": app.get("state"),
                "version": (app.get("metadata") or {}).get("app_version"),
                "portals": app.get("portals") or {},
            }
            for app in self._require_dict_list(
                self._rpc("app.query"), "app.query"
            )
        ]

    def get_snapshots(self) -> list[dict[str, Any]]:
        snapshots = self._require_dict_list(
            self._rpc("zfs.snapshot.query"), "zfs.snapshot.query"
        )
        result = []
        for snapshot in snapshots:
            properties = snapshot.get("properties") or {}
            result.append({
                "name": snapshot.get("name"),
                "dataset": snapshot.get("dataset"),
                "type": snapshot.get("type"),
                "creation": snapshot.get("createtxg"),
                "used_bytes": (properties.get("used") or {}).get("value")
                if isinstance(properties.get("used"), dict)
                else properties.get("used"),
            })
        return result

    def get_network_configuration(self) -> dict[str, Any]:
        configuration = self._rpc("network.configuration")
        if not isinstance(configuration, dict):
            raise ExternalServiceError(
                "TrueNAS retornou um formato inesperado para a configuração de rede"
            )
        return {
            "hostname": configuration.get("hostname"),
            "domain": configuration.get("domain"),
            "ipv4_gateway": configuration.get("ipv4gateway"),
            "ipv6_gateway": configuration.get("ipv6gateway"),
            "nameservers": configuration.get("nameserver"),
            "nameserver1": configuration.get("nameserver1"),
            "nameserver2": configuration.get("nameserver2"),
            "nameserver3": configuration.get("nameserver3"),
        }

    def get_task_history(self) -> list[dict[str, Any]]:
        jobs = self._require_dict_list(
            self._rpc("core.get_jobs"), "core.get_jobs"
        )
        result = []
        for job in jobs:
            result.append({
                "id": job.get("id"),
                "method": job.get("method"),
                "state": job.get("state"),
                "progress": job.get("progress"),
                "description": job.get("description"),
                "time_started": job.get("time_started"),
                "time_finished": job.get("time_finished"),
                "error": job.get("error"),
            })
        return result

    def get_app_ports(self) -> list[dict[str, Any]]:
        ports = []
        apps = self.get_apps()
        for app in apps:
            for label, portal in (app.get("portals") or {}).items():
                if isinstance(portal, str):
                    ports.append({
                        "application": app.get("name"),
                        "label": label,
                        "address": portal,
                    })
        return ports
