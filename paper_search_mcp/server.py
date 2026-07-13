"""MCP tools for the supported academic paper databases."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.core import CORESearcher
from .academic_platforms.crossref import CrossRefSearcher
from .academic_platforms.doaj import DOAJSearcher
from .academic_platforms.openalex import OpenAlexSearcher
from .academic_platforms.semantic import SemanticSearcher

mcp = FastMCP("paper_search_server")

SEARCHERS: Dict[str, Any] = {
    "arxiv": ArxivSearcher(),
    "core": CORESearcher(),
    "doaj": DOAJSearcher(),
    "semantic": SemanticSearcher(),
    "openalex": OpenAlexSearcher(),
    "crossref": CrossRefSearcher(),
}
ALL_SOURCES = list(SEARCHERS)


def _parse_sources(sources: str) -> List[str]:
    if not sources or sources.strip().lower() == "all":
        return ALL_SOURCES.copy()
    requested = [item.strip().lower() for item in sources.split(",") if item.strip()]
    return [source for source in requested if source in SEARCHERS]


def _unique_key(paper: Dict[str, Any]) -> str:
    doi = str(paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    title = str(paper.get("title") or "").strip().lower()
    authors = str(paper.get("authors") or "").strip().lower()
    return f"title:{title}|authors:{authors}" if title else f"id:{paper.get('paper_id', '')}"


async def async_search(searcher: Any, query: str, max_results: int, **kwargs: Any) -> List[Dict]:
    papers = await asyncio.to_thread(searcher.search, query, max_results=max_results, **kwargs)
    return [paper.to_dict() for paper in papers]


@mcp.tool()
async def search_papers(
    query: str,
    year: Optional[str] = None,
    sources: str = "all",
    max_results: int = 5,
    sorted_by: str = "relevance",
    author: Optional[str] = None,
) -> Dict[str, Any]:
    """Search the selected paper sources with the common public parameters.

    ``sources`` is used only for dispatch and is never sent to a provider API.
    Each provider adapter translates these common parameters into its own
    native API syntax.
    """
    selected = _parse_sources(sources)
    if not selected:
        return {"query": query, "sources_used": [], "errors": {"sources": "No valid sources selected."}, "papers": [], "total": 0}

    tasks = {}
    for source in selected:
        kwargs: Dict[str, Any] = {}
        if source == "arxiv":
            kwargs = {"sorted_by": sorted_by, "year": year, "author": author}
        elif source == "core":
            kwargs = {"sorted_by": sorted_by, "year": year, "author": author}
        elif source == "doaj":
            kwargs = {"sorted_by": sorted_by, "year": year, "author": author}
        elif source == "semantic":
            kwargs = {"sorted_by": sorted_by, "year": year, "author": author}
        elif source == "openalex":
            kwargs = {"sorted_by": sorted_by, "year": year, "author": author}
        elif source == "crossref":
            kwargs = {"sorted_by": sorted_by, "year": year, "author": author}
        tasks[source] = async_search(SEARCHERS[source], query, max_results, **kwargs)
    outputs = await asyncio.gather(*tasks.values(), return_exceptions=True)
    errors: Dict[str, str] = {}
    counts: Dict[str, int] = {}
    merged: List[Dict[str, Any]] = []
    for source, output in zip(tasks, outputs):
        if isinstance(output, Exception):
            errors[source] = str(output)
            counts[source] = 0
        else:
            counts[source] = len(output)
            merged.extend(output)

    seen: set[str] = set()
    papers = []
    for paper in merged:
        key = _unique_key(paper)
        if key not in seen:
            seen.add(key)
            papers.append(paper)
    return {"query": query, "sources_used": selected, "source_results": counts, "errors": errors, "papers": papers, "total": len(papers), "raw_total": len(merged)}


@mcp.tool()
async def search_arxiv(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    sorted_by: str = "relevance",
    author: Optional[str] = None,
) -> List[Dict]:
    """Search quantitative-finance papers on arXiv."""
    return await async_search(
        SEARCHERS["arxiv"],
        query,
        max_results,
        year=year,
        sorted_by=sorted_by,
        author=author,
    )


@mcp.tool()
async def search_core(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    sorted_by: str = "relevance",
    author: Optional[str] = None,
) -> List[Dict]:
    """Search finance-related works in CORE."""
    return await async_search(
        SEARCHERS["core"],
        query,
        max_results,
        year=year,
        sorted_by=sorted_by,
        author=author,
    )


@mcp.tool()
async def search_doaj(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    sorted_by: str = "relevance",
    author: Optional[str] = None,
) -> List[Dict]:
    """Search finance-related articles in DOAJ."""
    return await async_search(
        SEARCHERS["doaj"],
        query,
        max_results,
        year=year,
        sorted_by=sorted_by,
        author=author,
    )


@mcp.tool()
async def search_semantic(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    sorted_by: str = "relevance",
    author: Optional[str] = None,
) -> List[Dict]:
    """Search finance-related papers in Semantic Scholar."""
    return await async_search(
        SEARCHERS["semantic"],
        query,
        max_results,
        year=year,
        sorted_by=sorted_by,
        author=author,
    )


@mcp.tool()
async def search_openalex(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    sorted_by: str = "relevance",
    author: Optional[str] = None,
) -> List[Dict]:
    """Search finance-related articles and reviews in OpenAlex."""
    return await async_search(
        SEARCHERS["openalex"],
        query,
        max_results,
        year=year,
        sorted_by=sorted_by,
        author=author,
    )


@mcp.tool()
async def search_crossref(
    query: str,
    year: Optional[str] = None,
    max_results: int = 10,
    sorted_by: str = "relevance",
    author: Optional[str] = None,
) -> List[Dict]:
    """Search finance-related metadata in Crossref."""
    return await async_search(
        SEARCHERS["crossref"],
        query,
        max_results,
        year=year,
        sorted_by=sorted_by,
        author=author,
    )


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


for _source in ALL_SOURCES:
    _register_content_tools(_source)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
