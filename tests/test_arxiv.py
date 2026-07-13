from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from paper_search_mcp.academic_platforms.arxiv import ArxivSearcher


class TestArxivQueryBuilding(unittest.TestCase):
    def test_query_is_normalized_and_quoted(self):
        self.assertEqual(
            ArxivSearcher._normalize_phrase('  "momentum   factor"  ', "query"),
            '"momentum factor"',
        )

    def test_empty_query_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "query must not be empty"):
            ArxivSearcher._build_search_query("   ")

    def test_embedded_quotes_cannot_inject_advanced_syntax(self):
        with self.assertRaisesRegex(ValueError, "plain text"):
            ArxivSearcher._build_search_query('factor" OR cat:cs.AI')

    def test_advanced_words_without_quotes_remain_a_literal_phrase(self):
        result = ArxivSearcher._build_search_query("factor OR cat:cs.AI")
        self.assertTrue(result.startswith('all:"factor OR cat:cs.AI" AND (cat:q-fin.CP'))

    def test_year_formats(self):
        now = datetime(2026, 7, 13, 3, 20, tzinfo=timezone.utc)
        cases = {
            "2024": "submittedDate:[202401010000 TO 202412312359]",
            "2020-2024": "submittedDate:[202001010000 TO 202412312359]",
            "2020-": "submittedDate:[202001010000 TO 202607130320]",
            "-2020": "submittedDate:[199101010000 TO 202012312359]",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(
                    ArxivSearcher._build_year_filter(value, now_utc=now), expected
                )

    def test_invalid_year_formats_are_rejected(self):
        for value in ("20", "2020/2024", "2020 - 2024", "2020-24", ""):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "YYYY, YYYY-YYYY, YYYY-, or -YYYY"
            ):
                ArxivSearcher._build_year_filter(value)

    def test_reversed_year_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "start must not be greater"):
            ArxivSearcher._build_year_filter("2024-2020")

    def test_full_query_has_required_order(self):
        result = ArxivSearcher._build_search_query(
            "momentum factor", year="2020-2026", author="ABC"
        )
        self.assertEqual(
            result,
            'all:"momentum factor" AND au:"ABC" AND '
            "(cat:q-fin.CP OR cat:q-fin.EC OR cat:q-fin.GN OR cat:q-fin.MF "
            "OR cat:q-fin.PM OR cat:q-fin.PR OR cat:q-fin.RM OR cat:q-fin.ST "
            "OR cat:q-fin.TR) AND "
            "submittedDate:[202001010000 TO 202612312359]",
        )

    def test_sort_mapping(self):
        self.assertEqual(ArxivSearcher._map_sort("relevance"), "relevance")
        self.assertEqual(ArxivSearcher._map_sort("date"), "submittedDate")
        self.assertEqual(ArxivSearcher._map_sort("updated"), "lastUpdatedDate")
        with self.assertRaises(ValueError):
            ArxivSearcher._map_sort("ascending")

    def test_max_results_bounds(self):
        for value in (1, 30_000):
            ArxivSearcher._validate_max_results(value)
        for value in (0, 30_001, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ArxivSearcher._validate_max_results(value)


class TestArxivPagination(unittest.TestCase):
    @patch("paper_search_mcp.academic_platforms.arxiv.time.sleep")
    @patch("paper_search_mcp.academic_platforms.arxiv.feedparser.parse")
    def test_more_than_2000_results_uses_start_pagination(self, parse, sleep):
        first_entries = [object()] * 2_000
        second_entries = [object()]
        parse.side_effect = [
            SimpleNamespace(entries=first_entries),
            SimpleNamespace(entries=second_entries),
        ]
        searcher = ArxivSearcher()
        responses = [SimpleNamespace(content=b"first"), SimpleNamespace(content=b"second")]

        with patch.object(searcher, "_request_page", side_effect=responses) as request_page:
            with patch.object(searcher, "_parse_entry", side_effect=lambda entry: entry):
                papers = searcher.search("momentum", max_results=2_001, sorted_by="date")

        self.assertEqual(len(papers), 2_001)
        first_params = request_page.call_args_list[0].args[0]
        second_params = request_page.call_args_list[1].args[0]
        self.assertEqual(first_params["start"], 0)
        self.assertEqual(first_params["max_results"], 2_000)
        self.assertEqual(first_params["sortBy"], "submittedDate")
        self.assertEqual(first_params["sortOrder"], "descending")
        self.assertEqual(second_params["start"], 2_000)
        self.assertEqual(second_params["max_results"], 1)
        sleep.assert_called_once_with(3)

    @patch("paper_search_mcp.academic_platforms.arxiv.feedparser.parse")
    def test_parse_failure_does_not_abort_page(self, parse):
        bad, good = object(), object()
        parse.return_value = SimpleNamespace(entries=[bad, good])
        searcher = ArxivSearcher()

        def parse_entry(entry):
            if entry is bad:
                raise ValueError("bad entry")
            return entry

        with patch.object(
            searcher, "_request_page", return_value=SimpleNamespace(content=b"feed")
        ):
            with patch.object(searcher, "_parse_entry", side_effect=parse_entry):
                self.assertEqual(searcher.search("momentum", max_results=2), [good])


if __name__ == "__main__":
    unittest.main()
