import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from paper_search_mcp.academic_platforms.datacite import (
    DATACITE_FINANCE_FILTER,
    DATACITE_RESOURCE_TYPE_FILTER,
    DataCiteSearcher,
)


class TestDataCiteParameters(unittest.TestCase):
    def setUp(self):
        self.searcher = DataCiteSearcher(mailto="researcher@example.com")

    def test_query_is_normalized_as_a_literal_phrase(self):
        self.assertEqual(
            self.searcher._normalize_phrase('  "momentum   factor"  ', "query"),
            '"momentum factor"',
        )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            self.searcher._normalize_phrase("   ", "query")
        with self.assertRaisesRegex(ValueError, "embedded double quotes"):
            self.searcher._normalize_phrase('momentum "OR" factor', "query")
        with self.assertRaisesRegex(ValueError, "backslashes"):
            self.searcher._normalize_phrase("momentum\\factor", "query")

    def test_year_clause_supports_all_four_formats(self):
        expected = {
            "2024": "publicationYear:2024",
            "2020-2026": "publicationYear:[2020 TO 2026]",
            "2020-": "publicationYear:[2020 TO *]",
            "-2020": "publicationYear:[* TO 2020]",
        }
        for value, result in expected.items():
            with self.subTest(value=value):
                self.assertEqual(self.searcher._build_year_clause(value), result)

    def test_year_clause_rejects_invalid_and_reversed_ranges(self):
        for value in ("20", "2020/2024", "2020 - 2024", "2020-24"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "YYYY"):
                    self.searcher._build_year_clause(value)
        with self.assertRaisesRegex(ValueError, "start must not be greater"):
            self.searcher._build_year_clause("2026-2020")

    def test_search_query_uses_content_author_year_finance_and_type_order(self):
        query = self.searcher._build_search_query(
            "momentum factor", year="2020-2026", author="Clifford Asness"
        )
        content = (
            '(titles.title:"momentum factor" OR '
            'descriptions.description:"momentum factor" OR '
            'subjects.subject:"momentum factor")'
        )
        author = 'creators.name:"Clifford Asness"'
        year = "publicationYear:[2020 TO 2026]"
        self.assertEqual(
            query,
            " AND ".join(
                [
                    content,
                    author,
                    year,
                    DATACITE_FINANCE_FILTER,
                    DATACITE_RESOURCE_TYPE_FILTER,
                ]
            ),
        )

    def test_request_params_are_always_relevance_sorted(self):
        params = self.searcher._request_params("compiled-query", 100, 1)
        self.assertEqual(
            params,
            {
                "query": "compiled-query",
                "sort": "relevance",
                "page[size]": 100,
                "page[number]": 1,
                "disable-facets": "true",
                "mailto": "researcher@example.com",
            },
        )

    def test_max_results_is_limited_to_sortable_page_range(self):
        for value in (0, -1, True, 1.5, 10_001):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "between 1 and 10000"):
                    self.searcher._validate_max_results(value)

    def test_single_page_search_uses_no_sort_argument(self):
        payload = {"data": [{"id": "10.1/example"}], "links": {"next": None}}
        parsed = SimpleNamespace(doi="10.1/example")
        with patch.object(
            self.searcher, "_request_json", return_value=payload
        ) as request, patch.object(
            self.searcher, "_parse_item", return_value=parsed
        ):
            papers = self.searcher.search(
                "momentum factor",
                max_results=100,
                year="2024",
                author="ABC",
            )

        self.assertEqual(papers, [parsed])
        sent = request.call_args.args[0]
        self.assertEqual(sent["sort"], "relevance")
        self.assertEqual(sent["page[size]"], 100)
        self.assertEqual(sent["page[number]"], 1)
        self.assertIn("publicationYear:2024", sent["query"])
        self.assertIn('creators.name:"ABC"', sent["query"])

    def test_page_pagination_keeps_1000_page_size(self):
        pages = [
            {
                "data": [{"id": i} for i in range(1000)],
                "links": {"next": "page-2"},
            },
            {
                "data": [{"id": i} for i in range(1000, 2000)],
                "links": {"next": "page-3"},
            },
            {
                "data": [{"id": i} for i in range(2000, 3000)],
                "links": {"next": "page-4"},
            },
        ]

        def parse(item):
            return SimpleNamespace(doi=f"10.1/{item['id']}")

        with patch.object(
            self.searcher, "_request_json", side_effect=pages
        ) as request, patch.object(
            self.searcher, "_parse_item", side_effect=parse
        ):
            papers = self.searcher.search("finance", max_results=2500)

        self.assertEqual(len(papers), 2500)
        sent = [item.args[0] for item in request.call_args_list]
        self.assertEqual([item["page[size]"] for item in sent], [1000, 1000, 1000])
        self.assertEqual([item["page[number]"] for item in sent], [1, 2, 3])
        self.assertTrue(all(item["sort"] == "relevance" for item in sent))

    def test_short_page_stops_pagination(self):
        payload = {
            "data": [{"id": 1}],
            "links": {"next": "must-not-be-used"},
        }
        parsed = SimpleNamespace(doi="10.1/1")
        with patch.object(
            self.searcher, "_request_json", return_value=payload
        ) as request, patch.object(
            self.searcher, "_parse_item", return_value=parsed
        ):
            papers = self.searcher.search("finance", max_results=1001)
        self.assertEqual(papers, [parsed])
        request.assert_called_once()

    def test_duplicate_versions_are_replaced_and_next_page_fills_limit(self):
        pages = [
            {
                "data": [{"id": 1}, {"id": 2}, {"id": 3}],
                "links": {"next": "page-2"},
            },
            {
                "data": [{"id": 4}, {"id": 5}, {"id": 6}],
                "links": {"next": "page-3"},
            },
        ]
        parsed = {
            1: SimpleNamespace(
                doi="10.1/version-1",
                title="Climate Finance Report",
                authors=["Jane Doe"],
                abstract="",
            ),
            2: SimpleNamespace(
                doi="10.1/version-2",
                title="Climate finance report",
                authors=["Doe, Jane"],
                abstract="A complete abstract",
            ),
            3: SimpleNamespace(
                doi="10.1/second",
                title="Green Banking",
                authors=["John Smith"],
                abstract="Abstract",
            ),
            4: SimpleNamespace(
                doi="10.1/third",
                title="Carbon Markets",
                authors=["Alex Lee"],
                abstract="Abstract",
            ),
            5: SimpleNamespace(
                doi="10.1/fourth",
                title="Fintech",
                authors=["Kim Chen"],
                abstract="Abstract",
            ),
            6: SimpleNamespace(
                doi="10.1/fifth",
                title="Investment",
                authors=["Morgan Wu"],
                abstract="Abstract",
            ),
        }

        with patch.object(
            self.searcher, "_request_json", side_effect=pages
        ) as request, patch.object(
            self.searcher,
            "_parse_item",
            side_effect=lambda item: parsed[item["id"]],
        ):
            papers = self.searcher.search("climate finance", max_results=3)

        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            [paper.doi for paper in papers],
            ["10.1/version-2", "10.1/second", "10.1/third"],
        )

    def test_parse_complete_datacite_record(self):
        item = {
            "id": "10.1234/example",
            "attributes": {
                "doi": "10.1234/EXAMPLE",
                "titles": [
                    {"title": "Alternative", "titleType": "AlternativeTitle"},
                    {"title": "Momentum Factor Research", "titleType": None},
                ],
                "creators": [
                    {"name": "Asness, Clifford"},
                    {"givenName": "John", "familyName": "Doe"},
                ],
                "descriptions": [
                    {"descriptionType": "Abstract", "description": "Abstract text."},
                    {"descriptionType": "Methods", "description": "Ignore this."},
                ],
                "published": "2024-03-12",
                "updated": "2025-01-02T03:04:05Z",
                "url": "https://repository.example/item",
                "contentUrl": ["https://repository.example/paper.pdf"],
                "subjects": [{"subject": "Finance"}, {"subject": "Asset pricing"}],
                "citationCount": 7,
                "viewCount": 11,
                "downloadCount": 5,
                "types": {
                    "resourceTypeGeneral": "Preprint",
                    "resourceType": "Working Paper",
                },
                "publisher": {"name": "Example Repository"},
                "language": "en",
                "rightsList": [{"rights": "CC BY 4.0"}],
                "relatedIdentifiers": [
                    {
                        "relationType": "References",
                        "relatedIdentifierType": "DOI",
                        "relatedIdentifier": "10.1000/reference",
                    }
                ],
            },
            "relationships": {"client": {"data": {"id": "example.repo"}}},
        }

        paper = self.searcher._parse_item(item)

        self.assertIsNotNone(paper)
        self.assertEqual(paper.paper_id, "10.1234/example")
        self.assertEqual(paper.title, "Momentum Factor Research")
        self.assertEqual(paper.authors, ["Asness, Clifford", "John Doe"])
        self.assertEqual(paper.abstract, "Abstract text.")
        self.assertEqual(paper.published_date.isoformat(), "2024-03-12T00:00:00")
        self.assertEqual(paper.updated_date.isoformat(), "2025-01-02T03:04:05+00:00")
        self.assertEqual(paper.pdf_url, "https://repository.example/paper.pdf")
        self.assertEqual(paper.categories, ["Finance", "Asset pricing"])
        self.assertEqual(paper.citations, 7)
        self.assertEqual(paper.references, ["10.1000/reference"])
        self.assertEqual(paper.extra["publisher"], "Example Repository")
        self.assertEqual(paper.extra["client_id"], "example.repo")

    def test_sparse_or_unusable_records_do_not_break_page_parsing(self):
        self.assertIsNone(self.searcher._parse_item({"attributes": {}}))
        self.assertIsNone(
            self.searcher._parse_item(
                {"id": "10.1/no-title", "attributes": {"doi": "10.1/no-title"}}
            )
        )

    def test_http_error_details_are_propagated(self):
        response = Mock(status_code=400, text="bad query")
        response.json.return_value = {
            "errors": [{"title": "Bad Request", "detail": "invalid query syntax"}]
        }
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        self.searcher.session.get = Mock(return_value=response)
        with self.assertRaisesRegex(RuntimeError, "status=400.*invalid query syntax"):
            self.searcher._request_json({})

    def test_429_uses_retry_after_and_retries(self):
        limited = Mock(status_code=429)
        limited.headers = {"Retry-After": "3"}
        success = Mock(status_code=200)
        success.raise_for_status.return_value = None
        success.json.return_value = {"data": []}
        self.searcher.session.get = Mock(side_effect=[limited, success])

        with patch("paper_search_mcp.academic_platforms.datacite.time.sleep") as sleep:
            payload = self.searcher._request_json({})

        self.assertEqual(payload, {"data": []})
        sleep.assert_called_once_with(3.0)

    def test_mailto_is_optional_and_crossref_mailto_can_be_reused(self):
        def env(name, default=""):
            return "fallback@example.com" if name == "CROSSREF_MAILTO" else default

        with patch(
            "paper_search_mcp.academic_platforms.datacite.get_env", side_effect=env
        ):
            searcher = DataCiteSearcher()

        self.assertEqual(searcher.mailto, "fallback@example.com")
        self.assertIn("mailto:fallback@example.com", searcher.session.headers["User-Agent"])


if __name__ == "__main__":
    unittest.main()
