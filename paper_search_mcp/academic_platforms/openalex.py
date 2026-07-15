"""OpenAlex connector with finance filters and cursor pagination."""

from __future__ import annotations

from datetime import datetime
import logging
import re
from typing import Any, Dict, List, Optional

import requests

from ..config import get_env
from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource

logger = logging.getLogger(__name__)

OPENALEX_FINANCE_FILTER = "topics.field.id:20"
OPENALEX_WORK_TYPE_FILTER = "type:article|review"


class OpenAlexSearcher(PaperSource):
    """Search finance-related article and review works in OpenAlex."""

    WORKS_URL = "https://api.openalex.org/works"
    AUTHORS_URL = "https://api.openalex.org/authors"
    PAGE_SIZE = 100
    YEAR_ERROR = (
        "year must use one of: YYYY, YYYY-YYYY, YYYY-, or -YYYY "
        '(for example: "2024", "2020-2024", "2020-", "-2020")'
    )

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or get_env("OPENALEX_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "paper-search-mcp/1.0 (mailto:openags@example.com)"}
        )

    @staticmethod
    def _normalize_phrase(value: str, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")
        phrase = " ".join(value.strip().split())
        if len(phrase) >= 2 and phrase.startswith('"') and phrase.endswith('"'):
            phrase = " ".join(phrase[1:-1].strip().split())
        if not phrase:
            raise ValueError(f"{field_name} must not be empty")
        if '"' in phrase:
            raise ValueError(
                f"{field_name} must be plain text and must not contain embedded double quotes"
            )
        return f'"{phrase}"'

    @classmethod
    def _build_year_filter(cls, year: str) -> str:
        if not isinstance(year, str):
            raise ValueError(cls.YEAR_ERROR)
        value = year.strip()
        single = re.fullmatch(r"(\d{4})", value)
        closed = re.fullmatch(r"(\d{4})-(\d{4})", value)
        since = re.fullmatch(r"(\d{4})-", value)
        until = re.fullmatch(r"-(\d{4})", value)

        if single:
            return f"publication_year:{single.group(1)}"
        if closed:
            start_year, end_year = int(closed.group(1)), int(closed.group(2))
            if start_year > end_year:
                raise ValueError("year start must not be greater than year end")
            return f"publication_year:{start_year:04d}-{end_year:04d}"
        if since:
            return f"from_publication_date:{since.group(1)}-01-01"
        if until:
            return f"to_publication_date:{until.group(1)}-12-31"
        raise ValueError(cls.YEAR_ERROR)

    @staticmethod
    def _validate_max_results(max_results: int) -> None:
        if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results < 1:
            raise ValueError("max_results must be a positive integer")

    def _with_api_key(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _request_json(self, url: str, params: Dict[str, Any]) -> dict:
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            message = ""
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    payload = response.json()
                    message = payload.get("message") or payload.get("error") or ""
                except ValueError:
                    message = response.text[:300]
            detail = f": {message}" if message else ""
            raise RuntimeError(
                f"OpenAlex API request failed (status={status}){detail}"
            ) from exc
        except ValueError as exc:
            raise RuntimeError("OpenAlex returned an invalid JSON response") from exc

    def _resolve_author_id(self, author: str) -> Optional[str]:
        normalized_author = self._normalize_phrase(author, "author")[1:-1]
        payload = self._request_json(
            self.AUTHORS_URL,
            self._with_api_key({"search": normalized_author, "per_page": 5}),
        )
        candidates = payload.get("results", [])
        if not candidates:
            return None
        exact = [
            candidate
            for candidate in candidates
            if str(candidate.get("display_name") or "").casefold()
            == normalized_author.casefold()
        ]
        selected = (exact or candidates)[0]
        author_id = str(selected.get("id") or "").rstrip("/").split("/")[-1]
        return author_id or None

    @classmethod
    def _build_filter(
        cls, year: Optional[str] = None, author_id: Optional[str] = None
    ) -> str:
        filters = [OPENALEX_FINANCE_FILTER]
        if year is not None:
            filters.append(cls._build_year_filter(year))
        if author_id is not None:
            filters.append(f"authorships.author.id:{author_id}")
        filters.append(OPENALEX_WORK_TYPE_FILTER)
        return ",".join(filters)

    @staticmethod
    def _reconstruct_abstract(inverted_index: dict) -> str:
        if not inverted_index:
            return ""
        try:
            positions = [
                (position, word)
                for word, word_positions in inverted_index.items()
                for position in word_positions
            ]
            positions.sort(key=lambda item: item[0])
            return " ".join(word for _, word in positions)
        except Exception as exc:
            logger.debug("Error reconstructing OpenAlex abstract: %s", exc)
            return ""

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _parse_work(self, item: dict) -> Optional[Paper]:
        try:
            title = item.get("title") or item.get("display_name")
            if not title:
                return None
            paper_id = str(item.get("id") or "").replace("https://openalex.org/", "")
            authors = [
                authorship.get("author", {}).get("display_name", "")
                for authorship in item.get("authorships", [])
                if authorship.get("author", {}).get("display_name")
            ]
            abstract = self._reconstruct_abstract(item.get("abstract_inverted_index"))
            doi = str(item.get("doi") or "").replace("https://doi.org/", "")
            if not doi and abstract:
                doi = extract_doi(abstract)

            primary_location = item.get("primary_location") or {}
            url = primary_location.get("landing_page_url") or item.get("id", "")
            pdf_url = primary_location.get("pdf_url") or ""
            open_access = item.get("open_access") or {}
            if not pdf_url and open_access.get("is_oa"):
                pdf_url = open_access.get("oa_url") or ""

            topics = [
                topic.get("display_name", "")
                for topic in item.get("topics", [])
                if topic.get("display_name")
            ]
            if not topics:
                topics = [
                    concept.get("display_name", "")
                    for concept in item.get("concepts", [])
                    if concept.get("display_name")
                ]

            return Paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                abstract=abstract,
                url=url,
                pdf_url=pdf_url,
                published_date=self._parse_datetime(item.get("publication_date")),
                updated_date=self._parse_datetime(item.get("updated_date")),
                source="openalex",
                categories=topics[:5],
                doi=doi,
                citations=item.get("cited_by_count"),
            )
        except Exception as exc:
            logger.debug("Failed to parse OpenAlex work: %s", exc)
            return None

    def search(
        self,
        query: str,
        max_results: int = 10,
        year: Optional[str] = None,
        author: Optional[str] = None,
    ) -> List[Paper]:
        """Search OpenAlex using phrase search, filters, sort, and cursor paging."""
        self._validate_max_results(max_results)
        search_phrase = self._normalize_phrase(query, "query")
        author_id = self._resolve_author_id(author) if author is not None else None
        if author is not None and author_id is None:
            return []
        work_filter = self._build_filter(year=year, author_id=author_id)

        papers: List[Paper] = []
        use_cursor = max_results > self.PAGE_SIZE
        cursor: Optional[str] = "*" if use_cursor else None

        while len(papers) < max_results:
            per_page = min(self.PAGE_SIZE, max_results - len(papers))
            params: Dict[str, Any] = {
                "search": search_phrase,
                "filter": work_filter,
                "sort": "relevance_score:desc",
                "per_page": per_page,
            }
            if cursor is not None:
                params["cursor"] = cursor
            payload = self._request_json(
                self.WORKS_URL, self._with_api_key(params)
            )
            results = payload.get("results", [])
            if not results:
                break
            for item in results:
                if len(papers) >= max_results:
                    break
                paper = self._parse_work(item)
                if paper:
                    papers.append(paper)

            if not use_cursor:
                break
            cursor = (payload.get("meta") or {}).get("next_cursor")
            if not cursor:
                break

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError(
            "OpenAlex does not provide direct PDF downloads natively. "
            "Please use the extracted 'pdf_url' if available, or DOI for fallback."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        return (
            "OpenAlex papers cannot be read directly through this aggregator. "
            "Please use the paper's DOI or pdf_url to access the full text."
        )
