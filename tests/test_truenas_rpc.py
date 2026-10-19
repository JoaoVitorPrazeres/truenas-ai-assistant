import json
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
