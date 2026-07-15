import unittest
from unittest.mock import Mock, call, patch

import requests

from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher


class TestOpenAlexParameters(unittest.TestCase):
    def setUp(self):
        self.searcher = OpenAlexSearcher(api_key="test-openalex-key")

    def test_query_is_normalized_as_a_quoted_phrase(self):
        self.assertEqual(
            self.searcher._normalize_phrase('  "momentum   factor"  ', "query"),
            '"momentum factor"',
        )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            self.searcher._normalize_phrase("   ", "query")
        with self.assertRaisesRegex(ValueError, "embedded double quotes"):
            self.searcher._normalize_phrase('momentum "OR" factor', "query")

    def test_year_filter_supports_all_four_formats(self):
        expected = {
            "2024": "publication_year:2024",
            "2020-2026": "publication_year:2020-2026",
            "2020-": "from_publication_date:2020-01-01",
            "-2020": "to_publication_date:2020-12-31",
        }
        for value, result in expected.items():
            with self.subTest(value=value):
                self.assertEqual(self.searcher._build_year_filter(value), result)

    def test_year_filter_rejects_invalid_or_reversed_ranges(self):
        for value in ("20", "2020/2024", "2020 - 2024", "2020-24"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "YYYY"):
                    self.searcher._build_year_filter(value)
        with self.assertRaisesRegex(ValueError, "start must not be greater"):
            self.searcher._build_year_filter("2026-2020")

    def test_filter_order_is_finance_year_author_then_work_type(self):
        self.assertEqual(
            self.searcher._build_filter("2020-2026", "A123456789"),
            "topics.field.id:20,publication_year:2020-2026,"
            "authorships.author.id:A123456789,type:article|review",
        )

    def test_max_results_must_be_a_positive_integer(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.searcher._validate_max_results(value)

    def test_author_name_is_resolved_to_openalex_id(self):
        payload = {
            "results": [
                {"id": "https://openalex.org/A111", "display_name": "Another Author"},
                {"id": "https://openalex.org/A222", "display_name": "Clifford Asness"},
            ]
        }
        with patch.object(self.searcher, "_request_json", return_value=payload) as request:
            author_id = self.searcher._resolve_author_id(' "Clifford   Asness" ')

        self.assertEqual(author_id, "A222")
        request.assert_called_once_with(
            self.searcher.AUTHORS_URL,
            {"search": "Clifford Asness", "per_page": 5, "api_key": "test-openalex-key"},
        )

    def test_no_author_match_returns_no_works_without_work_request(self):
        with patch.object(self.searcher, "_resolve_author_id", return_value=None), patch.object(
            self.searcher, "_request_json"
        ) as request:
            papers = self.searcher.search("momentum factor", author="Nobody")
        self.assertEqual(papers, [])
        request.assert_not_called()

    def test_single_page_request_uses_native_parameters(self):
        with patch.object(
            self.searcher,
            "_request_json",
            return_value={"results": [{"id": 1}], "meta": {}},
        ) as request, patch.object(
            self.searcher, "_parse_work", return_value="paper"
        ):
            papers = self.searcher.search(
                "  momentum   factor ",
                max_results=50,
                year="2024",
            )

        self.assertEqual(papers, ["paper"])
        request.assert_called_once_with(
            self.searcher.WORKS_URL,
            {
                "search": '"momentum factor"',
                "filter": "topics.field.id:20,publication_year:2024,type:article|review",
                "sort": "relevance_score:desc",
                "per_page": 50,
                "api_key": "test-openalex-key",
            },
        )

    def test_cursor_pagination_uses_100_100_50(self):
        pages = [
            {"results": [{"id": i} for i in range(100)], "meta": {"next_cursor": "c2"}},
            {"results": [{"id": i} for i in range(100, 200)], "meta": {"next_cursor": "c3"}},
            {"results": [{"id": i} for i in range(200, 250)], "meta": {"next_cursor": "c4"}},
        ]
        with patch.object(
            self.searcher, "_request_json", side_effect=pages
        ) as request, patch.object(
            self.searcher, "_parse_work", side_effect=lambda item: item
        ):
            papers = self.searcher.search("momentum factor", max_results=250)

        self.assertEqual(len(papers), 250)
        base = {
            "search": '"momentum factor"',
            "filter": "topics.field.id:20,type:article|review",
            "sort": "relevance_score:desc",
            "api_key": "test-openalex-key",
        }
        self.assertEqual(
            request.call_args_list,
            [
                call(self.searcher.WORKS_URL, {**base, "per_page": 100, "cursor": "*"}),
                call(self.searcher.WORKS_URL, {**base, "per_page": 100, "cursor": "c2"}),
                call(self.searcher.WORKS_URL, {**base, "per_page": 50, "cursor": "c3"}),
            ],
        )

    def test_cursor_pagination_stops_without_next_cursor(self):
        page = {"results": [{"id": 1}], "meta": {"next_cursor": None}}
        with patch.object(
            self.searcher, "_request_json", return_value=page
        ) as request, patch.object(
            self.searcher, "_parse_work", side_effect=lambda item: item
        ):
            papers = self.searcher.search("asset pricing", max_results=101)
        self.assertEqual(papers, [{"id": 1}])
        request.assert_called_once()

    def test_http_errors_are_reported_instead_of_becoming_empty_results(self):
        response = Mock(status_code=429, text="rate limited")
        response.json.return_value = {"message": "insufficient budget"}
        error = requests.HTTPError(response=response)
        self.searcher.session.get = Mock(side_effect=error)
        with self.assertRaisesRegex(RuntimeError, "status=429.*insufficient budget"):
            self.searcher._request_json(self.searcher.WORKS_URL, {})


if __name__ == "__main__":
    unittest.main()
