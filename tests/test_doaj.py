from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import unquote

import requests

from paper_search_mcp.academic_platforms.doaj import (
    DOAJ_FINANCE_FILTER,
    DOAJSearcher,
)


class TestDOAJQueryBuilding(unittest.TestCase):
    def test_query_is_normalized_as_literal_phrase(self):
        self.assertEqual(
            DOAJSearcher._normalize_phrase('  "momentum   factor"  ', "query"),
            '"momentum factor"',
        )

    def test_empty_and_injected_queries_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            DOAJSearcher._build_search_query("  ")
        with self.assertRaisesRegex(ValueError, "plain text"):
            DOAJSearcher._build_search_query('factor" OR bibjson.year:2024')

    def test_year_formats(self):
        cases = {
            "2024": "bibjson.year:2024",
            "2020-2024": "bibjson.year:[2020 TO 2024]",
            "2020-": "bibjson.year:>=2020",
            "-2020": "bibjson.year:<=2020",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(DOAJSearcher._build_year_condition(value), expected)

    def test_invalid_year_formats_are_rejected(self):
        for value in ("20", "2020/2024", "2020 - 2024", "2020-24", ""):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "YYYY, YYYY-YYYY, YYYY-, or -YYYY"
            ):
                DOAJSearcher._build_year_condition(value)
        with self.assertRaisesRegex(ValueError, "start must not be greater"):
            DOAJSearcher._build_year_condition("2024-2020")

    def test_full_query_uses_doaj_syntax_and_required_order(self):
        result = DOAJSearcher._build_search_query(
            "momentum factor", year="2020-2026", author="ABC"
        )
        self.assertEqual(
            result,
            f'"momentum factor" AND {DOAJ_FINANCE_FILTER} '
            'AND bibjson.author.name:"ABC" '
            "AND bibjson.year:[2020 TO 2026]",
        )
        self.assertNotIn("yearPublished", result)
        self.assertNotIn("submittedDate", result)
        self.assertNotIn("cat:q-fin", result)

    def test_max_results_must_be_positive_integer(self):
        DOAJSearcher._validate_max_results(1)
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DOAJSearcher._validate_max_results(value)


class TestDOAJPagination(unittest.TestCase):
    def setUp(self):
        self.searcher = DOAJSearcher(api_key="test-key")

    @staticmethod
    def _response(results, total=250):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"results": results, "total": total},
        )

    @patch("paper_search_mcp.academic_platforms.doaj.time.sleep")
    def test_250_results_uses_three_exact_api_requests(self, sleep):
        responses = [
            self._response(list(range(100))),
            self._response(list(range(100, 200))),
            self._response(list(range(200, 250))),
        ]
        with patch.object(self.searcher.session, "get", side_effect=responses) as request:
            with patch.object(
                self.searcher, "_parse_doaj_item", side_effect=lambda item: ("paper", item)
            ):
                results = self.searcher.search(
                    "momentum factor",
                    year="2020-2026",
                    author="ABC",
                    max_results=250,
                )

        self.assertEqual(len(results), 250)
        params = [call.kwargs["params"] for call in request.call_args_list]
        self.assertEqual([item["page"] for item in params], [1, 2, 3])
        self.assertEqual([item["pageSize"] for item in params], [100, 100, 50])
        self.assertTrue(all(set(item) == {"page", "pageSize"} for item in params))
        decoded_query = unquote(request.call_args_list[0].args[0].rsplit("/", 1)[-1])
        self.assertEqual(
            decoded_query,
            DOAJSearcher._build_search_query(
                "momentum factor", year="2020-2026", author="ABC"
            ),
        )
        self.assertEqual(sleep.call_count, 2)

    def test_relevance_omits_sort_and_short_page_stops(self):
        response = self._response(list(range(20)), total=20)
        with patch.object(self.searcher.session, "get", return_value=response) as request:
            with patch.object(
                self.searcher, "_parse_doaj_item", side_effect=lambda item: ("paper", item)
            ):
                results = self.searcher.search("finance", max_results=250)
        self.assertEqual(len(results), 20)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["params"], {"page": 1, "pageSize": 100})

    def test_empty_page_stops(self):
        response = self._response([], total=0)
        with patch.object(self.searcher.session, "get", return_value=response) as request:
            self.assertEqual(self.searcher.search("finance", max_results=250), [])
        request.assert_called_once()

    def test_http_error_is_reported_instead_of_becoming_empty_results(self):
        response = SimpleNamespace(status_code=400)
        error = requests.HTTPError("bad sort", response=response)
        with patch.object(self.searcher.session, "get", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "status=400"):
                self.searcher.search("finance")


class TestDOAJParsing(unittest.TestCase):
    def test_parse_doaj_item_minimal(self):
        item = {
            "id": "abc123",
            "bibjson": {
                "title": "DOAJ Parser Test",
                "author": [{"name": "Alice"}],
                "identifier": [{"type": "doi", "id": "10.1000/doaj-test"}],
                "year": "2023",
                "link": [{"type": "fulltext", "url": "https://example.org/test.pdf"}],
            },
        }
        paper = DOAJSearcher(api_key="test-key")._parse_doaj_item(item)
        self.assertIsNotNone(paper)
        self.assertEqual(paper.source, "doaj")
        self.assertEqual(paper.title, "DOAJ Parser Test")
        self.assertEqual(paper.doi, "10.1000/doaj-test")

    def test_parse_doaj_item_invalid(self):
        self.assertIsNone(
            DOAJSearcher(api_key="test-key")._parse_doaj_item({"bibjson": {}})
        )


if __name__ == "__main__":
    unittest.main()
