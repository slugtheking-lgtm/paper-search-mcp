"""arXiv search implementation with finance-only query constraints."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import time
from typing import Any, List, Optional

import feedparser
from pypdf import PdfReader
import requests

from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource


class ArxivSearcher(PaperSource):
    """Search arXiv's quantitative-finance categories."""

    BASE_URL = "http://export.arxiv.org/api/query"
    MAX_RESULTS = 30_000
    PAGE_SIZE = 2_000
    EARLIEST_DATE = "199101010000"
    SORT_BY = {
        "relevance": "relevance",
        "date": "submittedDate",
        "updated": "lastUpdatedDate",
    }
    FINANCE_SUBJECTS = (
        "(cat:q-fin.CP OR cat:q-fin.EC OR cat:q-fin.GN OR cat:q-fin.MF "
        "OR cat:q-fin.PM OR cat:q-fin.PR OR cat:q-fin.RM OR cat:q-fin.ST "
        "OR cat:q-fin.TR)"
    )
    YEAR_ERROR = (
        "year must use one of: YYYY, YYYY-YYYY, YYYY-, or -YYYY "
        '(for example: "2024", "2020-2024", "2020-", "-2020")'
    )

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "paper-search-mcp/1.0 (mailto:openags@example.com)",
                "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
            }
        )

    @staticmethod
    def _normalize_phrase(value: str, field_name: str) -> str:
        """Normalize user text and quote it as a literal arXiv phrase."""
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")

        phrase = " ".join(value.strip().split())
        if len(phrase) >= 2 and phrase.startswith('"') and phrase.endswith('"'):
            phrase = " ".join(phrase[1:-1].strip().split())

        if not phrase:
            raise ValueError(f"{field_name} must not be empty")
        if '"' in phrase:
            raise ValueError(
                f'{field_name} must be plain text and must not contain embedded double quotes'
            )
        return f'"{phrase}"'

    @classmethod
    def _build_year_filter(
        cls, year: str, now_utc: Optional[datetime] = None
    ) -> str:
        """Convert a supported year expression to an arXiv submittedDate filter."""
        if not isinstance(year, str):
            raise ValueError(cls.YEAR_ERROR)

        value = year.strip()
        single = re.fullmatch(r"(\d{4})", value)
        closed = re.fullmatch(r"(\d{4})-(\d{4})", value)
        since = re.fullmatch(r"(\d{4})-", value)
        until = re.fullmatch(r"-(\d{4})", value)

        if single:
            start_year = end_year = int(single.group(1))
            start = f"{start_year:04d}01010000"
            end = f"{end_year:04d}12312359"
        elif closed:
            start_year = int(closed.group(1))
            end_year = int(closed.group(2))
            if start_year > end_year:
                raise ValueError("year start must not be greater than year end")
            start = f"{start_year:04d}01010000"
            end = f"{end_year:04d}12312359"
        elif since:
            start_year = int(since.group(1))
            current = now_utc or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            else:
                current = current.astimezone(timezone.utc)
            start = f"{start_year:04d}01010000"
            end = current.strftime("%Y%m%d%H%M")
        elif until:
            end_year = int(until.group(1))
            start = cls.EARLIEST_DATE
            end = f"{end_year:04d}12312359"
        else:
            raise ValueError(cls.YEAR_ERROR)

        return f"submittedDate:[{start} TO {end}]"

    @classmethod
    def _build_search_query(
        cls,
        query: str,
        year: Optional[str] = None,
        author: Optional[str] = None,
    ) -> str:
        """Build query, author, finance subjects, and year in a fixed order."""
        parts = [f"all:{cls._normalize_phrase(query, 'query')}"]
        if author is not None:
            parts.append(f"au:{cls._normalize_phrase(author, 'author')}")
        parts.append(cls.FINANCE_SUBJECTS)
        if year is not None:
            parts.append(cls._build_year_filter(year))
        return " AND ".join(parts)

    @classmethod
    def _validate_max_results(cls, max_results: int) -> None:
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            raise ValueError("max_results must be an integer from 1 to 30000")
        if not 1 <= max_results <= cls.MAX_RESULTS:
            raise ValueError("max_results must be between 1 and 30000")

    @classmethod
    def _map_sort(cls, sorted_by: str) -> str:
        try:
            return cls.SORT_BY[sorted_by]
        except (KeyError, TypeError):
            raise ValueError("sorted_by must be one of: relevance, date, updated") from None

    def _request_page(self, params: dict[str, Any]):
        response = None
        for attempt in range(3):
            try:
                response = self.session.get(self.BASE_URL, params=params, timeout=30)
            except requests.RequestException:
                time.sleep((attempt + 1) * 1.5)
                continue
            if response.status_code == 200:
                return response
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep((attempt + 1) * 1.5)
                continue
            break
        return None

    @staticmethod
    def _parse_entry(entry: Any) -> Paper:
        authors = [author.name for author in entry.authors]
        published = datetime.strptime(entry.published, "%Y-%m-%dT%H:%M:%SZ")
        updated = datetime.strptime(entry.updated, "%Y-%m-%dT%H:%M:%SZ")
        pdf_url = next(
            (link.href for link in entry.links if link.type == "application/pdf"), ""
        )
        doi = entry.get("doi", "") or extract_doi(entry.summary) or extract_doi(entry.id)
        for link in entry.links:
            if link.get("title") == "doi":
                doi = doi or extract_doi(link.href)

        return Paper(
            paper_id=entry.id.split("/")[-1],
            title=entry.title,
            authors=authors,
            abstract=entry.summary,
            url=entry.id,
            pdf_url=pdf_url,
            published_date=published,
            updated_date=updated,
            source="arxiv",
            categories=[tag.term for tag in entry.tags],
            keywords=[],
            doi=doi,
        )

    def search(
        self,
        query: str,
        max_results: int = 10,
        sorted_by: str = "relevance",
        year: Optional[str] = None,
        author: Optional[str] = None,
    ) -> List[Paper]:
        """Search arXiv and return at most ``max_results`` papers."""
        self._validate_max_results(max_results)
        search_query = self._build_search_query(query, year=year, author=author)
        sort_by = self._map_sort(sorted_by)

        papers: List[Paper] = []
        start = 0
        while start < max_results:
            page_size = min(self.PAGE_SIZE, max_results - start)
            params = {
                "search_query": search_query,
                "start": start,
                "max_results": page_size,
                "sortBy": sort_by,
                "sortOrder": "descending",
            }
            response = self._request_page(params)
            if response is None:
                break

            entries = feedparser.parse(response.content).entries
            if not entries:
                break
            for entry in entries:
                try:
                    papers.append(self._parse_entry(entry))
                except Exception as exc:
                    print(f"Error parsing arXiv entry: {exc}")

            if len(entries) < page_size:
                break
            start += page_size
            if start < max_results:
                time.sleep(3)

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        pdf_url = f"https://arxiv.org/pdf/{paper_id}.pdf"
        response = requests.get(pdf_url)
        os.makedirs(save_path, exist_ok=True)
        output_file = f"{save_path}/{paper_id}.pdf"
        with open(output_file, "wb") as file_obj:
            file_obj.write(response.content)
        return output_file

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        pdf_path = f"{save_path}/{paper_id}.pdf"
        if not os.path.exists(pdf_path):
            pdf_path = self.download_pdf(paper_id, save_path)
        try:
            reader = PdfReader(pdf_path)
            return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception as exc:
            print(f"Error reading PDF for paper {paper_id}: {exc}")
            return ""
