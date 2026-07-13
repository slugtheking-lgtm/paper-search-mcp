import unittest
from unittest.mock import AsyncMock, patch

from paper_search_mcp import server
from paper_search_mcp import cli
from paper_search_mcp.cli import build_parser


class TestServerSources(unittest.TestCase):
    def test_only_requested_sources_are_registered(self):
        self.assertEqual(
            server.ALL_SOURCES,
            ["arxiv", "core", "doaj", "semantic", "openalex", "crossref"],
        )

    def test_source_parser_rejects_removed_sources(self):
        self.assertEqual(server._parse_sources("arxiv,removed_source,doaj"), ["arxiv", "doaj"])

    def test_cli_exposes_common_search_parameters(self):
        args = build_parser().parse_args(
            [
                "search", "momentum factor", "-y", "2020-2024",
                "-s", "arxiv", "-n", "25", "-sort", "date",
                "-au", "Clifford Asness",
            ]
        )
        self.assertEqual(args.query, "momentum factor")
        self.assertEqual(args.year, "2020-2024")
        self.assertEqual(args.sources, "arxiv")
        self.assertEqual(args.max_results, 25)
        self.assertEqual(args.sorted_by, "date")
        self.assertEqual(args.author, "Clifford Asness")


class TestOpenAlexPublicDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_unified_search_forwards_common_parameters(self):
        with patch.object(server, "async_search", new=AsyncMock(return_value=[])) as search:
            result = await server.search_papers(
                "momentum factor",
                year="2020-2026",
                sources="openalex",
                max_results=250,
                sorted_by="date",
                author="Clifford Asness",
            )

        search.assert_awaited_once_with(
            server.SEARCHERS["openalex"],
            "momentum factor",
            250,
            sorted_by="date",
            year="2020-2026",
            author="Clifford Asness",
        )
        self.assertEqual(result["sources_used"], ["openalex"])

    async def test_cli_search_forwards_common_parameters(self):
        openalex_searcher = object()
        args = build_parser().parse_args(
            [
                "search", "momentum factor", "-s", "openalex", "-y", "2020-2026",
                "-n", "250", "-sort", "recency", "-au", "Clifford Asness",
            ]
        )
        with patch.dict(cli.SEARCHERS, {"openalex": openalex_searcher}, clear=True), patch.object(
            cli, "_async_search", new=AsyncMock(return_value=[])
        ) as search, patch("builtins.print"):
            exit_code = await cli.cmd_search(args)

        self.assertEqual(exit_code, 0)
        search.assert_awaited_once_with(
            openalex_searcher,
            "momentum factor",
            250,
            sorted_by="recency",
            year="2020-2026",
            author="Clifford Asness",
        )


class TestCrossrefPublicDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_unified_search_forwards_common_parameters(self):
        with patch.object(server, "async_search", new=AsyncMock(return_value=[])) as search:
            result = await server.search_papers(
                "momentum factor",
                year="2020-2026",
                sources="crossref",
                max_results=2500,
                sorted_by="date",
                author="Clifford Asness",
            )

        search.assert_awaited_once_with(
            server.SEARCHERS["crossref"],
            "momentum factor",
            2500,
            sorted_by="date",
            year="2020-2026",
            author="Clifford Asness",
        )
        self.assertEqual(result["sources_used"], ["crossref"])

    async def test_cli_search_forwards_common_parameters(self):
        crossref_searcher = object()
        args = build_parser().parse_args(
            [
                "search", "momentum factor", "-s", "crossref", "-y", "2020-2026",
                "-n", "2500", "-sort", "recency", "-au", "Clifford Asness",
            ]
        )
        with patch.dict(cli.SEARCHERS, {"crossref": crossref_searcher}, clear=True), patch.object(
            cli, "_async_search", new=AsyncMock(return_value=[])
        ) as search, patch("builtins.print"):
            exit_code = await cli.cmd_search(args)

        self.assertEqual(exit_code, 0)
        search.assert_awaited_once_with(
            crossref_searcher,
            "momentum factor",
            2500,
            sorted_by="recency",
            year="2020-2026",
            author="Clifford Asness",
        )


if __name__ == "__main__":
    unittest.main()
