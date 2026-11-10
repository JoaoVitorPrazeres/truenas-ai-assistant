import unittest

from ia_admin.agent.service import (
    AgentService,
    direct_readonly_tool,
    format_direct_result,
)


class DirectReadonlyToolTests(unittest.TestCase):
    def test_network_interface_result_is_not_replaced_by_example_ip(self) -> None:
        result = [{
            "name": "enp2s0f1",
            "state": "UP",
            "addresses": ["192.168.100.34"],
        }]

        formatted = format_direct_result("get_network_interfaces", result)

        self.assertIn("192.168.100.34", formatted)
        self.assertNotIn("192.168.1.100", formatted)

    def test_network_interface_tool_error_is_returned(self) -> None:
        formatted = format_direct_result(
            "get_network_interfaces",
            {"error": "Falha na ferramenta get_network_interfaces"},
        )

        self.assertEqual(formatted, "Falha na ferramenta get_network_interfaces")

    def test_network_interface_result_includes_tailscale_ip(self) -> None:
        result = [
            {"name": "enp2s0f1", "state": "UP", "addresses": ["192.168.100.34"]},
            {"name": "tailscale0", "state": "UP", "addresses": ["100.64.0.5"]},
        ]

        formatted = format_direct_result("get_network_interfaces", result)

        self.assertIn("192.168.100.34", formatted)
        self.assertIn("100.64.0.5", formatted)
        self.assertIn("Tailscale", formatted)

    def test_storage_and_pool_questions_call_get_pools(self) -> None:
        for question in (
            "Quais são os pools do servidor?",
            "Mostre o armazenamento disponível",
            "Quanto espaço em disco há?",
        ):
            with self.subTest(question=question):
                self.assertEqual(direct_readonly_tool(question), "get_pools")

    def test_dataset_questions_call_get_datasets(self) -> None:
        self.assertEqual(
            direct_readonly_tool("Liste os datasets do servidor"),
            "get_datasets",
        )

    def test_dataset_creation_is_not_treated_as_readonly_query(self) -> None:
        self.assertIsNone(
            direct_readonly_tool("Crie um novo dataset no pool principal")
        )

    def test_monitoring_questions_route_to_readonly_tools(self) -> None:
        cases = (
            ("Quais snapshots existem?", "get_snapshots"),
            ("Qual é o gateway configurado?", "get_network_configuration"),
            ("Quais servidores DNS estão configurados?", "get_network_configuration"),
            ("Qual é o endereço IP do servidor?", "get_network_interfaces"),
            ("Me informe o IPv4 da rede", "get_network_interfaces"),
            ("Liste o histórico de tarefas", "get_task_history"),
            ("Quais aplicações estão instaladas?", "get_apps"),
            ("Quais portas utilizadas pelos apps?", "get_app_ports"),
            ("Existe algum alerta ativo?", "get_alerts"),
            ("Qual a versão do TrueNAS?", "get_system_info"),
            ("Qual é o IP do servidor?", "get_network_interfaces"),
        )
        for question, expected in cases:
            with self.subTest(question=question):
                self.assertEqual(direct_readonly_tool(question), expected)


class OllamaFormattingTests(unittest.IsolatedAsyncioTestCase):
    async def test_ip_question_uses_only_confirmed_interface_addresses(self) -> None:
        class UnexpectedOllamaCall:
            def chat(self, messages, tools):
                raise AssertionError("IPs confirmados não devem passar pelo modelo")

        service = AgentService(
            allowed_chat_ids=frozenset({"123"}),
            ollama=UnexpectedOllamaCall(),
            tool_functions={
                "get_network_interfaces": lambda: [{
                    "name": "enp2s0f1",
                    "state": "UP",
                    "addresses": ["192.168.100.34"],
                }],
            },
        )

        response = await service.answer(123, "Qual é o IP do servidor?")

        self.assertIn("192.168.100.34", response.text)
        self.assertNotIn("192.168.1.100", response.text)

    async def test_direct_tool_result_is_formatted_by_ollama(self) -> None:
        class FakeOllama:
            def __init__(self) -> None:
                self.messages = None
                self.tools = None

            def chat(self, messages, tools):
                self.messages = messages
                self.tools = tools
                return {"content": "Pool nas está ONLINE e com 4,51 TB livres."}

        ollama = FakeOllama()
        service = AgentService(
            allowed_chat_ids=frozenset({"123"}),
            ollama=ollama,
            tool_functions={
                "get_pools": lambda: [{
                    "name": "nas",
                    "status": "ONLINE",
                    "free_human": "4.51 TB",
                }],
            },
        )

        response = await service.answer(123, "Como está o pool nas?")

        self.assertIn("ONLINE", response.text)
        self.assertEqual(ollama.tools, [])
        self.assertEqual(
            ollama.messages[-2]["role"],
            "tool",
        )

    async def test_broad_status_query_uses_multiple_tools(self) -> None:
        class UnexpectedOllama:
            def chat(self, messages, tools):
                raise AssertionError("O resumo do servidor deve ser determinístico")

        called = []

        def make_function(name):
            def function():
                called.append(name)
                if name == "get_system_info":
                    return {
                        "hostname": "truenas",
                        "version": "25.10.7",
                        "uptime_human": "2 horas",
                        "cpu_model": "Intel i5-8400T",
                        "cpu_cores": 6,
                        "memory_bytes": 7710375936,
                        "memory_human": "7.18 GiB",
                        "memory_total_bytes": 7710375936,
                        "memory_total_human": "7.18 GiB",
                        "load_average": [0.0063, 0.0332, 0.0605],
                    }
                if name == "get_resource_usage":
                    return {
                        "cpu_percent": 0.63,
                        "memory_available_bytes": 1524060200,
                        "memory_available_gib": 1.42,
                    }
                if name == "get_network_interfaces":
                    return [
                        {
                            "name": "enp2s0f1",
                            "state": "UP",
                            "addresses": ["192.168.100.34"],
                        },
                        {
                            "name": "tailscale0",
                            "state": "UP",
                            "addresses": ["100.65.161.105"],
                        },
                    ]
                if name == "get_network_configuration":
                    return {
                        "ipv4_gateway": "192.168.100.1",
                        "nameservers": ["192.168.100.1"],
                    }
                if name == "get_services":
                    return [{"service": "cifs", "state": "RUNNING"}]
                if name == "get_pools":
                    return [{"name": "nas", "status": "ONLINE", "free_human": "4.5 TB"}]
                if name == "get_disks":
                    return [{"name": "sda", "model": "Disk model", "status": "HEALTHY"}]
                if name == "get_disk_temperatures":
                    return []
                if name == "get_alerts":
                    return []
                if name == "get_scrub_status":
                    return [{"pool": "nas", "scan_function": None, "scan_state": None}]
                if name == "get_apps":
                    return [{"name": "ollama", "state": "RUNNING"}]
                raise AssertionError(f"Ferramenta inesperada: {name}")
            return function

        tool_names = (
            "get_system_info",
            "get_resource_usage",
            "get_network_interfaces",
            "get_network_configuration",
            "get_services",
            "get_pools",
            "get_disks",
            "get_disk_temperatures",
            "get_alerts",
            "get_scrub_status",
            "get_apps",
        )
        service = AgentService(
            allowed_chat_ids=frozenset({"123"}),
            ollama=UnexpectedOllama(),
            tool_functions={name: make_function(name) for name in tool_names},
        )

        response = await service.answer(
            123,
            "Quero ver todas as informações do servidor",
        )

        self.assertIn("Hostname: truenas", response.text)
        self.assertIn("RAM utilizada estimada: 5.76 GiB (80.23%)", response.text)
        self.assertIn("192.168.100.34", response.text)
        self.assertIn("100.65.161.105", response.text)
        self.assertIn("Tailscale", response.text)
        self.assertIn("Gateway IPv4: 192.168.100.1", response.text)
        self.assertIn("cifs: RUNNING", response.text)
        self.assertIn("Nenhum alerta ativo foi detectado.", response.text)
        self.assertEqual(set(called), set(tool_names))

    async def test_individual_system_info_includes_current_network_interfaces(self) -> None:
        class UnexpectedOllama:
            def chat(self, messages, tools):
                raise AssertionError("A resposta do sistema deve ser determinística")

        service = AgentService(
            allowed_chat_ids=frozenset({"123"}),
            ollama=UnexpectedOllama(),
            tool_functions={
                "get_system_info": lambda: {
                    "hostname": "truenas",
                    "version": "25.10.7",
                    "memory_human": "7.18 GiB",
                },
                "get_network_interfaces": lambda: [{
                    "name": "tailscale0",
                    "state": "UP",
                    "addresses": ["100.65.161.105"],
                }],
            },
        )

        response = await service.answer(123, "Mostre as informações do servidor")

        self.assertIn("Versão: 25.10.7", response.text)
        self.assertIn("100.65.161.105", response.text)
        self.assertIn("Tailscale", response.text)

    async def test_individual_resource_usage_uses_total_and_available_memory(self) -> None:
        class UnexpectedOllama:
            def chat(self, messages, tools):
                raise AssertionError("O uso de recursos deve ser formatado diretamente")

        service = AgentService(
            allowed_chat_ids=frozenset({"123"}),
            ollama=UnexpectedOllama(),
            tool_functions={
                "get_resource_usage": lambda: {
                    "cpu_percent": 0.63,
                    "memory_available_bytes": 1524060200,
                    "memory_available_gib": 1.42,
                },
                "get_system_info": lambda: {
                    "memory_bytes": 7710375936,
                    "memory_human": "7.18 GiB",
                },
            },
        )

        response = await service.answer(123, "Mostre o uso de memória e CPU")

        self.assertIn("CPU: 0.63% (média no período consultado)", response.text)
        self.assertIn("RAM utilizada estimada: 5.76 GiB (80.23%)", response.text)
        self.assertIn("RAM disponível (média): 1.42 GiB", response.text)


if __name__ == "__main__":
    unittest.main()
