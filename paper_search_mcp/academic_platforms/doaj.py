# paper_search_mcp/academic_platforms/doaj.py
"""Searcher for DOAJ (Directory of Open Access Journals).

DOAJ is a community-curated online directory that indexes and provides
access to high quality, open access, peer-reviewed journals.

API Documentation: https://doaj.org/api/v2
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import requests
import logging
import re
import time
from urllib.parse import quote
from ..paper import Paper
from ..utils import extract_doi
from ..config import get_env
from .base import PaperSource

logger = logging.getLogger(__name__)

DOAJ_FINANCE_FILTER = (
    "("
    'index.classification:"Social Sciences: Finance" OR '
    'index.classification:"Social Sciences: Commerce: Business" OR '
    'index.classification:"Accounting. Bookkeeping" OR '
    'index.classification:"Public finance" OR '
    "bibjson.subject.term:finance OR "
    "bibjson.subject.term:financial OR "
    "bibjson.subject.term:economics OR "
    "bibjson.subject.term:banking OR "
    "bibjson.subject.term:investment OR "
    "bibjson.subject.term:securities OR "
    "bibjson.subject.term:insurance OR "
    "bibjson.keywords:finance OR "
    "bibjson.keywords:financial OR "
    "bibjson.keywords:banking OR "
    "bibjson.keywords:investment"
    ")"
)

class DOAJSearcher(PaperSource):
    """Searcher for DOAJ (Directory of Open Access Journals)."""

    BASE_URL = "https://doaj.org/api"
    PAGE_SIZE = 100
    USER_AGENT = "paper-search-mcp/0.1.3 (https://github.com/openags/paper-search-mcp)"
    YEAR_ERROR = (
        "year must use one of: YYYY, YYYY-YYYY, YYYY-, or -YYYY "
        '(for example: "2024", "2020-2024", "2020-", "-2020")'
    )

    def __init__(self, api_key: Optional[str] = None):
        """Initialize DOAJ searcher.

        Args:
            api_key: DOAJ API key (optional, free registration required)
                     Can also be set via DOAJ_API_KEY environment variable.
        """
        self.api_key = api_key or get_env("DOAJ_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': self.USER_AGENT,
            'Accept': 'application/json'
        })

        if self.api_key:
            self.session.headers.update({'X-API-Key': self.api_key})

    @staticmethod
    def _normalize_phrase(value: str, field_name: str) -> str:
        """Normalize user input as a literal DOAJ phrase."""
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
    def _build_year_condition(cls, year: str) -> str:
        """Compile a public year expression to a DOAJ bibjson.year condition."""
        if not isinstance(year, str):
            raise ValueError(cls.YEAR_ERROR)
        value = year.strip()
        single = re.fullmatch(r"(\d{4})", value)
        closed = re.fullmatch(r"(\d{4})-(\d{4})", value)
        since = re.fullmatch(r"(\d{4})-", value)
        until = re.fullmatch(r"-(\d{4})", value)

        if single:
            return f"bibjson.year:{single.group(1)}"
        if closed:
            start_year, end_year = int(closed.group(1)), int(closed.group(2))
            if start_year > end_year:
                raise ValueError("year start must not be greater than year end")
            return f"bibjson.year:[{start_year:04d} TO {end_year:04d}]"
        if since:
            return f"bibjson.year:>={since.group(1)}"
        if until:
            return f"bibjson.year:<={until.group(1)}"
        raise ValueError(cls.YEAR_ERROR)

    @classmethod
    def _build_search_query(
        cls,
        query: str,
        year: Optional[str] = None,
        author: Optional[str] = None,
    ) -> str:
        """Build query, finance scope, author, and year in DOAJ order."""
        parts = [cls._normalize_phrase(query, "query"), DOAJ_FINANCE_FILTER]
        if author is not None:
            parts.append(f'bibjson.author.name:{cls._normalize_phrase(author, "author")}')
        if year is not None:
            parts.append(cls._build_year_condition(year))
        return " AND ".join(parts)

    @staticmethod
    def _validate_max_results(max_results: int) -> None:
        if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results < 1:
            raise ValueError("max_results must be a positive integer")

    def search(
        self,
        query: str,
        max_results: int = 10,
        year: Optional[str] = None,
        author: Optional[str] = None,
    ) -> List[Paper]:
        """Search DOAJ with client-controlled page and pageSize pagination."""
        self._validate_max_results(max_results)
        search_query = self._build_search_query(query, year=year, author=author)
        search_url = f"{self.BASE_URL}/search/articles/{quote(search_query, safe='')}"
        papers: List[Paper] = []
        records_seen = 0
        page = 1

        while records_seen < max_results:
            page_size = min(self.PAGE_SIZE, max_results - records_seen)
            params: Dict[str, Any] = {"page": page, "pageSize": page_size}

            try:
                response = self.session.get(search_url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.RequestException as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                logger.debug("DOAJ API request failed (status=%s): %s", status_code, exc)
                if status_code is not None:
                    if status_code == 429:
                        logger.debug("DOAJ rate limit exceeded.")
                raise RuntimeError(
                    f"DOAJ API request failed (status={status_code}): {exc}"
                ) from exc
            except ValueError as exc:
                logger.debug("Failed to parse DOAJ JSON response: %s", exc)
                raise RuntimeError("DOAJ returned an invalid JSON response") from exc

            if "error" in data:
                logger.debug("DOAJ API error: %s", data["error"])
                raise RuntimeError(f'DOAJ API error: {data["error"]}')
            results = data.get("results", [])
            if not results:
                break

            for item in results:
                try:
                    paper = self._parse_doaj_item(item)
                    if paper:
                        papers.append(paper)
                except Exception as exc:
                    logger.debug("Error parsing DOAJ item: %s", exc)

            records_seen += len(results)
            total = data.get("total")
            if len(results) < page_size:
                break
            if isinstance(total, int) and records_seen >= total:
                break
            if records_seen >= max_results:
                break

            page += 1
            time.sleep(0.5 if self.api_key else 1.0)

        return papers[:max_results]

    def _parse_doaj_item(self, item: Dict[str, Any]) -> Optional[Paper]:
        """Parse DOAJ API response item to Paper object.

        Args:
            item: DOAJ article item from API response

        Returns:
            Paper object or None if parsing fails
        """
        try:
            bibjson = item.get('bibjson', {})
            if not bibjson:
                return None

            # Extract title
            title = bibjson.get('title', '')
            if not title:
                return None

            # Extract authors
            authors = []
            author_list = bibjson.get('author', [])
            for author in author_list:
                name = author.get('name', '')
                if name:
                    authors.append(name.strip())

            # Extract abstract
            abstract = ''
            abstract_elem = bibjson.get('abstract')
            if isinstance(abstract_elem, str):
                abstract = abstract_elem
            elif isinstance(abstract_elem, dict):
                abstract = abstract_elem.get('text', '')

            # Extract DOI
            doi = ''
            identifiers = bibjson.get('identifier', [])
            for ident in identifiers:
                if ident.get('type') == 'doi' and ident.get('id'):
                    doi = ident['id']
                    break

            # Extract publication date
            published_date = None
            year = bibjson.get('year')
            month = bibjson.get('month', 1)
            day = bibjson.get('day', 1)

            if year:
                try:
                    published_date = datetime(int(year), int(month), int(day))
                except (ValueError, TypeError):
                    # Try just year
                    try:
                        published_date = datetime(int(year), 1, 1)
                    except (ValueError, TypeError):
                        pass

            # Extract journal information
            journal = bibjson.get('journal', {})
            journal_title = journal.get('title', '')
            journal_issn = journal.get('issn', '')
            if isinstance(journal_issn, list):
                journal_issn = journal_issn[0] if journal_issn else ''

            # Extract keywords
            keywords = []
            keywords_list = bibjson.get('keywords', [])
            if isinstance(keywords_list, list):
                keywords = [kw.strip() for kw in keywords_list if isinstance(kw, str) and kw.strip()]

            # Extract subject categories
            categories = []
            subject_list = bibjson.get('subject', [])
            if isinstance(subject_list, list):
                categories = [sub.get('term', '') for sub in subject_list if isinstance(sub, dict)]
                categories = [cat for cat in categories if cat]

            # Extract links (PDF and HTML)
            pdf_url = ''
            url = item.get('admin', {}).get('url', '')

            links = bibjson.get('link', [])
            for link in links:
                if isinstance(link, dict):
                    link_type = link.get('type', '')
                    link_url = link.get('url', '')
                    if link_type == 'fulltext' and link_url:
                        if link_url.lower().endswith('.pdf'):
                            pdf_url = link_url
                        elif not url:
                            url = link_url

            # If no PDF found, check for fulltext PDF in other fields
            if not pdf_url and 'fulltext' in bibjson:
                fulltext = bibjson.get('fulltext')
                if isinstance(fulltext, str) and fulltext.lower().endswith('.pdf'):
                    pdf_url = fulltext

            # Construct DOAJ URL if not available
            if not url and doi:
                url = f"https://doi.org/{doi}"
            elif not url:
                # Use DOAJ article page
                article_id = item.get('id', '')
                if article_id:
                    url = f"https://doaj.org/article/{article_id}"

            # Create Paper object
            paper = Paper(
                paper_id=item.get('id', '') or doi or f"doaj_{hash(title) & 0xffffffff:08x}",
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                published_date=published_date,
                pdf_url=pdf_url,
                url=url,
                source='doaj',
                categories=categories,
                keywords=keywords
            )

            # Add extra metadata
            paper.extra = {
                'journal': journal_title,
                'issn': journal_issn,
                'publisher': journal.get('publisher', {}),
                'country': journal.get('country', ''),
                'language': bibjson.get('language', ''),
                'license': bibjson.get('license', [{}])[0] if isinstance(bibjson.get('license'), list) else {},
                'start_page': bibjson.get('start_page', ''),
                'end_page': bibjson.get('end_page', ''),
                'volume': bibjson.get('volume', ''),
                'number': bibjson.get('number', '')
            }

            return paper

        except Exception as e:
            logger.debug(f"Error parsing DOAJ article: {e}")
            return None

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """Download PDF for a DOAJ article.

        DOAJ provides direct PDF links for open access articles.

        Args:
            paper_id: DOAJ article ID or DOI
            save_path: Directory to save PDF

        Returns:
            Path to saved PDF file

        Raises:
            ValueError: If paper not found or no PDF available
            IOError: If download fails
        """
        # Try to get paper info first
        papers = self.search(paper_id, max_results=1)
        if not papers:
            raise ValueError(f"DOAJ article not found: {paper_id}")

        paper = papers[0]
        if not paper.pdf_url:
            # Try to construct PDF URL from DOI
            if paper.doi:
                # Some publishers provide direct PDF links via DOI
                pdf_url = f"https://doi.org/{paper.doi}"
                # But we need to check if it's actually a PDF
                # For now, try the URL
                paper.pdf_url = pdf_url
            else:
                raise ValueError(f"No PDF available for DOAJ article: {paper_id}")

        # Download PDF
        import os
        response = self.session.get(paper.pdf_url, timeout=30)
        response.raise_for_status()

        # Check if response is actually PDF
        content_type = response.headers.get('content-type', '')
        if 'pdf' not in content_type.lower() and not paper.pdf_url.lower().endswith('.pdf'):
            logger.warning(f"Response may not be PDF: {content_type}")

        os.makedirs(save_path, exist_ok=True)

        # Create safe filename
        safe_id = paper_id.replace('/', '_').replace(':', '_')
        filename = f"doaj_{safe_id}.pdf"
        output_file = os.path.join(save_path, filename)

        with open(output_file, 'wb') as f:
            f.write(response.content)

        logger.info(f"Downloaded PDF to {output_file}")
        return output_file

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read paper text from PDF.

        Args:
            paper_id: Paper identifier
            save_path: Directory where PDF is/will be saved

        Returns:
            Extracted text content

        Raises:
            NotImplementedError: If PDF cannot be read
        """
        try:
            # Try to download PDF first
            pdf_path = self.download_pdf(paper_id, save_path)

            # Extract text from PDF
            from pypdf import PdfReader
            reader = PdfReader(pdf_path)
            text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            return text.strip()
        except Exception as e:
            logger.error(f"Error reading DOAJ paper {paper_id}: {e}")
            raise NotImplementedError(
                f"Cannot read paper from DOAJ: {e}"
            )


if __name__ == "__main__":
    """Test the DOAJSearcher."""
    import logging
    logging.basicConfig(level=logging.INFO)

    # Test with and without API key
    searcher = DOAJSearcher()

    # Test search
    print("Testing DOAJ search...")
    test_queries = [
        "machine learning",
        "open access",
        "climate change"
    ]

    for query in test_queries[:1]:  # Test first query only
        print(f"\nSearching DOAJ for: '{query}'")
        papers = searcher.search(query, max_results=3)
        print(f"Found {len(papers)} papers")
        for i, paper in enumerate(papers):
            print(f"{i+1}. {paper.title}")
            print(f"   Authors: {', '.join(paper.authors[:3])}")
            print(f"   Journal: {paper.extra.get('journal', 'Unknown')}")
            print(f"   Year: {paper.published_date.year if paper.published_date else 'Unknown'}")
            print(f"   DOI: {paper.doi}")
            print(f"   PDF: {'Yes' if paper.pdf_url else 'No'}")
            print()
