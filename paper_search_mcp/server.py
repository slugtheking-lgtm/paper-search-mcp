"""MCP tools for the supported academic paper databases."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.core import CORESearcher
from .academic_platforms.crossref import CrossRefSearcher
from .academic_platforms.datacite import DataCiteSearcher
from .academic_platforms.doaj import DOAJSearcher
from .academic_platforms.openalex import OpenAlexSearcher
from .academic_platforms.semantic import SemanticSearcher
from .dedup import dedupe_paper_dicts, mapping_identity_keys, sort_papers_by_date_desc

mcp = FastMCP("paper_search_server")

SEARCHERS: Dict[str, Any] = {
    "arxiv": ArxivSearcher(),
    "core": CORESearcher(),
    "doaj": DOAJSearcher(),
    "semantic": SemanticSearcher(),
    "openalex": OpenAlexSearcher(),
    "crossref": CrossRefSearcher(),
    "datacite": DataCiteSearcher(),
}
ALL_SOURCES = [
    "arxiv", "core", "doaj", "semantic", "openalex", "crossref", "datacite",
]


def _parse_sources(sources: str) -> List[str]:
    if not sources or sources.strip().lower() == "all":
        return ALL_SOURCES.copy()
    requested = [item.strip().lower() for item in sources.split(",") if item.strip()]
    return [source for source in requested if source in SEARCHERS]


def _unique_key(paper: Dict[str, Any]) -> str:
    doi_key, bibliographic_key = mapping_identity_keys(paper)
    if doi_key:
        return doi_key
    if bibliographic_key:
        return bibliographic_key
    return f"id:{paper.get('paper_id', '')}"


async def async_search(searcher: Any, query: str, max_results: int, **kwargs: Any) -> List[Dict]:
    try:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results, **kwargs)
        return [paper.to_dict() for paper in papers]
    except Exception:
        # Provider failures and rate limits are empty results at the MCP boundary.
        return []


@mcp.tool()
async def search_papers(
    query: str,
    year: Optional[str] = None,
    sources: str = "all",
    max_results: int = 5,
    author: Optional[str] = None,
) -> Dict[str, Any]:
    """Search the selected paper sources with the common public parameters.

    ``sources`` is used only for dispatch and is never sent to a provider API.
    Each provider adapter translates these common parameters into its own
    native API syntax.
    """
    selected = _parse_sources(sources)
    if not selected:
        return {"papers": []}

    tasks = {}
    for source in selected:
        kwargs: Dict[str, Any] = {"year": year, "author": author}
        tasks[source] = async_search(SEARCHERS[source], query, max_results, **kwargs)
    outputs = await asyncio.gather(*tasks.values(), return_exceptions=True)
    merged: List[Dict[str, Any]] = []
    for source, output in zip(tasks, outputs):
        if not isinstance(output, Exception):
            merged.extend(output)

    papers = sort_papers_by_date_desc(dedupe_paper_dicts(merged))
    return {"papers": papers}


@mcp.tool()
async def search_arxiv(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    author: Optional[str] = None,
) -> Dict[str, List[Dict]]:
    """Search quantitative-finance papers on arXiv."""
    return {"papers": await async_search(
        SEARCHERS["arxiv"],
        query,
        max_results,
        year=year,
        author=author,
    )}


@mcp.tool()
async def search_core(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    author: Optional[str] = None,
) -> Dict[str, List[Dict]]:
    """Search finance-related works in CORE."""
    return {"papers": await async_search(
        SEARCHERS["core"],
        query,
        max_results,
        year=year,
        author=author,
    )}


@mcp.tool()
async def search_doaj(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    author: Optional[str] = None,
) -> Dict[str, List[Dict]]:
    """Search finance-related articles in DOAJ."""
    return {"papers": await async_search(
        SEARCHERS["doaj"],
        query,
        max_results,
        year=year,
        author=author,
    )}


@mcp.tool()
async def search_semantic(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    author: Optional[str] = None,
) -> Dict[str, List[Dict]]:
    """Search finance-related papers in Semantic Scholar."""
    return {"papers": await async_search(
        SEARCHERS["semantic"],
        query,
        max_results,
        year=year,
        author=author,
    )}


@mcp.tool()
async def search_openalex(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    author: Optional[str] = None,
) -> Dict[str, List[Dict]]:
    """Search finance-related articles and reviews in OpenAlex."""
    return {"papers": await async_search(
        SEARCHERS["openalex"],
        query,
        max_results,
        year=year,
        author=author,
    )}


@mcp.tool()
async def search_crossref(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    author: Optional[str] = None,
) -> Dict[str, List[Dict]]:
    """Search finance-related metadata in Crossref."""
    return {"papers": await async_search(
        SEARCHERS["crossref"],
        query,
        max_results,
        year=year,
        author=author,
    )}


@mcp.tool()
async def search_datacite(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    author: Optional[str] = None,
) -> Dict[str, List[Dict]]:
    """Search finance-related DataCite literature in relevance order."""
    return {"papers": await async_search(
        SEARCHERS["datacite"],
        query,
        max_results,
        year=year,
        author=author,
    )}


def _register_content_tools(source: str) -> None:
    searcher = SEARCHERS[source]

    async def download(paper_id: str, save_path: str = "./downloads") -> str:
        return await asyncio.to_thread(searcher.download_pdf, paper_id, save_path)

    async def read(paper_id: str, save_path: str = "./downloads") -> str:
        return await asyncio.to_thread(searcher.read_paper, paper_id, save_path)

    download.__name__ = f"download_{source}"
    download.__doc__ = f"Download a paper through {source}."
    read.__name__ = f"read_{source}_paper"
    read.__doc__ = f"Read a paper through {source}."
    mcp.tool()(download)
    mcp.tool()(read)
    globals()[download.__name__] = download
    globals()[read.__name__] = read


for _source in SEARCHERS:
    _register_content_tools(_source)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
