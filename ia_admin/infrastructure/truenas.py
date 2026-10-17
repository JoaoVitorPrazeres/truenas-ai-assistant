import logging
from typing import Any

from ia_admin.config import Settings
from ia_admin.infrastructure.http_client import ExternalServiceError, request_json


log = logging.getLogger("ia-admin-telegram")


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
        self.base_url = f"{settings.truenas_url}/api/v2.0"
        self.headers = {
            "Authorization": f"Bearer {settings.truenas_api_key}",
            "Content-Type": "application/json",
        }
        self.verify: str | bool = settings.truenas_ca_bundle or True

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return request_json(
            "GET",
            f"{self.base_url}{path}",
            "TrueNAS",
            15,
            headers=self.headers,
            params=params,
            verify=self.verify,
        )

    def _post(self, path: str, body: Any = None) -> Any:
        return request_json(
            "POST",
            f"{self.base_url}{path}",
            "TrueNAS",
            30,
            headers=self.headers,
            json=body or {},
            verify=self.verify,
        )

    def get_system_info(self) -> Any:
        return self._get("/system/info")

    def get_pools(self) -> list[dict[str, Any]]:
        pools = self._get("/pool")
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
        datasets = self._get("/pool/dataset")

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
        data = self._post(
            "/reporting/get_data",
            {"graphs": [{"name": "cpu"}, {"name": "memory"}]},
        )
        if not isinstance(data, list):
            raise ExternalServiceError(
                "TrueNAS retornou um formato inesperado para uso de recursos"
            )

        summary = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            mean = (entry.get("aggregations") or {}).get("mean")
            if isinstance(mean, dict):
                mean_key, mean = next(iter(mean.items()), (None, None))
            elif isinstance(mean, list) and mean:
                mean_key, mean = None, mean[-1]
            else:
                mean_key = None

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
            for disk in self._get("/disk")
        ]

    def get_services(self) -> list[dict[str, Any]]:
        return [
            {
                "service": service.get("service"),
                "state": service.get("state"),
                "enable": service.get("enable"),
            }
            for service in self._get("/service")
        ]

    def restart_service(self, service_name: str) -> Any:
        return self._post("/service/restart", {"service": service_name})

    def create_dataset(self, pool_name: str, dataset_name: str) -> Any:
        return self._post("/pool/dataset", {"name": f"{pool_name}/{dataset_name}"})

    def get_alerts(self) -> list[dict[str, Any]]:
        return [
            {
                "level": alert.get("level"),
                "text": alert.get("formatted") or alert.get("text"),
                "dismissed": alert.get("dismissed"),
                "datetime": (alert.get("datetime") or {}).get("$date"),
            }
            for alert in self._get("/alert/list")
        ]

    def get_disk_temperatures(self) -> list[dict[str, Any]]:
        def normalize(items: Any) -> list[dict[str, Any]]:
            if isinstance(items, dict):
                nested = (
                    items.get("sensors")
                    or items.get("data")
                    or items.get("temperatures")
                    or items.get("items")
                )
                items = nested if isinstance(nested, (list, dict)) else [items]
            if isinstance(items, dict):
                items = [
                    {"name": name, "temperature": value}
                    for name, value in items.items()
                ]
            if not isinstance(items, list):
                return []

            normalized = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = (
                    item.get("name")
                    or item.get("device")
                    or item.get("disk")
                    or item.get("sensor")
                    or item.get("label")
                )
                value = next(
                    (
                        item[key]
                        for key in ("temperature", "temp", "temperature_c", "value", "current")
                        if key in item and item[key] is not None
                    ),
                    None,
                )
                if value is not None:
                    normalized.append({
                        "name": name,
                        "temperature": value,
                        "unit": item.get("unit") or "C",
                    })
            return normalized

        try:
            disk_data = normalize(self._get("/disk"))
        except Exception:
            log.debug("Não foi possível obter temperaturas em /disk", exc_info=True)
            disk_data = []
        if disk_data:
            return disk_data

        for path in ("/disk/temperatures", "/disk/temperature", "/system/sensor", "/sensor"):
            try:
                data = self._get(path)
            except Exception:
                log.debug("Endpoint de temperatura indisponível: %s", path, exc_info=True)
                continue
            if not data:
                log.debug("Endpoint de temperatura retornou vazio: %s", path)
                continue
            normalized = normalize(data)
            if normalized:
                return normalized
        return []

    def get_disk_temperature_alerts(self) -> list[dict[str, Any]]:
        disks = self._get("/disk")
        names = [disk.get("name") for disk in disks if disk.get("name")]
        if not names:
            return []
        alerts = self._post("/disk/temperature_alerts", names)
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
            for pool in self._get("/pool")
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
            for interface in self._get("/interface")
            if isinstance(interface, dict)
        ]

    def get_update_status(self) -> Any:
        return self._get("/update/check_available")

    def get_apps(self) -> list[dict[str, Any]]:
        return [
            {
                "name": app.get("name"),
                "state": app.get("state"),
                "version": (app.get("metadata") or {}).get("app_version"),
            }
            for app in self._get("/app")
        ]
