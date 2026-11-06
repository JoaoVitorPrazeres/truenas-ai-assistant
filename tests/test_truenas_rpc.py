import json
import ssl
import unittest
from unittest.mock import patch

import websocket

from ia_admin.config import Settings
from ia_admin.infrastructure.http_client import ExternalServiceError
from ia_admin.infrastructure.truenas import TrueNASClient


def make_settings(url: str = "https://nas.example.test") -> Settings:
    return Settings(
        truenas_url=url,
        truenas_username="bot-user",
        truenas_api_key="api-key",
        truenas_ca_bundle="",
        ollama_model="qwen3:1.7b",
        ollama_host="http://127.0.0.1:11434",
        telegram_bot_token="telegram-token",
        allowed_chat_ids=frozenset({"123"}),
    )


class FakeWebSocket:
    def __init__(self, responses: list[dict[str, object]]):
        self.responses = [json.dumps(response) for response in responses]
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def recv(self) -> str:
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class TrueNASRpcTests(unittest.TestCase):
    def test_client_rejects_http_url(self) -> None:
        with self.assertRaisesRegex(ExternalServiceError, "deve usar HTTPS"):
            TrueNASClient(make_settings("http://nas.example.test"))

    def test_client_rejects_invalid_url_scheme(self) -> None:
        with self.assertRaisesRegex(ExternalServiceError, "deve usar HTTPS"):
            TrueNASClient(make_settings("ftp://nas.example.test"))

    def test_rpc_authenticates_and_calls_method_over_websocket(self) -> None:
        connection = FakeWebSocket([
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"response_type": "SUCCESS"},
            },
            {"jsonrpc": "2.0", "id": 2, "result": {"hostname": "nas"}},
        ])
        client = TrueNASClient(make_settings())

        with patch(
            "ia_admin.infrastructure.truenas.websocket.create_connection",
            return_value=connection,
        ) as create_connection:
            result = client.get_system_info()

        self.assertEqual(result, {"hostname": "nas"})
        self.assertEqual(client.websocket_url, "wss://nas.example.test/api/current")
        self.assertEqual(
            connection.sent[0]["method"],
            "auth.login_ex",
        )
        self.assertEqual(
            connection.sent[0]["params"][0],
            {
                "mechanism": "API_KEY_PLAIN",
                "username": "bot-user",
                "api_key": "api-key",
            },
        )
        self.assertEqual(connection.sent[1]["method"], "system.info")
        self.assertEqual(connection.sent[1]["params"], [])
        self.assertTrue(connection.closed)
        create_connection.assert_called_once()
        self.assertEqual(
            create_connection.call_args.kwargs["sslopt"],
            {"cert_reqs": ssl.CERT_REQUIRED, "check_hostname": True},
        )

    def test_rpc_rejects_failed_authentication(self) -> None:
        connection = FakeWebSocket([
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"response_type": "AUTH_ERR"},
            },
        ])
        client = TrueNASClient(make_settings())

        with (
            patch(
                "ia_admin.infrastructure.truenas.websocket.create_connection",
                return_value=connection,
            ),
            self.assertRaisesRegex(ExternalServiceError, "Autenticação"),
        ):
            client.get_system_info()

        self.assertEqual(len(connection.sent), 1)
        self.assertTrue(connection.closed)

    def test_resource_usage_reads_jsonrpc_reporting_aggregations(self) -> None:
        client = TrueNASClient(make_settings())
        client._rpc = unittest.mock.Mock(return_value=[
            {
                "name": "cpu",
                "data": [{"aggregations": {"mean": {"cpu": 17.25}}}],
            },
            {
                "name": "memory",
                "data": [{
                    "aggregations": {
                        "mean": {"available": 2 * 1024 ** 3},
                    },
                }],
            },
        ])

        result = client.get_resource_usage()

        self.assertEqual(result["cpu_percent"], 17.25)
        self.assertEqual(result["memory_available_bytes"], 2 * 1024 ** 3)
        self.assertEqual(result["memory_available_gib"], 2)
        client._rpc.assert_called_once_with(
            "reporting.get_data",
            [[{"name": "cpu"}, {"name": "memory"}], {
                "unit": "HOUR",
                "page": 1,
                "aggregate": True,
            }],
        )

    def test_write_operations_use_jsonrpc_methods_and_parameters(self) -> None:
        client = TrueNASClient(make_settings())
        client._rpc = unittest.mock.Mock()

        client.restart_service("smb")
        client.create_dataset("tank", "backups")

        self.assertEqual(
            client._rpc.call_args_list,
            [
                unittest.mock.call("service.restart", ["smb", {}]),
                unittest.mock.call(
                    "pool.dataset.create",
                    [{"name": "tank/backups"}],
                ),
            ],
        )

    def test_receive_result_rejects_invalid_json(self) -> None:
        connection = unittest.mock.Mock()
        connection.recv.return_value = "{not-json"

        with self.assertRaisesRegex(
            ExternalServiceError, "resposta JSON inválida"
        ):
            TrueNASClient._receive_result(connection, 2, "system.info")

    def test_receive_result_rejects_mismatched_request_id(self) -> None:
        connection = unittest.mock.Mock()
        connection.recv.return_value = json.dumps({"jsonrpc": "2.0", "id": 99, "result": {}})

        with self.assertRaisesRegex(
            ExternalServiceError, "resposta inesperada"
        ):
            TrueNASClient._receive_result(connection, 2, "system.info")

    def test_receive_result_rejects_jsonrpc_error(self) -> None:
        connection = unittest.mock.Mock()
        connection.recv.return_value = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"message": "Método não encontrado", "data": {"reason": "unknown method"}},
        })

        with self.assertRaisesRegex(
            ExternalServiceError,
            "unknown method",
        ):
            TrueNASClient._receive_result(connection, 2, "system.info")

    def test_receive_result_rejects_missing_result(self) -> None:
        connection = unittest.mock.Mock()
        connection.recv.return_value = json.dumps({"jsonrpc": "2.0", "id": 2})

        with self.assertRaisesRegex(
            ExternalServiceError, "não retornou resultado"
        ):
            TrueNASClient._receive_result(connection, 2, "system.info")

    def test_rpc_reports_websocket_network_error(self) -> None:
        client = TrueNASClient(make_settings())

        with patch(
            "ia_admin.infrastructure.truenas.websocket.create_connection",
            side_effect=websocket.WebSocketException("connection closed"),
        ):
            with self.assertRaisesRegex(
                ExternalServiceError, "WebSocket do TrueNAS"
            ):
                client.get_system_info()

    def test_rpc_reports_timeout(self) -> None:
        client = TrueNASClient(make_settings())

        with patch(
            "ia_admin.infrastructure.truenas.websocket.create_connection",
            side_effect=TimeoutError("timed out"),
        ):
            with self.assertRaisesRegex(
                ExternalServiceError, "WebSocket do TrueNAS"
            ):
                client.get_system_info()

    def test_list_methods_reject_unexpected_format(self) -> None:
        client = TrueNASClient(make_settings())
        client._rpc = unittest.mock.Mock(return_value={"not": "a list"})

        for method in (
            client.get_pools,
            client.get_disks,
            client.get_services,
            client.get_alerts,
            client.get_scrub_status,
            client.get_network_interfaces,
            client.get_apps,
        ):
            with self.subTest(method=method.__name__):
                with self.assertRaisesRegex(
                    ExternalServiceError, "lista inválida"
                ):
                    method()

    def test_list_methods_reject_non_dict_items(self) -> None:
        client = TrueNASClient(make_settings())
        client._rpc = unittest.mock.Mock(return_value=["invalid"])

        with self.assertRaisesRegex(
            ExternalServiceError, "item inválido"
        ):
            client.get_pools()

    def test_secondary_rpc_methods_build_params(self) -> None:
        client = TrueNASClient(make_settings())
        client._rpc = unittest.mock.Mock(side_effect=[
            [{"name": "sda"}],
            [{"name": "sda", "temperature": 38, "unit": "C"}],
        ])

        result = client.get_disk_temperatures()
        self.assertEqual(
            result,
            [{"name": "sda", "temperature": 38, "unit": "C"}],
        )
        self.assertEqual(
            client._rpc.call_args_list[1],
            unittest.mock.call("disk.temperatures", [["sda"]]),
        )

        client._rpc = unittest.mock.Mock(return_value=[
            {"level": "WARNING", "formatted": "Disk hot", "dismissed": False}
        ])
        self.assertEqual(client.get_alerts()[0]["level"], "WARNING")

    def test_persistent_mode_authenticates_once_for_multiple_calls(self) -> None:
        connection = FakeWebSocket([
            {"jsonrpc": "2.0", "id": 1, "result": {"response_type": "SUCCESS"}},
            {"jsonrpc": "2.0", "id": 2, "result": {"hostname": "nas-one"}},
            {"jsonrpc": "2.0", "id": 2, "result": {"hostname": "nas-two"}},
        ])
        client = TrueNASClient(make_settings(), persistent=True)

        with patch(
            "ia_admin.infrastructure.truenas.websocket.create_connection",
            return_value=connection,
        ) as create_connection:
            self.assertEqual(client.get_system_info()["hostname"], "nas-one")
            self.assertEqual(client.get_system_info()["hostname"], "nas-two")
            client.close()

        self.assertEqual(create_connection.call_count, 1)
        self.assertEqual(
            sum(1 for message in connection.sent if message["method"] == "auth.login_ex"),
            1,
        )
        self.assertTrue(connection.closed)

    def test_temperature_alerts_interfaces_apps_and_updates(self) -> None:
        client = TrueNASClient(make_settings())

        with unittest.mock.patch.object(
            client,
            "_rpc",
            side_effect=[
                [{"name": "sda"}],
                [{"id": 1, "level": "WARNING", "formatted": "Hot disk"}],
                [],
                [{"name": "plex", "state": "RUNNING", "metadata": {"app_version": "1.0"}}],
                [{"version": "24.10.2"}],
            ],
        ):
            alerts = client.get_disk_temperature_alerts()
            interfaces = client.get_network_interfaces()
            apps = client.get_apps()
            updates = client.get_update_status()

        self.assertEqual(alerts[0]["formatted"], "Hot disk")
        self.assertEqual(interfaces, [])
        self.assertEqual(apps[0]["version"], "1.0")
        self.assertEqual(updates, [{"version": "24.10.2"}])

    def test_snapshots_network_tasks_and_app_ports(self) -> None:
        client = TrueNASClient(make_settings())

        with unittest.mock.patch.object(
            client,
            "_rpc",
            side_effect=[
                [{"name": "nas/data@snap-1", "dataset": "nas/data"}],
                {
                    "hostname": "truenas",
                    "ipv4gateway": "192.168.100.1",
                    "nameserver1": "192.168.100.1",
                },
                [{
                    "id": 12,
                    "method": "pool.scrub.run",
                    "state": "RUNNING",
                    "progress": {"percent": 25},
                }],
                [{
                    "name": "plex",
                    "state": "RUNNING",
                    "portals": {"Web UI": "http://192.168.100.34:32400"},
                }],
            ],
        ):
            snapshots = client.get_snapshots()
            network = client.get_network_configuration()
            tasks = client.get_task_history()
            ports = client.get_app_ports()

        self.assertEqual(snapshots[0]["name"], "nas/data@snap-1")
        self.assertEqual(network["ipv4_gateway"], "192.168.100.1")
        self.assertEqual(tasks[0]["method"], "pool.scrub.run")
        self.assertEqual(ports[0]["address"], "http://192.168.100.34:32400")


if __name__ == "__main__":
    unittest.main()
