import unittest

from ia_admin.agent.service import direct_readonly_tool


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


if __name__ == "__main__":
    unittest.main()
