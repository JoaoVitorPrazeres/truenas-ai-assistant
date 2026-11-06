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
        class FakeOllama:
            def __init__(self) -> None:
                self.messages = None

            def chat(self, messages, tools):
                self.messages = messages
                return {"content": "Resumo validado do servidor."}

        called = []

        def make_function(name):
            def function():
                called.append(name)
                return {"status": name}
            return function

        ollama = FakeOllama()
        tool_names = (
            "get_system_info",
            "get_resource_usage",
            "get_pools",
            "get_disks",
            "get_disk_temperatures",
            "get_alerts",
            "get_scrub_status",
        )
        service = AgentService(
            allowed_chat_ids=frozenset({"123"}),
            ollama=ollama,
            tool_functions={name: make_function(name) for name in tool_names},
        )

        response = await service.answer(
            123,
            "Faça um resumo do estado geral do servidor",
        )

        self.assertEqual(response.text, "Resumo validado do servidor.")
        self.assertEqual(set(called), set(tool_names))


if __name__ == "__main__":
    unittest.main()
