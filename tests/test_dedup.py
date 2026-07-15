import unittest

from paper_search_mcp.dedup import (
    dedupe_paper_dicts,
    identity_keys,
    normalize_doi,
    sort_papers_by_date_desc,
)


class TestPaperDeduplication(unittest.TestCase):
    @staticmethod
    def paper(doi, title, authors, paper_id=""):
        return {
            "doi": doi,
            "title": title,
            "authors": authors,
            "paper_id": paper_id or doi,
        }

    def test_different_dois_with_same_title_and_authors_are_merged(self):
        first = self.paper(
            "10.1/first",
            "Machine Learning in Empirical Asset Pricing",
            "Doe, Jane; Smith, John",
        )
        duplicate = self.paper(
            "10.2/second",
            "Machine learning in empirical asset-pricing",
            "Jane Doe; John Smith",
        )

        self.assertEqual(dedupe_paper_dicts([first, duplicate]), [first])

    def test_same_title_with_different_authors_is_not_merged(self):
        first = self.paper("10.1/first", "Essays in Finance", "Jane Doe")
        second = self.paper("10.2/second", "Essays in Finance", "John Smith")
        self.assertEqual(dedupe_paper_dicts([first, second]), [first, second])

    def test_missing_authors_does_not_trigger_cross_doi_title_merge(self):
        first = self.paper("10.1/first", "Essays in Finance", "")
        second = self.paper("10.2/second", "Essays in Finance", "")
        self.assertEqual(dedupe_paper_dicts([first, second]), [first, second])

    def test_figshare_version_dois_are_canonicalized(self):
        self.assertEqual(
            normalize_doi("https://doi.org/10.6084/m9.figshare.19144763.v2"),
            "10.6084/m9.figshare.19144763",
        )
        first = self.paper(
            "10.6084/m9.figshare.19144763",
            "Comparative Study of Digital Currency",
            "Jane Doe",
        )
        version = self.paper(
            "10.6084/m9.figshare.19144763.v2",
            "Uploaded file: Comparative Study of Digital Currency",
            "Jane Doe",
        )
        self.assertEqual(dedupe_paper_dicts([first, version]), [first])

    def test_identity_keys_normalize_author_name_order(self):
        first = identity_keys(
            "10.1/first", "Asset Pricing", ["Asness, Clifford S."]
        )
        second = identity_keys(
            "10.2/second", "Asset Pricing", "Clifford S. Asness"
        )
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1], second[1])

    def test_fallback_paper_id_is_deduplicated(self):
        first = self.paper("", "Untitled", "", paper_id="source-123")
        second = self.paper("", "Untitled copy", "", paper_id="SOURCE-123")
        self.assertEqual(dedupe_paper_dicts([first, second]), [first])

    def test_duplicate_sources_and_topics_are_merged(self):
        first = {
            **self.paper("10.1/shared", "Asset Pricing", ["Jane Doe"]),
            "sources": ["arxiv"],
            "topics": ["Finance"],
            "citations": None,
        }
        duplicate = {
            **self.paper("10.1/shared", "Asset Pricing", ["Jane Doe"]),
            "sources": ["semantic", "openalex"],
            "topics": ["Economics", "Finance"],
            "citations": 12,
        }

        merged = dedupe_paper_dicts([first, duplicate])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["sources"], ["arxiv", "semantic", "openalex"])
        self.assertEqual(merged[0]["topics"], ["Finance", "Economics"])
        self.assertEqual(merged[0]["citations"], 12)

    def test_publication_dates_sort_descending_with_unknown_last(self):
        papers = [
            {"paper_id": "old", "published_date": "2020-01-01T00:00:00"},
            {"paper_id": "unknown", "published_date": None},
            {"paper_id": "new", "published_date": "2025-06-01T00:00:00"},
        ]
        self.assertEqual(
            [paper["paper_id"] for paper in sort_papers_by_date_desc(papers)],
            ["new", "old", "unknown"],
        )


if __name__ == "__main__":
    unittest.main()
