from types import SimpleNamespace
import unittest
from unittest.mock import patch

from paper_search_mcp.academic_platforms.core import CORE_FINANCE_FILTER, CORESearcher


class TestCOREQueryBuilding(unittest.TestCase):
    def test_query_is_normalized_as_literal_phrase(self):
        self.assertEqual(
            CORESearcher._normalize_phrase('  "momentum   factor"  ', "query"),
            '"momentum factor"',
        )

    def test_empty_and_injected_queries_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            CORESearcher._build_search_query("  ")
        with self.assertRaisesRegex(ValueError, "plain text"):
            CORESearcher._build_search_query('factor" OR authors:"Injected')

    def test_year_formats(self):
        cases = {
            "2024": ["yearPublished:2024"],
            "2020-2024": [
                'yearPublished>="2020"',
                'yearPublished<="2024"',
            ],
            "2020-": ['yearPublished>="2020"'],
            "-2020": ['yearPublished<="2020"'],
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(CORESearcher._build_year_conditions(value), expected)

    def test_invalid_year_formats_are_rejected(self):
        for value in ("20", "2020/2024", "2020 - 2024", "2020-24", ""):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "YYYY, YYYY-YYYY, YYYY-, or -YYYY"
            ):
                CORESearcher._build_year_conditions(value)
        with self.assertRaisesRegex(ValueError, "start must not be greater"):
            CORESearcher._build_year_conditions("2024-2020")

    def test_full_query_uses_core_syntax_and_required_order(self):
        result = CORESearcher._build_search_query(
            "momentum factor", year="2020-2026", author="ABC"
        )
        self.assertEqual(
            result,
            '"momentum factor" AND authors:"ABC" '
            'AND yearPublished>="2020" AND yearPublished<="2026" '
            f"AND {CORE_FINANCE_FILTER}",
        )
        self.assertNotIn("submittedDate", result)
        self.assertNotIn("cat:q-fin", result)

    def test_sort_mapping(self):
        self.assertEqual(CORESearcher._map_sort("relevance"), "relevance")
        self.assertEqual(CORESearcher._map_sort("date"), "recency")
        self.assertEqual(CORESearcher._map_sort("recency"), "recency")
        with self.assertRaises(ValueError):
            CORESearcher._map_sort("updated")

    def test_max_results_must_be_positive_integer(self):
        CORESearcher._validate_max_results(1)
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CORESearcher._validate_max_results(value)


class TestCOREPagination(unittest.TestCase):
    def setUp(self):
        self.searcher = CORESearcher(api_key="test-key")

    def test_250_results_uses_three_exact_api_requests(self):
        responses = [
            SimpleNamespace(json=lambda: {"results": list(range(100))}),
            SimpleNamespace(json=lambda: {"results": list(range(100, 200))}),
            SimpleNamespace(json=lambda: {"results": list(range(200, 250))}),
        ]
        with patch.object(self.searcher, "_request_page", side_effect=responses) as request:
            with patch.object(self.searcher, "_parse_item", side_effect=lambda item: ("paper", item)):
                results = self.searcher.search(
                    "momentum factor",
                    year="2020-2026",
                    author="ABC",
                    max_results=250,
                    sorted_by="date",
                )

        self.assertEqual(len(results), 250)
        params = [call.args[0] for call in request.call_args_list]
        self.assertEqual([item["limit"] for item in params], [100, 100, 50])
        self.assertEqual([item["offset"] for item in params], [0, 100, 200])
        self.assertTrue(all(item["sort"] == "recency" for item in params))
        self.assertTrue(all(set(item) == {"q", "limit", "offset", "sort"} for item in params))
        self.assertEqual(params[0]["q"], params[1]["q"])

    def test_short_page_stops_pagination(self):
        response = SimpleNamespace(json=lambda: {"results": list(range(20))})
        with patch.object(self.searcher, "_request_page", return_value=response) as request:
            with patch.object(self.searcher, "_parse_item", side_effect=lambda item: ("paper", item)):
                results = self.searcher.search("finance", max_results=250)
        self.assertEqual(len(results), 20)
        request.assert_called_once()

    def test_empty_page_stops_pagination(self):
        response = SimpleNamespace(json=lambda: {"results": []})
        with patch.object(self.searcher, "_request_page", return_value=response) as request:
            self.assertEqual(self.searcher.search("finance", max_results=250), [])
        request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
