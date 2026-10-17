from collections.abc import Callable
from typing import Any

from ia_admin.infrastructure.truenas import TrueNASClient


def build_tool_functions(
    client: TrueNASClient,
) -> dict[str, Callable[..., Any]]:
    return {
        "get_system_info": lambda **_: client.get_system_info(),
        "get_pools": lambda **_: client.get_pools(),
        "get_datasets": lambda **_: client.get_datasets(),
        "get_resource_usage": lambda **_: client.get_resource_usage(),
        "get_disks": lambda **_: client.get_disks(),
        "get_services": lambda **_: client.get_services(),
        "get_alerts": lambda **_: client.get_alerts(),
        "get_disk_temperatures": lambda **_: client.get_disk_temperatures(),
        "get_disk_temperature_alerts": lambda **_: client.get_disk_temperature_alerts(),
        "get_scrub_status": lambda **_: client.get_scrub_status(),
        "get_network_interfaces": lambda **_: client.get_network_interfaces(),
        "get_update_status": lambda **_: client.get_update_status(),
        "get_apps": lambda **_: client.get_apps(),
        "restart_service": lambda **kwargs: client.restart_service(
            kwargs["service_name"]
        ),
        "create_dataset": lambda **kwargs: client.create_dataset(
            kwargs["pool_name"], kwargs["dataset_name"]
        ),
    }
