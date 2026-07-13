from typing import List, Optional
from datetime import datetime
import os
import requests
from bs4 import BeautifulSoup
import time
import random
from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource
import logging
from pypdf import PdfReader
import re
from ..config import get_env

logger = logging.getLogger(__name__)


class SemanticSearcher(PaperSource):
    """Semantic Scholar paper search implementation"""

    SEMANTIC_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    SEMANTIC_BASE_URL = "https://api.semanticscholar.org/graph/v1"
    FINANCE_FIELDS = "Business,Economics"
    RELEVANCE_PAGE_SIZE = 100
    RELEVANCE_MAX_RESULTS = 1_000
    AUTHOR_PAGE_SIZE = 1_000
    PAPER_FIELDS = ",".join(
        [
            "title",
            "abstract",
            "year",
            "citationCount",
            "authors",
            "url",
            "publicationDate",
            "externalIds",
            "fieldsOfStudy",
            "s2FieldsOfStudy",
            "openAccessPdf",
            "publicationTypes",
        ]
    )
    YEAR_ERROR = (
        "year must use one of: YYYY, YYYY-YYYY, YYYY-, or -YYYY "
        '(for example: "2024", "2020-2024", "2020-", "-2020")'
    )
    BROWSERS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    ]

    def __init__(self):
        self._setup_session()

    def _setup_session(self):
        """Initialize session with random user agent"""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": random.choice(self.BROWSERS),
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse date from Semantic Scholar format (e.g., '2025-06-02')"""
        if not date_str:
            return None

        try:
            return datetime.strptime(date_str.strip(), "%Y-%m-%d")
        except ValueError:
            logger.warning(f"Could not parse date: {date_str}")
            return None

    def _extract_url_from_disclaimer(self, disclaimer: str) -> str:
        """Extract URL from disclaimer text"""
        # 匹配常见的 URL 模式
        url_patterns = [
            r"https?://[^\s,)]+",  # 基本的 HTTP/HTTPS URL
            r"https?://arxiv\.org/abs/[^\s,)]+",  # arXiv 链接
            r"https?://[^\s,)]*\.pdf",  # PDF 文件链接
        ]

        all_urls = []
        for pattern in url_patterns:
            matches = re.findall(pattern, disclaimer)
            all_urls.extend(matches)

        if not all_urls:
            return ""

        doi_urls = [url for url in all_urls if "doi.org" in url]
        if doi_urls:
            return doi_urls[0]

        url = all_urls[0]
        if "arxiv.org/abs/" in url:
            return url.replace("/abs/", "/pdf/")
        return url

        return ""

    def _parse_paper(self, item) -> Optional[Paper]:
        """Parse single paper entry from Semantic Scholar HTML and optionally fetch detailed info"""
        try:
            authors = [author["name"] for author in item.get("authors", [])]

            # Parse the publication date
            published_date = self._parse_date(item.get("publicationDate", ""))

            # Safely get PDF URL - 支持从 disclaimer 中提取
            pdf_url = ""
            if item.get("openAccessPdf"):
                open_access_pdf = item["openAccessPdf"]
                # 首先尝试直接获取 URL
                if open_access_pdf.get("url"):
                    pdf_url = open_access_pdf["url"]
                # 如果 URL 为空但有 disclaimer，尝试从 disclaimer 中提取
                elif open_access_pdf.get("disclaimer"):
                    pdf_url = self._extract_url_from_disclaimer(
                        open_access_pdf["disclaimer"]
                    )

            # Safely get DOI
            doi = ""
            if item.get("externalIds") and item["externalIds"].get("DOI"):
                doi = item["externalIds"]["DOI"]

            if not doi and item.get("abstract"):
                doi = extract_doi(item["abstract"])

            # Safely get categories
            categories = item.get("fieldsOfStudy", [])
            if not categories:
                categories = [
                    field.get("category", "")
                    for field in (item.get("s2FieldsOfStudy") or [])
                    if isinstance(field, dict) and field.get("category")
                ]
            elif not isinstance(categories, list):
                categories = [categories] if categories else []

            return Paper(
                paper_id=item["paperId"],
                title=item["title"],
                authors=authors,
                abstract=item.get("abstract", ""),
                url=item.get("url", ""),
                pdf_url=pdf_url,
                published_date=published_date,
                source="semantic",
                categories=categories,
                doi=doi,
                citations=item.get("citationCount", 0),
            )

        except Exception as e:
            logger.warning(f"Failed to parse Semantic paper: {e}")
            return None

    @staticmethod
    def get_api_key() -> Optional[str]:
        """
        Get the Semantic Scholar API key from environment variables.
        Returns None if no API key is set or if it's empty, enabling unauthenticated access.
        """
        api_key = get_env("SEMANTIC_SCHOLAR_API_KEY", "")
        if not api_key or api_key.strip() == "":
            logger.warning(
                "No SEMANTIC_SCHOLAR_API_KEY set or it's empty. Using unauthenticated access with lower rate limits."
            )
            return None
        return api_key.strip()

    def request_api(self, path: str, params: dict) -> dict:
        """
        Make a request to the Semantic Scholar API with optional API key.
        """
        max_retries = 3
        api_key = self.get_api_key()
        retry_delay = 5 if api_key is None else 2
        has_retried_without_key = False

        for attempt in range(max_retries):
            try:
                headers = {"x-api-key": api_key} if api_key else {}
                url = f"{self.SEMANTIC_BASE_URL}/{path}"
                response = self.session.get(
                    url, params=params, headers=headers, timeout=30
                )

                if (
                    response.status_code == 403
                    and api_key
                    and not has_retried_without_key
                ):
                    logger.warning(
                        "Semantic Scholar API key was rejected (403). Retrying without API key."
                    )
                    api_key = None
                    has_retried_without_key = True
                    continue

                # 检查是否是429错误（限流）
                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        retry_after = response.headers.get("Retry-After")
                        wait_time = (
                            int(retry_after)
                            if retry_after and retry_after.isdigit()
                            else retry_delay * (2**attempt)
                        )
                        logger.warning(
                            f"Rate limited (429). Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}"
                        )
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(
                            f"Rate limited (429) after {max_retries} attempts. Please wait before making more requests."
                        )
                        return {
                            "error": "rate_limited",
                            "status_code": 429,
                            "message": "Too many requests. Please wait before retrying.",
                        }

                response.raise_for_status()
                return response

            except requests.exceptions.HTTPError as e:
                if (
                    e.response.status_code == 403
                    and api_key
                    and not has_retried_without_key
                ):
                    logger.warning(
                        "Semantic Scholar API key was rejected (403). Retrying without API key."
                    )
                    api_key = None
                    has_retried_without_key = True
                    continue
                if e.response.status_code == 429:
                    if attempt < max_retries - 1:
                        retry_after = e.response.headers.get("Retry-After")
                        wait_time = (
                            int(retry_after)
                            if retry_after and retry_after.isdigit()
                            else retry_delay * (2**attempt)
                        )
                        logger.warning(
                            f"Rate limited (429). Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}"
                        )
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(
                            f"Rate limited (429) after {max_retries} attempts. Please wait before making more requests."
                        )
                        return {
                            "error": "rate_limited",
                            "status_code": 429,
                            "message": "Too many requests. Please wait before retrying.",
                        }
                else:
                    logger.error(f"HTTP Error requesting API: {e}")
                    return {
                        "error": "http_error",
                        "status_code": e.response.status_code,
                        "message": str(e),
                    }
            except Exception as e:
                logger.error(f"Error requesting API: {e}")
                return {"error": "general_error", "message": str(e)}

        return {
            "error": "max_retries_exceeded",
            "message": "Maximum retry attempts exceeded",
        }

    @staticmethod
    def _normalize_text(value: str, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} must not be empty")
        return normalized

    @classmethod
    def _validate_year(cls, year: Optional[str]) -> Optional[str]:
        if year is None:
            return None
        if not isinstance(year, str):
            raise ValueError(cls.YEAR_ERROR)
        value = year.strip()
        single = re.fullmatch(r"(\d{4})", value)
        closed = re.fullmatch(r"(\d{4})-(\d{4})", value)
        since = re.fullmatch(r"(\d{4})-", value)
        until = re.fullmatch(r"-(\d{4})", value)
        if closed and int(closed.group(1)) > int(closed.group(2)):
            raise ValueError("year start must not be greater than year end")
        if not any((single, closed, since, until)):
            raise ValueError(cls.YEAR_ERROR)
        return value

    @staticmethod
    def _validate_max_results(max_results: int) -> None:
        if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results < 1:
            raise ValueError("max_results must be a positive integer")

    @staticmethod
    def _validate_sort(sorted_by: str) -> str:
        if sorted_by not in {"relevance", "date", "recency"}:
            raise ValueError(
                "Semantic Scholar sorted_by must be one of: relevance, date, recency"
            )
        return "relevance" if sorted_by == "recency" else sorted_by

    def _get_json(self, path: str, params: dict) -> dict:
        response = self.request_api(path, params)
        if isinstance(response, dict) and "error" in response:
            status = response.get("status_code", "unknown")
            message = response.get("message", response["error"])
            raise RuntimeError(
                f"Semantic Scholar API request failed (status={status}): {message}"
            )
        if not hasattr(response, "status_code") or response.status_code != 200:
            status = getattr(response, "status_code", "unknown")
            raise RuntimeError(f"Semantic Scholar API request failed (status={status})")
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("Semantic Scholar returned an invalid JSON response") from exc

    def _parse_results(self, items: List[dict], remaining: int) -> List[Paper]:
        papers: List[Paper] = []
        for item in items:
            if len(papers) >= remaining:
                break
            paper = self._parse_paper(item)
            if paper:
                papers.append(paper)
        return papers

    def _search_relevance(
        self, query: str, year: Optional[str], max_results: int
    ) -> List[Paper]:
        papers: List[Paper] = []
        offset = 0
        while len(papers) < max_results and offset < self.RELEVANCE_MAX_RESULTS:
            limit = min(
                self.RELEVANCE_PAGE_SIZE,
                max_results - len(papers),
                self.RELEVANCE_MAX_RESULTS - offset,
            )
            params = {
                "query": query,
                "fields": self.PAPER_FIELDS,
                "fieldsOfStudy": self.FINANCE_FIELDS,
                "limit": limit,
                "offset": offset,
            }
            if year is not None:
                params["year"] = year
            payload = self._get_json("paper/search", params)
            results = payload.get("data", [])
            if not results:
                break
            papers.extend(self._parse_results(results, max_results - len(papers)))
            if len(results) < limit or payload.get("next") is None:
                break
            offset = int(payload["next"])
        return papers[:max_results]

    def _search_bulk(
        self,
        query: str,
        year: Optional[str],
        max_results: int,
        sort_by_date: bool,
    ) -> List[Paper]:
        papers: List[Paper] = []
        token: Optional[str] = None
        while len(papers) < max_results:
            params = {
                "query": query,
                "fields": self.PAPER_FIELDS,
                "fieldsOfStudy": self.FINANCE_FIELDS,
            }
            if year is not None:
                params["year"] = year
            if sort_by_date:
                params["sort"] = "publicationDate:desc"
            if token is not None:
                params["token"] = token
            payload = self._get_json("paper/search/bulk", params)
            results = payload.get("data", [])
            if not results:
                break
            papers.extend(self._parse_results(results, max_results - len(papers)))
            token = payload.get("token")
            if not token:
                break
        return papers[:max_results]

    @classmethod
    def _year_bounds(cls, year: Optional[str]) -> tuple[Optional[int], Optional[int]]:
        if year is None:
            return None, None
        if re.fullmatch(r"\d{4}", year):
            value = int(year)
            return value, value
        start, end = year.split("-", 1)
        return (int(start) if start else None, int(end) if end else None)

    @classmethod
    def _matches_author_filters(
        cls,
        item: dict,
        query: str,
        year: Optional[str],
    ) -> bool:
        searchable = f"{item.get('title') or ''} {item.get('abstract') or ''}".casefold()
        if not all(term.casefold() in searchable for term in query.split()):
            return False

        categories = {
            str(category).casefold() for category in (item.get("fieldsOfStudy") or [])
        }
        categories.update(
            str(field.get("category", "")).casefold()
            for field in (item.get("s2FieldsOfStudy") or [])
            if isinstance(field, dict)
        )
        if not categories.intersection({"business", "economics"}):
            return False

        start, end = cls._year_bounds(year)
        paper_year = item.get("year")
        if start is not None or end is not None:
            if not isinstance(paper_year, int):
                return False
            if start is not None and paper_year < start:
                return False
            if end is not None and paper_year > end:
                return False
        return True

    @staticmethod
    def _author_relevance_key(item: dict, query: str) -> tuple[int, int]:
        title = str(item.get("title") or "").casefold()
        abstract = str(item.get("abstract") or "").casefold()
        score = sum(title.count(term.casefold()) * 2 for term in query.split())
        score += sum(abstract.count(term.casefold()) for term in query.split())
        return score, int(item.get("citationCount") or 0)

    @staticmethod
    def _author_date_key(item: dict) -> tuple[str, int]:
        return str(item.get("publicationDate") or ""), int(item.get("year") or 0)

    def _search_author(
        self,
        author: str,
        query: str,
        year: Optional[str],
        max_results: int,
        sorted_by: str,
    ) -> List[Paper]:
        author_payload = self._get_json(
            "author/search", {"query": author, "limit": 10, "fields": "name,paperCount"}
        )
        candidates = author_payload.get("data", [])
        if not candidates:
            return []
        exact = [
            candidate
            for candidate in candidates
            if str(candidate.get("name") or "").casefold() == author.casefold()
        ]
        selected = (exact or candidates)[0]
        author_id = selected.get("authorId")
        if not author_id:
            return []

        items: List[dict] = []
        offset = 0
        while True:
            payload = self._get_json(
                f"author/{author_id}/papers",
                {
                    "fields": self.PAPER_FIELDS,
                    "limit": self.AUTHOR_PAGE_SIZE,
                    "offset": offset,
                },
            )
            batch = payload.get("data", [])
            if not batch:
                break
            items.extend(
                item
                for item in batch
                if self._matches_author_filters(item, query=query, year=year)
            )
            next_offset = payload.get("next")
            if next_offset is None:
                break
            offset = int(next_offset)

        if sorted_by == "date":
            items.sort(key=self._author_date_key, reverse=True)
        else:
            items.sort(
                key=lambda item: self._author_relevance_key(item, query), reverse=True
            )
        return self._parse_results(items, max_results)

    def search(
        self,
        query: str,
        year: Optional[str] = None,
        max_results: int = 10,
        sorted_by: str = "relevance",
        author: Optional[str] = None,
        fetch_details: bool = False,
    ) -> List[Paper]:
        """Search Semantic Scholar using relevance, bulk, or author workflows."""
        del fetch_details  # Retained only for backward compatibility.
        normalized_query = self._normalize_text(query, "query")
        normalized_year = self._validate_year(year)
        self._validate_max_results(max_results)
        effective_sort = self._validate_sort(sorted_by)

        if author is not None:
            normalized_author = self._normalize_text(author, "author")
            return self._search_author(
                normalized_author,
                normalized_query,
                normalized_year,
                max_results,
                effective_sort,
            )
        if effective_sort == "date":
            return self._search_bulk(
                normalized_query, normalized_year, max_results, sort_by_date=True
            )
        if max_results > self.RELEVANCE_MAX_RESULTS:
            logger.warning(
                "Semantic Scholar relevance search is limited to 1000 results; "
                "switching to bulk retrieval without relevance ranking."
            )
            return self._search_bulk(
                normalized_query, normalized_year, max_results, sort_by_date=False
            )
        return self._search_relevance(normalized_query, normalized_year, max_results)

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Download PDF from Semantic Scholar

        Args:
            paper_id (str): Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")
            save_path: Path to save the PDF

        Returns:
            str: Path to downloaded file or error message
        """
        try:
            paper = self.get_paper_details(paper_id)
            if not paper or not paper.pdf_url:
                return f"Error: Could not find PDF URL for paper {paper_id}"
            pdf_url = paper.pdf_url
            pdf_response = requests.get(pdf_url, timeout=30)
            pdf_response.raise_for_status()

            # Create download directory if it doesn't exist
            os.makedirs(save_path, exist_ok=True)

            filename = f"semantic_{paper_id.replace('/', '_')}.pdf"
            pdf_path = os.path.join(save_path, filename)

            with open(pdf_path, "wb") as f:
                f.write(pdf_response.content)
            return pdf_path
        except Exception as e:
            logger.error(f"PDF download error: {e}")
            return f"Error downloading PDF: {e}"

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Download and extract text from Semantic Scholar paper PDF

        Args:
            paper_id (str): Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")
            save_path: Directory to save downloaded PDF

        Returns:
            str: Extracted text from the PDF or error message
        """
        try:
            os.makedirs(save_path, exist_ok=True)
            filename = f"semantic_{paper_id.replace('/', '_')}.pdf"
            pdf_path = os.path.join(save_path, filename)

            if not os.path.exists(pdf_path):
                paper = self.get_paper_details(paper_id)
                if not paper or not paper.pdf_url:
                    return f"Error: Could not find PDF URL for paper {paper_id}"

                pdf_response = requests.get(paper.pdf_url, timeout=30)
                pdf_response.raise_for_status()

                with open(pdf_path, "wb") as f:
                    f.write(pdf_response.content)
            else:
                paper = self.get_paper_details(paper_id)

            # Extract text using PyPDF
            reader = PdfReader(pdf_path)
            text = ""

            for page_num, page in enumerate(reader.pages):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += f"\n--- Page {page_num + 1} ---\n"
                        text += page_text + "\n"
                except Exception as e:
                    logger.warning(
                        f"Failed to extract text from page {page_num + 1}: {e}"
                    )
                    continue

            if not text.strip():
                return (
                    f"PDF downloaded to {pdf_path}, but unable to extract readable text"
                )

            # Add paper metadata at the beginning
            metadata = f"Title: {paper.title if paper else paper_id}\n"
            metadata += f"Authors: {', '.join(paper.authors) if paper else ''}\n"
            metadata += f"Published Date: {paper.published_date if paper else ''}\n"
            metadata += f"URL: {paper.url if paper else ''}\n"
            metadata += f"PDF downloaded to: {pdf_path}\n"
            metadata += "=" * 80 + "\n\n"

            return metadata + text.strip()

        except requests.RequestException as e:
            logger.error(f"Error downloading PDF: {e}")
            return f"Error downloading PDF: {e}"
        except Exception as e:
            logger.error(f"Read paper error: {e}")
            return f"Error reading paper: {e}"

    def get_paper_details(self, paper_id: str) -> Optional[Paper]:
        """
        Fetch detailed information for a specific Semantic Scholar paper

        Args:
            paper_id (str): Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")

        Returns:
            Paper: Detailed paper object with full metadata
        """
        try:
            fields = [
                "title",
                "abstract",
                "year",
                "citationCount",
                "authors",
                "url",
                "publicationDate",
                "externalIds",
                "fieldsOfStudy",
                "openAccessPdf",
            ]
            params = {
                "fields": ",".join(fields),
            }

            response = self.request_api(f"paper/{paper_id}", params)

            # Check for errors
            if isinstance(response, dict) and "error" in response:
                error_msg = response.get("message", "Unknown error")
                if response.get("error") == "rate_limited":
                    logger.error(f"Rate limited by Semantic Scholar API: {error_msg}")
                else:
                    logger.error(f"Semantic Scholar API error: {error_msg}")
                return None

            # Check response status code
            if not hasattr(response, "status_code") or response.status_code != 200:
                status_code = getattr(response, "status_code", "unknown")
                logger.error(
                    f"Semantic Scholar paper details fetch failed with status {status_code}"
                )
                return None

            results = response.json()
            paper = self._parse_paper(results)
            if paper:
                return paper
            else:
                return None
        except Exception as e:
            logger.error(f"Error fetching paper details for {paper_id}: {e}")
            return None


if __name__ == "__main__":
    # Test Semantic searcher
    searcher = SemanticSearcher()

    print("Testing Semantic search functionality...")
    query = "secret sharing"
    max_results = 2

    print("\n" + "=" * 60)
    print("1. Testing search with detailed information")
    print("=" * 60)
    try:
        papers = searcher.search(query, year=None, max_results=max_results)
        print(f"\nFound {len(papers)} papers for query '{query}' (with details):")
        for i, paper in enumerate(papers, 1):
            print(f"\n{i}. {paper.title}")
            print(f"   Paper ID: {paper.paper_id}")
            print(f"   Authors: {', '.join(paper.authors)}")
            print(f"   Categories: {', '.join(paper.categories)}")
            print(f"   URL: {paper.url}")
            if paper.pdf_url:
                print(f"   PDF: {paper.pdf_url}")
            if paper.published_date:
                print(f"   Published Date: {paper.published_date}")
            if paper.abstract:
                print(f"   Abstract: {paper.abstract[:200]}...")
    except Exception as e:
        print(f"Error during detailed search: {e}")

    print("\n" + "=" * 60)
    print("2. Testing manual paper details fetching")
    print("=" * 60)
    test_paper_id = "5bbfdf2e62f0508c65ba6de9c72fe2066fd98138"
    try:
        paper_details = searcher.get_paper_details(test_paper_id)
        if paper_details:
            print(f"\nManual fetch for paper {test_paper_id}:")
            print(f"Title: {paper_details.title}")
            print(f"Authors: {', '.join(paper_details.authors)}")
            print(f"Categories: {', '.join(paper_details.categories)}")
            print(f"URL: {paper_details.url}")
            if paper_details.pdf_url:
                print(f"PDF: {paper_details.pdf_url}")
            if paper_details.published_date:
                print(f"Published Date: {paper_details.published_date}")
            print(f"DOI: {paper_details.doi}")
            print(f"Citations: {paper_details.citations}")
            print(f"Abstract: {paper_details.abstract[:200]}...")
        else:
            print(f"Could not fetch details for paper {test_paper_id}")
    except Exception as e:
        print(f"Error fetching paper details: {e}")
