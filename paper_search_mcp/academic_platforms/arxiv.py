"""arXiv search implementation with finance-only query constraints."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import os
import re
import threading
import time
from typing import Any, List, Optional

import feedparser
from pypdf import PdfReader
import requests

from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource


logger = logging.getLogger(__name__)


class ArxivSearcher(PaperSource):
    """Search arXiv's quantitative-finance categories."""

    BASE_URL = "https://export.arxiv.org/api/query"
    MAX_RESULTS = 30_000
    PAGE_SIZE = 2_000
    MIN_REQUEST_INTERVAL = 3.0
    MAX_ATTEMPTS = 4
    EARLIEST_DATE = "199101010000"
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
        self._request_lock = threading.Lock()
        self._last_request_started_at: Optional[float] = None
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
    def _retry_delay(cls, response: Optional[requests.Response], attempt: int) -> float:
        """Return Retry-After or a 3/6/12-second exponential delay."""
        backoff = cls.MIN_REQUEST_INTERVAL * (2**attempt)
        if response is None:
            return backoff

        retry_after = response.headers.get("Retry-After", "").strip()
        if not retry_after:
            return backoff
        try:
            return max(backoff, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return max(backoff, seconds)
            except (TypeError, ValueError, OverflowError):
                return backoff

    def _request_once(self, params: dict[str, Any]) -> requests.Response:
        """Send one request while enforcing one connection and 3-second spacing."""
        with self._request_lock:
            now = time.monotonic()
            if self._last_request_started_at is not None:
                remaining = self.MIN_REQUEST_INTERVAL - (
                    now - self._last_request_started_at
                )
                if remaining > 0:
                    time.sleep(remaining)
            self._last_request_started_at = time.monotonic()
            return self.session.get(self.BASE_URL, params=params, timeout=30)

    def _request_page(self, params: dict[str, Any]) -> requests.Response:
        response = None
        last_error: Optional[requests.RequestException] = None
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                response = self._request_once(params)
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < self.MAX_ATTEMPTS:
                    time.sleep(self._retry_delay(None, attempt))
                continue
            if response.status_code == 200:
                return response
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < self.MAX_ATTEMPTS:
                    time.sleep(self._retry_delay(response, attempt))
                continue
            detail = response.text[:300].strip()
            raise RuntimeError(
                f"arXiv API request failed (status={response.status_code})"
                + (f": {detail}" if detail else "")
            )

        if response is not None:
            detail = response.text[:300].strip()
            raise RuntimeError(
                f"arXiv API request failed after {self.MAX_ATTEMPTS} attempts "
                f"(status={response.status_code})"
                + (f": {detail}" if detail else "")
            )
        raise RuntimeError(
            f"arXiv API connection failed after {self.MAX_ATTEMPTS} attempts: "
            f"{last_error}"
        ) from last_error

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
        year: Optional[str] = None,
        author: Optional[str] = None,
    ) -> List[Paper]:
        """Search arXiv and return at most ``max_results`` papers."""
        self._validate_max_results(max_results)
        search_query = self._build_search_query(query, year=year, author=author)

        papers: List[Paper] = []
        start = 0
        while start < max_results:
            page_size = min(self.PAGE_SIZE, max_results - start)
            params = {
                "search_query": search_query,
                "start": start,
                "max_results": page_size,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
            response = self._request_page(params)

            entries = feedparser.parse(response.content).entries
            if not entries:
                break
            for entry in entries:
                try:
                    papers.append(self._parse_entry(entry))
                except Exception as exc:
                    logger.debug("Error parsing arXiv entry: %s", exc)

            if len(entries) < page_size:
                break
            start += page_size

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
