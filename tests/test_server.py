import inspect
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from paper_search_mcp import cli, server
from paper_search_mcp.academic_platforms.arxiv import ArxivSearcher
from paper_search_mcp.academic_platforms.core import CORESearcher
from paper_search_mcp.academic_platforms.crossref import CrossRefSearcher
from paper_search_mcp.academic_platforms.datacite import DataCiteSearcher
from paper_search_mcp.academic_platforms.doaj import DOAJSearcher
from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher
from paper_search_mcp.academic_platforms.semantic import SemanticSearcher
from paper_search_mcp.cli import build_parser


EXPECTED_SOURCES = [
    "arxiv",
    "core",
    "doaj",
    "semantic",
    "openalex",
    "crossref",
    "datacite",
]


class TestPublicParameters(unittest.TestCase):
    def test_datacite_is_in_all_for_cli_and_mcp(self):
        self.assertEqual(server.ALL_SOURCES, EXPECTED_SOURCES)
        self.assertEqual(cli.ALL_SOURCES, EXPECTED_SOURCES)
        self.assertEqual(server._parse_sources("all"), EXPECTED_SOURCES)

        with patch.dict(
            cli.SEARCHERS,
            {source: object() for source in EXPECTED_SOURCES},
            clear=True,
        ):
            self.assertEqual(cli._parse_sources("all"), EXPECTED_SOURCES)

    def test_source_parser_rejects_unknown_sources(self):
        self.assertEqual(
            server._parse_sources("arxiv,removed_source,datacite"),
            ["arxiv", "datacite"],
        )

    def test_cli_exposes_only_current_common_search_parameters(self):
        args = build_parser().parse_args(
            [
                "search",
                "momentum factor",
                "-y",
                "2020-2024",
                "-s",
                "arxiv",
                "-n",
                "25",
                "-au",
                "Clifford Asness",
            ]
        )
        self.assertEqual(args.query, "momentum factor")
        self.assertEqual(args.year, "2020-2024")
        self.assertEqual(args.sources, "arxiv")
        self.assertEqual(args.max_results, 25)
        self.assertEqual(args.author, "Clifford Asness")
        self.assertNotIn("sorted" + "_by", vars(args))

    def test_removed_cli_sort_flags_are_rejected(self):
        for flag in ("-sort", "--sorted-by"):
            with self.subTest(flag=flag), self.assertRaises(SystemExit):
                build_parser().parse_args(
                    ["search", "momentum", "-s", "arxiv", flag, "date"]
                )

    def test_mcp_and_connector_signatures_do_not_accept_removed_parameter(self):
        obsolete_name = "sorted" + "_by"
        public_functions = [
            server.search_papers,
            server.search_arxiv,
            server.search_core,
            server.search_doaj,
            server.search_semantic,
            server.search_openalex,
            server.search_crossref,
            server.search_datacite,
        ]
        connector_methods = [
            ArxivSearcher.search,
            CORESearcher.search,
            DOAJSearcher.search,
            SemanticSearcher.search,
            OpenAlexSearcher.search,
            CrossRefSearcher.search,
            DataCiteSearcher.search,
        ]
        for function in public_functions + connector_methods:
            with self.subTest(function=function.__qualname__):
                self.assertNotIn(obsolete_name, inspect.signature(function).parameters)


class TestPublicDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_provider_errors_are_silently_treated_as_empty_results(self):
        searcher = SimpleNamespace(search=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("429")))

        self.assertEqual(
            await server.async_search(searcher, "finance", 10),
            [],
        )
        self.assertEqual(
            await cli._async_search(searcher, "finance", 10),
            [],
        )

    async def test_mcp_unified_search_forwards_year_and_author_only(self):
        with patch.object(
            server, "async_search", new=AsyncMock(return_value=[])
        ) as search:
            result = await server.search_papers(
                "momentum factor",
                year="2020-2026",
                sources="openalex",
                max_results=250,
                author="Clifford Asness",
            )

        search.assert_awaited_once_with(
            server.SEARCHERS["openalex"],
            "momentum factor",
            250,
            year="2020-2026",
            author="Clifford Asness",
        )
        self.assertEqual(result, {"papers": []})

    async def test_cli_search_forwards_year_and_author_only(self):
        searcher = object()
        args = build_parser().parse_args(
            [
                "search",
                "momentum factor",
                "-s",
                "crossref",
                "-y",
                "2020-2026",
                "-n",
                "2500",
                "-au",
                "Clifford Asness",
            ]
        )
        with patch.dict(
            cli.SEARCHERS, {"crossref": searcher}, clear=True
        ), patch.object(
            cli, "_async_search", new=AsyncMock(return_value=[])
        ) as search, patch("builtins.print") as output:
            exit_code = await cli.cmd_search(args)

        self.assertEqual(exit_code, 0)
        search.assert_awaited_once_with(
            searcher,
            "momentum factor",
            2500,
            year="2020-2026",
            author="Clifford Asness",
        )
        self.assertEqual(json.loads(output.call_args.args[0]), {"papers": []})

    async def test_all_dispatches_all_seven_sources(self):
        with patch.object(
            server, "async_search", new=AsyncMock(return_value=[])
        ) as search:
            result = await server.search_papers("asset pricing", sources="all")

        self.assertEqual(result, {"papers": []})
        self.assertEqual(search.await_count, 7)
        called_searchers = [call.args[0] for call in search.await_args_list]
        self.assertEqual(
            called_searchers,
            [server.SEARCHERS[source] for source in EXPECTED_SOURCES],
        )

    async def test_unified_output_merges_sources_and_sorts_newest_first(self):
        shared_arxiv = {
            "paper_id": "arxiv-1",
            "title": "Shared Paper",
            "authors": ["Jane Doe"],
            "abstract": "",
            "doi": "10.1/shared",
            "published_date": "2020-01-01T00:00:00",
            "pdf_url": None,
            "url": "https://arxiv.org/abs/1",
            "sources": ["arxiv"],
            "topics": ["Finance"],
            "citations": None,
        }
        shared_semantic = {
            **shared_arxiv,
            "paper_id": "semantic-1",
            "sources": ["semantic"],
            "citations": 8,
        }
        newest = {
            **shared_arxiv,
            "paper_id": "openalex-1",
            "title": "Newest Paper",
            "doi": "10.1/newest",
            "published_date": "2025-01-01T00:00:00",
            "sources": ["openalex"],
        }

        with patch.object(
            server,
            "async_search",
            new=AsyncMock(side_effect=[[shared_arxiv], [shared_semantic], [newest]]),
        ):
            result = await server.search_papers(
                "asset pricing", sources="arxiv,semantic,openalex"
            )

        self.assertEqual(list(result), ["papers"])
        self.assertEqual(
            [paper["paper_id"] for paper in result["papers"]],
            ["openalex-1", "arxiv-1"],
        )
        self.assertEqual(result["papers"][1]["sources"], ["arxiv", "semantic"])
        self.assertEqual(result["papers"][1]["citations"], 8)

    async def test_datacite_source_tool_forwards_current_parameters(self):
        with patch.object(
            server, "async_search", new=AsyncMock(return_value=[])
        ) as search:
            result = await server.search_datacite(
                "momentum factor",
                year="2020-2026",
                max_results=25,
                author="Clifford Asness",
            )

        self.assertEqual(result, {"papers": []})
        search.assert_awaited_once_with(
            server.SEARCHERS["datacite"],
            "momentum factor",
            25,
            year="2020-2026",
            author="Clifford Asness",
        )


if __name__ == "__main__":
    unittest.main()
