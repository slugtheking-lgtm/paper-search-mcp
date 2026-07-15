import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from paper_search_mcp.api import app


class TestPaperSearchAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_does_not_call_providers(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_search_forwards_public_parameters_and_returns_only_papers(self):
        paper = {
            "paper_id": "paper-1",
            "title": "Asset Pricing",
            "authors": ["Jane Doe"],
            "abstract": "Abstract",
            "doi": "10.1/example",
            "published_date": "2025-01-01T00:00:00",
            "pdf_url": None,
            "url": "https://example.test/paper-1",
            "sources": ["openalex", "crossref"],
            "topics": ["Finance"],
            "citations": 12,
        }
        with patch(
            "paper_search_mcp.api.search_papers",
            new=AsyncMock(return_value={"papers": [paper]}),
        ) as search:
            response = self.client.post(
                "/search",
                json={
                    "query": "  asset   pricing ",
                    "year": "2020-",
                    "sources": "openalex, crossref",
                    "max_results": 20,
                    "author": "  Jane   Doe ",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"papers": [paper]})
        search.assert_awaited_once_with(
            query="asset pricing",
            year="2020-",
            sources="openalex,crossref",
            max_results=20,
            author="Jane Doe",
        )

    def test_request_validation_rejects_bad_input(self):
        for payload in (
            {"query": "   "},
            {"query": "finance", "year": "2020/2024"},
            {"query": "finance", "year": "2025-2020"},
            {"query": "finance", "max_results": 0},
            {"query": "finance", "unknown": True},
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/search", json=payload)
                self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
