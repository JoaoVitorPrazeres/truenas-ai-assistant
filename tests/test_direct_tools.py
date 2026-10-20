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


if __name__ == "__main__":
    unittest.main()
