#!/usr/bin/env python3
"""CLI interface for paper-search — search, download, and read academic papers."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List

from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.semantic import SemanticSearcher
from .academic_platforms.crossref import CrossRefSearcher
from .academic_platforms.datacite import DataCiteSearcher
from .academic_platforms.openalex import OpenAlexSearcher
from .academic_platforms.core import CORESearcher
from .academic_platforms.doaj import DOAJSearcher
from .dedup import dedupe_paper_dicts, mapping_identity_keys, sort_papers_by_date_desc

# ---------------------------------------------------------------------------
# Searcher registry
# ---------------------------------------------------------------------------

SEARCHERS: Dict[str, Any] = {}


def _init_searchers() -> None:
    """Lazily initialize searcher instances."""
    if SEARCHERS:
        return

    SEARCHERS["arxiv"] = ArxivSearcher()
    SEARCHERS["semantic"] = SemanticSearcher()
    SEARCHERS["crossref"] = CrossRefSearcher()
    SEARCHERS["datacite"] = DataCiteSearcher()
    SEARCHERS["openalex"] = OpenAlexSearcher()
    SEARCHERS["core"] = CORESearcher()
    SEARCHERS["doaj"] = DOAJSearcher()


ALL_SOURCES = [
    "arxiv", "core", "doaj", "semantic", "openalex", "crossref", "datacite",
]


def _parse_sources(sources: str) -> List[str]:
    if not sources or sources.strip().lower() == "all":
        return [s for s in ALL_SOURCES if s in SEARCHERS]
    normalized = [p.strip().lower() for p in sources.split(",") if p.strip()]
    return [s for s in normalized if s in SEARCHERS]


def _paper_unique_key(paper: Dict[str, Any]) -> str:
    doi_key, bibliographic_key = mapping_identity_keys(paper)
    if doi_key:
        return doi_key
    if bibliographic_key:
        return bibliographic_key
    return f"id:{(paper.get('paper_id') or '').strip().lower()}"


def _dedupe(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return dedupe_paper_dicts(papers)


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------

async def _async_search(searcher: Any, query: str, max_results: int, **kwargs) -> List[Dict]:
    try:
        if kwargs:
            papers = await asyncio.to_thread(searcher.search, query, max_results=max_results, **kwargs)
        else:
            papers = await asyncio.to_thread(searcher.search, query, max_results=max_results)
        return [p.to_dict() for p in papers]
    except Exception:
        # A failed provider is treated exactly like an empty provider result so
        # one outage or rate limit cannot fail a multi-source search.
        return []


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_search(args: argparse.Namespace) -> int:
    _init_searchers()
    selected = _parse_sources(args.sources)
    if not selected:
        print(json.dumps({"papers": []}, ensure_ascii=False))
        return 1

    tasks = {}
    for src in selected:
        searcher = SEARCHERS[src]
        extra = {"year": args.year, "author": args.author}
        tasks[src] = _async_search(searcher, args.query, args.max_results, **extra)

    names = list(tasks.keys())
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    merged: List[Dict[str, Any]] = []
    for name, result in zip(names, results):
        if not isinstance(result, Exception):
            for p in result:
                if not p.get("sources"):
                    p["sources"] = [name]
                merged.append(p)

    papers = sort_papers_by_date_desc(_dedupe(merged))
    print(json.dumps({"papers": papers}, indent=2, default=str, ensure_ascii=False))
    return 0


async def cmd_download(args: argparse.Namespace) -> int:
    _init_searchers()
    source = args.source.strip().lower()

    if source not in SEARCHERS:
        print(json.dumps({"error": f"Unknown source: {source}", "available": sorted(SEARCHERS.keys())}))
        return 1

    searcher = SEARCHERS[source]
    try:
        result = await asyncio.to_thread(searcher.download_pdf, args.paper_id, args.save_path)
        print(json.dumps({"status": "ok", "path": result}))
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 1


async def cmd_read(args: argparse.Namespace) -> int:
    _init_searchers()
    source = args.source.strip().lower()

    if source not in SEARCHERS:
        print(json.dumps({"error": f"Unknown source: {source}", "available": sorted(SEARCHERS.keys())}))
        return 1

    searcher = SEARCHERS[source]
    try:
        text = await asyncio.to_thread(searcher.read_paper, args.paper_id, args.save_path)
        print(text)
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 1


async def cmd_sources(args: argparse.Namespace) -> int:
    _init_searchers()
    print(json.dumps({"sources": sorted(SEARCHERS.keys())}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-search",
        description="Search, download, and read papers from seven academic sources.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = sub.add_parser("search", help="Search for papers across academic platforms")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("-y", "--year", default=None,
                          help="Year filter: YYYY, YYYY-YYYY, YYYY-, or -YYYY")
    p_search.add_argument("-s", "--sources", default="all",
                          help="Comma-separated sources or 'all' (default: all)")
    p_search.add_argument("-n", "--max-results", type=int, default=5,
                          help="Final result limit per source (default: 5)")
    p_search.add_argument("-au", "--author", default=None,
                          help="Author name as a complete phrase")

    # download
    p_dl = sub.add_parser("download", help="Download a paper PDF")
    p_dl.add_argument("source", help="Source platform (e.g. arxiv, semantic)")
    p_dl.add_argument("paper_id", help="Paper identifier")
    p_dl.add_argument("-o", "--save-path", default="./downloads", help="Save directory (default: ./downloads)")

    # read
    p_read = sub.add_parser("read", help="Download and extract text from a paper")
    p_read.add_argument("source", help="Source platform (e.g. arxiv, semantic)")
    p_read.add_argument("paper_id", help="Paper identifier")
    p_read.add_argument("-o", "--save-path", default="./downloads", help="Save directory (default: ./downloads)")

    # sources
    sub.add_parser("sources", help="List available sources")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "search": cmd_search,
        "download": cmd_download,
        "read": cmd_read,
        "sources": cmd_sources,
    }

    exit_code = asyncio.run(dispatch[args.command](args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
