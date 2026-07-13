from types import SimpleNamespace
import unittest
from unittest.mock import patch

from paper_search_mcp.academic_platforms.semantic import SemanticSearcher


class TestSemanticParameters(unittest.TestCase):
    def test_year_formats_are_passed_through(self):
        for value in ("2024", "2020-2026", "2020-", "-2020"):
            with self.subTest(value=value):
                self.assertEqual(SemanticSearcher._validate_year(value), value)

    def test_invalid_year_formats_are_rejected(self):
        for value in ("20", "2020/2024", "2020 - 2024", "2020-24", ""):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "YYYY, YYYY-YYYY, YYYY-, or -YYYY"
            ):
                SemanticSearcher._validate_year(value)
        with self.assertRaisesRegex(ValueError, "start must not be greater"):
            SemanticSearcher._validate_year("2026-2020")

    def test_recency_falls_back_to_relevance(self):
        self.assertEqual(SemanticSearcher._validate_sort("recency"), "relevance")
        self.assertEqual(SemanticSearcher._validate_sort("date"), "date")
        with self.assertRaises(ValueError):
            SemanticSearcher._validate_sort("updated")

    def test_max_results_must_be_positive(self):
        SemanticSearcher._validate_max_results(1)
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SemanticSearcher._validate_max_results(value)


class TestSemanticEndpoints(unittest.TestCase):
    def setUp(self):
        self.searcher = SemanticSearcher()

    @staticmethod
    def _items(start, count):
        return [{"paperId": str(i), "title": f"Paper {i}"} for i in range(start, start + count)]

    def test_relevance_uses_limit_and_offset_pages(self):
        payloads = [
            {"data": self._items(0, 100), "next": 100},
            {"data": self._items(100, 100), "next": 200},
            {"data": self._items(200, 50), "next": 250},
        ]
        with patch.object(self.searcher, "_get_json", side_effect=payloads) as request:
            with patch.object(self.searcher, "_parse_paper", side_effect=lambda item: item):
                papers = self.searcher.search(
                    "momentum factor", year="2020-2026", max_results=250
                )

        self.assertEqual(len(papers), 250)
        self.assertEqual([call.args[0] for call in request.call_args_list], ["paper/search"] * 3)
        params = [call.args[1] for call in request.call_args_list]
        self.assertEqual([item["limit"] for item in params], [100, 100, 50])
        self.assertEqual([item["offset"] for item in params], [0, 100, 200])
        self.assertTrue(all(item["query"] == "momentum factor" for item in params))
        self.assertTrue(all(item["year"] == "2020-2026" for item in params))
        self.assertTrue(
            all(item["fieldsOfStudy"] == "Business,Economics" for item in params)
        )
        self.assertTrue(all("sort" not in item and "token" not in item for item in params))

    def test_date_uses_bulk_token_pagination(self):
        payloads = [
            {"data": self._items(0, 1000), "token": "next-token"},
            {"data": self._items(1000, 250)},
        ]
        with patch.object(self.searcher, "_get_json", side_effect=payloads) as request:
            with patch.object(self.searcher, "_parse_paper", side_effect=lambda item: item):
                papers = self.searcher.search(
                    "momentum factor",
                    year="2020-2026",
                    max_results=1250,
                    sorted_by="date",
                )

        self.assertEqual(len(papers), 1250)
        self.assertEqual(
            [call.args[0] for call in request.call_args_list],
            ["paper/search/bulk", "paper/search/bulk"],
        )
        first, second = [call.args[1] for call in request.call_args_list]
        self.assertEqual(first["sort"], "publicationDate:desc")
        self.assertNotIn("token", first)
        self.assertEqual(second["token"], "next-token")
        self.assertNotIn("limit", first)
        self.assertNotIn("offset", first)

    def test_recency_uses_native_relevance_endpoint(self):
        with patch.object(
            self.searcher, "_get_json", return_value={"data": self._items(0, 2)}
        ) as request:
            with patch.object(self.searcher, "_parse_paper", side_effect=lambda item: item):
                papers = self.searcher.search(
                    "momentum", max_results=2, sorted_by="recency"
                )
        self.assertEqual(len(papers), 2)
        self.assertEqual(request.call_args.args[0], "paper/search")
        self.assertNotIn("sort", request.call_args.args[1])

    def test_relevance_over_1000_switches_to_bulk_without_sort(self):
        with patch.object(
            self.searcher,
            "_get_json",
            return_value={"data": self._items(0, 1001)},
        ) as request:
            with patch.object(self.searcher, "_parse_paper", side_effect=lambda item: item):
                papers = self.searcher.search("momentum", max_results=1001)
        self.assertEqual(len(papers), 1001)
        self.assertEqual(request.call_args.args[0], "paper/search/bulk")
        self.assertNotIn("sort", request.call_args.args[1])

    def test_author_uses_two_stage_workflow_and_local_filters(self):
        papers = [
            {
                "paperId": "new",
                "title": "Momentum Factor in Markets",
                "abstract": "Finance evidence",
                "year": 2024,
                "publicationDate": "2024-05-01",
                "fieldsOfStudy": ["Business"],
                "citationCount": 3,
            },
            {
                "paperId": "old",
                "title": "Momentum Factor Evidence",
                "abstract": "Economics evidence",
                "year": 2021,
                "publicationDate": "2021-01-01",
                "fieldsOfStudy": ["Economics"],
                "citationCount": 20,
            },
            {
                "paperId": "wrong-field",
                "title": "Momentum Factor Medicine",
                "abstract": "",
                "year": 2023,
                "fieldsOfStudy": ["Medicine"],
            },
            {
                "paperId": "wrong-query",
                "title": "Unrelated Finance Paper",
                "abstract": "",
                "year": 2023,
                "fieldsOfStudy": ["Business"],
            },
        ]

        def get_json(path, params):
            if path == "author/search":
                return {
                    "data": [
                        {"authorId": "other", "name": "ABC Smith"},
                        {"authorId": "exact", "name": "ABC"},
                    ]
                }
            self.assertEqual(path, "author/exact/papers")
            self.assertNotIn("query", params)
            self.assertNotIn("year", params)
            self.assertNotIn("fieldsOfStudy", params)
            return {"data": papers}

        with patch.object(self.searcher, "_get_json", side_effect=get_json) as request:
            with patch.object(self.searcher, "_parse_paper", side_effect=lambda item: item):
                results = self.searcher.search(
                    "momentum factor",
                    year="2020-2024",
                    max_results=10,
                    sorted_by="date",
                    author="ABC",
                )

        self.assertEqual([item["paperId"] for item in results], ["new", "old"])
        self.assertEqual(request.call_args_list[0].args[0], "author/search")
        self.assertEqual(request.call_args_list[0].args[1]["query"], "ABC")

    def test_api_error_is_not_silently_converted_to_no_results(self):
        with patch.object(
            self.searcher,
            "request_api",
            return_value={"error": "rate_limited", "status_code": 429, "message": "wait"},
        ):
            with self.assertRaisesRegex(RuntimeError, "status=429"):
                self.searcher.search("momentum", max_results=1)


if __name__ == "__main__":
    unittest.main()
