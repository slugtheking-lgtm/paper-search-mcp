import unittest
from unittest.mock import Mock, call, patch

import requests

from paper_search_mcp.academic_platforms.crossref import (
    CROSSREF_FINANCE_QUERY,
    CrossRefSearcher,
)


class TestCrossrefParameters(unittest.TestCase):
    def setUp(self):
        self.searcher = CrossRefSearcher(mailto="researcher@example.com")

    def test_query_is_plain_text_not_a_quoted_or_boolean_expression(self):
        params = self.searcher._base_search_params(
            "  momentum factor  ", "relevance", None, None
        )
        self.assertEqual(params["query"], "momentum factor")
        self.assertNotEqual(params["query"], '"momentum factor"')
        self.assertNotIn("AND", params["query"])
        self.assertNotIn("OR", params["query"])

    def test_query_is_not_rewritten_and_author_is_normalized(self):
        params = self.searcher._base_search_params(
            "momentum   factor", "relevance", None, '"Clifford  Asness"'
        )
        self.assertEqual(params["query"], "momentum   factor")
        self.assertEqual(params["query.author"], "Clifford Asness")

    def test_empty_query_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "query must not be empty"):
            self.searcher.search("   ")

    def test_year_filter_supports_all_four_formats(self):
        expected = {
            "2024": "from-pub-date:2024-01-01,until-pub-date:2024-12-31",
            "2020-2026": "from-pub-date:2020-01-01,until-pub-date:2026-12-31",
            "2020-": "from-pub-date:2020-01-01",
            "-2020": "until-pub-date:2020-12-31",
        }
        for value, result in expected.items():
            with self.subTest(value=value):
                self.assertEqual(self.searcher._build_year_filter(value), result)

    def test_year_filter_rejects_invalid_and_reversed_ranges(self):
        for value in ("20", "2020/2024", "2020 - 2024", "2020-24"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "YYYY"):
                    self.searcher._build_year_filter(value)
        with self.assertRaisesRegex(ValueError, "start must not be greater"):
            self.searcher._build_year_filter("2026-2020")

    def test_complete_native_parameter_mapping(self):
        params = self.searcher._base_search_params(
            "momentum factor", "date", "2020-2026", "ABC"
        )
        self.assertEqual(
            params,
            {
                "query": "momentum factor",
                "query.author": "ABC",
                "query.bibliographic": CROSSREF_FINANCE_QUERY,
                "filter": "from-pub-date:2020-01-01,until-pub-date:2026-12-31",
                "sort": "published",
                "order": "desc",
                "mailto": "researcher@example.com",
            },
        )

    def test_optional_author_and_year_are_omitted(self):
        params = self.searcher._base_search_params(
            "asset pricing", "relevance", None, None
        )
        self.assertNotIn("query.author", params)
        self.assertNotIn("filter", params)

    def test_sort_mapping_uses_crossref_fields(self):
        self.assertEqual(self.searcher._map_sort("relevance"), "relevance")
        self.assertEqual(self.searcher._map_sort("date"), "published")
        self.assertEqual(self.searcher._map_sort("recency"), "updated")
        with self.assertRaisesRegex(ValueError, "relevance, date, recency"):
            self.searcher._map_sort("updated")

    def test_max_results_must_be_a_positive_integer(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.searcher._validate_max_results(value)

    def test_250_results_use_one_request_without_cursor(self):
        payload = {"message": {"items": [{"id": 1}]}}
        with patch.object(
            self.searcher, "_request_json", return_value=payload
        ) as request, patch.object(
            self.searcher, "_parse_crossref_item", return_value="paper"
        ):
            papers = self.searcher.search(
                "momentum factor", max_results=250, sorted_by="relevance"
            )

        self.assertEqual(papers, ["paper"])
        sent = request.call_args.args[0]
        self.assertEqual(sent["rows"], 250)
        self.assertNotIn("cursor", sent)

    def test_cursor_pagination_uses_1000_1000_500(self):
        pages = [
            {
                "message": {
                    "items": [{"id": i} for i in range(1000)],
                    "next-cursor": "c2",
                }
            },
            {
                "message": {
                    "items": [{"id": i} for i in range(1000, 2000)],
                    "next-cursor": "c3",
                }
            },
            {
                "message": {
                    "items": [{"id": i} for i in range(2000, 2500)],
                    "next-cursor": "still-present-on-last-page",
                }
            },
        ]
        with patch.object(
            self.searcher, "_request_json", side_effect=pages
        ) as request, patch.object(
            self.searcher, "_parse_crossref_item", side_effect=lambda item: item
        ):
            papers = self.searcher.search(
                "momentum factor", max_results=2500, sorted_by="recency"
            )

        self.assertEqual(len(papers), 2500)
        calls = request.call_args_list
        self.assertEqual([c.args[0]["rows"] for c in calls], [1000, 1000, 500])
        self.assertEqual([c.args[0]["cursor"] for c in calls], ["*", "c2", "c3"])
        self.assertTrue(all(c.args[0]["sort"] == "updated" for c in calls))

    def test_short_page_stops_even_when_next_cursor_exists(self):
        page = {
            "message": {
                "items": [{"id": 1}],
                "next-cursor": "cursor-that-must-not-be-used",
            }
        }
        with patch.object(
            self.searcher, "_request_json", return_value=page
        ) as request, patch.object(
            self.searcher, "_parse_crossref_item", side_effect=lambda item: item
        ):
            papers = self.searcher.search("asset pricing", max_results=1001)
        self.assertEqual(papers, [{"id": 1}])
        request.assert_called_once()

    def test_http_error_is_reported_instead_of_becoming_zero_results(self):
        response = Mock(status_code=400, text="bad filter")
        response.json.return_value = {"message": "invalid parameter"}
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        self.searcher.session.get = Mock(return_value=response)
        with self.assertRaisesRegex(RuntimeError, "status=400.*invalid parameter"):
            self.searcher._request_json({})

    def test_429_is_retried_once(self):
        limited = Mock(status_code=429)
        success = Mock(status_code=200)
        success.raise_for_status.return_value = None
        success.json.return_value = {"message": {"items": []}}
        self.searcher.session.get = Mock(side_effect=[limited, success])
        with patch("paper_search_mcp.academic_platforms.crossref.time.sleep") as sleep:
            result = self.searcher._request_json({"rows": 1})
        self.assertEqual(result, {"message": {"items": []}})
        self.assertEqual(self.searcher.session.get.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_mailto_is_used_in_params_and_user_agent(self):
        params = self.searcher._base_search_params(
            "asset pricing", "relevance", None, None
        )
        self.assertEqual(params["mailto"], "researcher@example.com")
        self.assertIn(
            "mailto:researcher@example.com", self.searcher.session.headers["User-Agent"]
        )


if __name__ == "__main__":
    unittest.main()
