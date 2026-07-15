"""HTTP API for remote Agent access to paper-search-mcp."""

from __future__ import annotations

import os
import re
from typing import Optional

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .server import search_papers


class SearchRequest(BaseModel):
    """Public search parameters accepted by ``POST /search``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, description="Plain-text paper search phrase")
    year: Optional[str] = Field(
        default=None,
        description="YYYY, YYYY-YYYY, YYYY-, or -YYYY",
    )
    sources: str = Field(
        default="all",
        description="all or a comma-separated list of source names",
    )
    max_results: int = Field(default=10, ge=1, le=30_000)
    author: Optional[str] = None

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized

    @field_validator("year")
    @classmethod
    def validate_year(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        match = re.fullmatch(
            r"(?:(\d{4})|(\d{4})-(\d{4})|(\d{4})-|-(\d{4}))",
            normalized,
        )
        if not match:
            raise ValueError("year must use YYYY, YYYY-YYYY, YYYY-, or -YYYY")
        if match.group(2) and int(match.group(2)) > int(match.group(3)):
            raise ValueError("year start must not be greater than year end")
        return normalized

    @field_validator("sources")
    @classmethod
    def normalize_sources(cls, value: str) -> str:
        normalized = ",".join(
            source.strip().lower() for source in value.split(",") if source.strip()
        )
        return normalized or "all"

    @field_validator("author")
    @classmethod
    def normalize_author(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None


class PaperResponse(BaseModel):
    """Compact paper representation returned to the Agent."""

    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str
    authors: list[str]
    abstract: str
    doi: Optional[str]
    published_date: Optional[str]
    pdf_url: Optional[str]
    url: Optional[str]
    sources: list[str]
    topics: list[str]
    citations: Optional[int]


class SearchResponse(BaseModel):
    papers: list[PaperResponse]


app = FastAPI(
    title="Paper Search API",
    description="Finance-focused academic paper search for Agent tools.",
    version="1.0.0",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return service availability without calling any provider API."""
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse, tags=["papers"])
async def search(request: SearchRequest) -> dict:
    """Search one or more academic sources and return only papers."""
    return await search_papers(
        query=request.query,
        year=request.year,
        sources=request.sources,
        max_results=request.max_results,
        author=request.author,
    )


def main() -> None:
    """Run the HTTP service with one worker to preserve provider rate limits."""
    host = os.getenv("PAPER_SEARCH_API_HOST", "0.0.0.0")
    port = int(os.getenv("PAPER_SEARCH_API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, workers=1)


if __name__ == "__main__":
    main()
