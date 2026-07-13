# paper_search_mcp/academic_platforms/crossref.py
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from ..config import get_env
from ..paper import Paper
from .base import PaperSource

logger = logging.getLogger(__name__)


CROSSREF_FINANCE_QUERY = (
    "finance financial economics econometrics "
    "asset pricing corporate finance banking "
    "investment securities portfolio derivatives "
    "risk management market microstructure fintech"
)

CROSSREF_SORT_MAP = {
    "relevance": "relevance",
    "date": "published",
    "recency": "updated",
}


class CrossRefSearcher(PaperSource):
    """Search finance-related metadata through the Crossref Works API."""

    BASE_URL = "https://api.crossref.org"
    WORKS_URL = f"{BASE_URL}/works"
    MAX_ROWS = 1000
    DEFAULT_MAILTO = "paper-search@example.org"
    YEAR_ERROR = (
        "year must use one of: YYYY, YYYY-YYYY, YYYY-, or -YYYY "
        '(for example: "2024", "2020-2024", "2020-", "-2020")'
    )

    def __init__(self, mailto: Optional[str] = None):
        self.mailto = (
            mailto
            if mailto is not None
            else get_env("CROSSREF_MAILTO", self.DEFAULT_MAILTO)
        ).strip()
        if not self.mailto:
            self.mailto = self.DEFAULT_MAILTO
        user_agent = (
            "paper-search-mcp/1.0 "
            "(https://github.com/Dragonatorul/paper-search-mcp; "
            f"mailto:{self.mailto})"
        )
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': user_agent,
            'Accept': 'application/json'
        })

    @staticmethod
    def _normalize_query(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("query must be a string")
        query = value.strip()
        if not query:
            raise ValueError("query must not be empty")
        return query

    @staticmethod
    def _normalize_author(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("author must be a string")
        normalized = " ".join(value.strip().split())
        if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
            normalized = " ".join(normalized[1:-1].strip().split())
        if not normalized:
            raise ValueError("author must not be empty")
        return normalized

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
            year_value = single.group(1)
            return (
                f"from-pub-date:{year_value}-01-01,"
                f"until-pub-date:{year_value}-12-31"
            )
        if closed:
            start_year, end_year = int(closed.group(1)), int(closed.group(2))
            if start_year > end_year:
                raise ValueError("year start must not be greater than year end")
            return (
                f"from-pub-date:{start_year:04d}-01-01,"
                f"until-pub-date:{end_year:04d}-12-31"
            )
        if since:
            return f"from-pub-date:{since.group(1)}-01-01"
        if until:
            return f"until-pub-date:{until.group(1)}-12-31"
        raise ValueError(cls.YEAR_ERROR)

    @staticmethod
    def _validate_max_results(max_results: int) -> None:
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or max_results < 1
        ):
            raise ValueError("max_results must be a positive integer")

    @staticmethod
    def _map_sort(sorted_by: str) -> str:
        try:
            return CROSSREF_SORT_MAP[sorted_by]
        except (KeyError, TypeError):
            raise ValueError(
                "Crossref sorted_by must be one of: relevance, date, recency"
            ) from None

    def _request_json(self, params: Dict[str, Any]) -> dict:
        response = None
        try:
            response = self.session.get(self.WORKS_URL, params=params, timeout=30)
            if response.status_code == 429:
                logger.warning("Rate limited by Crossref API; retrying in 2 seconds")
                time.sleep(2)
                response = self.session.get(self.WORKS_URL, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            status = getattr(response, "status_code", None)
            detail = ""
            if response is not None:
                try:
                    payload = response.json()
                    detail = str(payload.get("message") or payload.get("status") or "")
                except ValueError:
                    detail = response.text[:300]
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"Crossref API request failed (status={status}){suffix}"
            ) from exc
        except ValueError as exc:
            raise RuntimeError("Crossref returned an invalid JSON response") from exc

    def _base_search_params(
        self,
        query: str,
        sorted_by: str,
        year: Optional[str],
        author: Optional[str],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "query": self._normalize_query(query),
            "query.bibliographic": CROSSREF_FINANCE_QUERY,
            "sort": self._map_sort(sorted_by),
            "order": "desc",
            "mailto": self.mailto,
        }
        if author is not None:
            params["query.author"] = self._normalize_author(author)
        if year is not None:
            params["filter"] = self._build_year_filter(year)
        return params

    def search(
        self,
        query: str,
        max_results: int = 10,
        sorted_by: str = "relevance",
        year: Optional[str] = None,
        author: Optional[str] = None,
    ) -> List[Paper]:
        """Search Crossref using native fields and cursor pagination."""
        self._validate_max_results(max_results)
        base_params = self._base_search_params(query, sorted_by, year, author)
        use_cursor = max_results > self.MAX_ROWS
        cursor: Optional[str] = "*" if use_cursor else None
        papers: List[Paper] = []

        while len(papers) < max_results:
            rows = min(self.MAX_ROWS, max_results - len(papers))
            params = {**base_params, "rows": rows}
            if cursor is not None:
                params["cursor"] = cursor

            data = self._request_json(params)
            message = data.get("message") or {}
            items = message.get("items") or []
            if not items:
                break

            for item in items:
                if len(papers) >= max_results:
                    break
                try:
                    paper = self._parse_crossref_item(item)
                    if paper:
                        papers.append(paper)
                except Exception as exc:
                    logger.warning("Error parsing Crossref item: %s", exc)
                    continue

            if not use_cursor or len(items) < rows:
                break
            next_cursor = message.get("next-cursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        return papers[:max_results]

    def _parse_crossref_item(self, item: Dict[str, Any]) -> Optional[Paper]:
        """Parse a CrossRef API item into a Paper object."""
        try:
            # Extract basic information
            doi = item.get('DOI', '')
            title = self._extract_title(item)
            authors = self._extract_authors(item)
            abstract = item.get('abstract', '')
            
            # Extract publication date
            published_date = self._extract_date(item, 'published')
            if not published_date:
                published_date = self._extract_date(item, 'issued')
            if not published_date:
                published_date = self._extract_date(item, 'created')
            
            # Default to epoch if no date found
            if not published_date:
                published_date = datetime(1970, 1, 1)
            
            # Extract URLs
            url = item.get('URL', f"https://doi.org/{doi}" if doi else '')
            pdf_url = self._extract_pdf_url(item)
            
            # Extract additional metadata
            container_title = self._extract_container_title(item)
            publisher = item.get('publisher', '')
            categories = [item.get('type', '')]
            
            # Extract subjects/keywords if available
            subjects = item.get('subject', [])
            if isinstance(subjects, list):
                keywords = subjects
            else:
                keywords = []
            
            citations = item.get('is-referenced-by-count')
            if not isinstance(citations, int):
                citations = 0

            return Paper(
                paper_id=doi,
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                published_date=published_date,
                pdf_url=pdf_url,
                url=url,
                source='crossref',
                categories=categories,
                keywords=keywords,
                citations=citations,
                extra={
                    'publisher': publisher,
                    'container_title': container_title,
                    'volume': item.get('volume', ''),
                    'issue': item.get('issue', ''),
                    'page': item.get('page', ''),
                    'issn': item.get('ISSN', []),
                    'isbn': item.get('ISBN', []),
                    'crossref_type': item.get('type', ''),
                    'member': item.get('member', ''),
                    'prefix': item.get('prefix', '')
                }
            )
            
        except Exception as e:
            logger.error(f"Error parsing CrossRef item: {e}")
            return None
    
    def _extract_title(self, item: Dict[str, Any]) -> str:
        """Extract title from CrossRef item."""
        titles = item.get('title', [])
        if isinstance(titles, list) and titles:
            return titles[0]
        return str(titles) if titles else ''
    
    def _extract_authors(self, item: Dict[str, Any]) -> List[str]:
        """Extract author names from CrossRef item."""
        authors = []
        author_list = item.get('author', [])
        
        for author in author_list:
            if isinstance(author, dict):
                given = author.get('given', '')
                family = author.get('family', '')
                if given and family:
                    authors.append(f"{given} {family}")
                elif family:
                    authors.append(family)
                elif given:
                    authors.append(given)
                    
        return authors
    
    def _extract_date(self, item: Dict[str, Any], date_field: str) -> Optional[datetime]:
        """Extract date from CrossRef item."""
        date_info = item.get(date_field, {})
        if not date_info:
            return None
            
        date_parts = date_info.get('date-parts', [])
        if not date_parts or not date_parts[0]:
            return None
            
        parts = date_parts[0]
        try:
            year = parts[0] if len(parts) > 0 and parts[0] is not None else 1970
            month = parts[1] if len(parts) > 1 and parts[1] is not None else 1
            day = parts[2] if len(parts) > 2 and parts[2] is not None else 1
            return datetime(year, month, day)
        except (TypeError, ValueError, IndexError):
            return None
    
    def _extract_container_title(self, item: Dict[str, Any]) -> str:
        """Extract container title (journal/book title) from CrossRef item."""
        container_titles = item.get('container-title', [])
        if isinstance(container_titles, list) and container_titles:
            return container_titles[0]
        return str(container_titles) if container_titles else ''
    
    def _extract_pdf_url(self, item: Dict[str, Any]) -> str:
        """Extract PDF URL from CrossRef item."""
        # Check for link in the resource field
        resource = item.get('resource', {})
        if resource:
            primary = resource.get('primary', {})
            if primary and primary.get('URL', '').endswith('.pdf'):
                return primary['URL']
        
        # Check in links array
        links = item.get('link', [])
        for link in links:
            if isinstance(link, dict):
                content_type = link.get('content-type', '')
                if 'pdf' in content_type.lower():
                    return link.get('URL', '')
                    
        return ''
    
    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        CrossRef doesn't provide direct PDF downloads.
        
        Args:
            paper_id: DOI of the paper
            save_path: Directory to save the PDF
            
        Raises:
            NotImplementedError: Always raises this error as CrossRef doesn't provide direct PDF access
        """
        message = ("CrossRef does not provide direct PDF downloads. "
                  "CrossRef is a citation database that provides metadata about academic papers. "
                  "To access the full text, please use the paper's DOI or URL to visit the publisher's website.")
        raise NotImplementedError(message)
    
    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        CrossRef doesn't provide direct paper content access.
        
        Args:
            paper_id: DOI of the paper
            save_path: Directory for potential PDF storage (unused)
            
        Returns:
            str: Error message indicating PDF reading is not supported
        """
        message = ("CrossRef papers cannot be read directly through this tool. "
                  "CrossRef is a citation database that provides metadata about academic papers. "
                  "Only metadata and abstracts are available through CrossRef's API. "
                  "To access the full text, please use the paper's DOI or URL to visit the publisher's website.")
        return message

    def get_paper_by_doi(self, doi: str) -> Optional[Paper]:
        """
        Get a specific paper by DOI.
        
        Args:
            doi: Digital Object Identifier
            
        Returns:
            Paper object if found, None otherwise
        """
        try:
            url = f"{self.BASE_URL}/works/{doi}"
            params = {'mailto': self.mailto}
            
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 404:
                logger.warning(f"DOI not found in CrossRef: {doi}")
                return None
                
            response.raise_for_status()
            data = response.json()
            
            item = data.get('message', {})
            return self._parse_crossref_item(item)
            
        except requests.RequestException as e:
            logger.error(f"Error fetching DOI {doi} from CrossRef: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching DOI {doi}: {e}")
            return None

if __name__ == "__main__":
    # Test CrossRefSearcher functionality
    # 测试CrossRefSearcher功能
    searcher = CrossRefSearcher()
    
    # Test search functionality
    # 测试搜索功能
    print("Testing search functionality...")
    query = "machine learning"
    max_results = 5
    papers = []
    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"Found {len(papers)} papers for query '{query}':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (DOI: {paper.doi})")
            print(f"   Authors: {', '.join(paper.authors[:3])}{'...' if len(paper.authors) > 3 else ''}")
            print(f"   Published: {paper.published_date.year}")
            print(f"   Citations: {paper.citations}")
            publisher = paper.extra.get('publisher', 'N/A') if paper.extra else 'N/A'
            print(f"   Publisher: {publisher}")
            print()
    except Exception as e:
        print(f"Error during search: {e}")
    
    # Test DOI lookup functionality
    # 测试DOI查找功能
    if papers:
        print("Testing DOI lookup functionality...")
        test_doi = papers[0].doi
        try:
            paper = searcher.get_paper_by_doi(test_doi)
            if paper:
                print(f"Successfully retrieved paper by DOI: {paper.title}")
            else:
                print("Failed to retrieve paper by DOI")
        except Exception as e:
            print(f"Error during DOI lookup: {e}")
    
    # Test PDF download functionality (will return unsupported message)
    # 测试PDF下载功能（会返回不支持的提示）
    if papers:
        print("\nTesting PDF download functionality...")
        paper_id = papers[0].doi
        try:
            pdf_path = searcher.download_pdf(paper_id, "./downloads")
        except NotImplementedError as e:
            print(f"Expected error: {e}")
    
    # Test paper reading functionality (will return unsupported message)
    # 测试论文阅读功能（会返回不支持的提示）
    if papers:
        print("\nTesting paper reading functionality...")
        paper_id = papers[0].doi
        message = searcher.read_paper(paper_id)
        print(f"Message: {message}")
