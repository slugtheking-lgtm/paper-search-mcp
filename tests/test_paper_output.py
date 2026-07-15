import json
import unittest
from datetime import datetime

from paper_search_mcp.paper import Paper


class TestPaperPublicOutput(unittest.TestCase):
    def test_compact_schema_uses_lists_nulls_and_combined_topics(self):
        paper = Paper(
            paper_id="paper-1",
            title="التمويل الأخضر",
            authors=["Alice", "Bob"],
            abstract="Abstract",
            doi="",
            published_date=datetime(2024, 5, 1),
            pdf_url="",
            url="https://example.test/paper-1",
            source="doaj",
            categories=["Finance", "Economics"],
            keywords=["Economics", "Green finance"],
        )

        result = paper.to_dict()

        self.assertEqual(
            list(result),
            [
                "paper_id",
                "title",
                "authors",
                "abstract",
                "doi",
                "published_date",
                "pdf_url",
                "url",
                "sources",
                "topics",
                "citations",
            ],
        )
        self.assertEqual(result["authors"], ["Alice", "Bob"])
        self.assertEqual(result["sources"], ["doaj"])
        self.assertEqual(result["topics"], ["Finance", "Economics", "Green finance"])
        self.assertIsNone(result["doi"])
        self.assertIsNone(result["pdf_url"])
        self.assertIsNone(result["citations"])
        self.assertIn("التمويل الأخضر", json.dumps(result, ensure_ascii=False))

    def test_nullable_provider_values_are_safe_for_fastapi_output(self):
        paper = Paper(
            paper_id="paper-2",
            title="Paper without abstract",
            authors=["Alice", None, ""],
            abstract=None,
            doi=None,
            published_date=None,
            pdf_url=None,
            url=None,
            source="semantic",
            citations="unknown",
        )

        result = paper.to_dict()

        self.assertEqual(result["authors"], ["Alice"])
        self.assertIsNone(result["abstract"])
        self.assertIsNone(result["doi"])
        self.assertIsNone(result["pdf_url"])
        self.assertIsNone(result["url"])
        self.assertIsNone(result["citations"])


if __name__ == "__main__":
    unittest.main()
