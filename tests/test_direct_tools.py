import unittest

from ia_admin.agent.service import AgentService, direct_readonly_tool


class DirectReadonlyToolTests(unittest.TestCase):
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
        )
        for question, expected in cases:
            with self.subTest(question=question):
                self.assertEqual(direct_readonly_tool(question), expected)


class OllamaFormattingTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
