# paper_search_mcp/paper.py
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional

@dataclass
class Paper:
    """Standardized paper format with core fields for academic sources"""
    # 核心字段（必填，但允许空值或默认值）
    paper_id: str              # Unique identifier (e.g., arXiv ID, PMID, DOI)
    title: str                 # Paper title
    authors: List[str]         # List of author names
    abstract: str              # Abstract text
    doi: str                   # Digital Object Identifier
    published_date: Optional[datetime]   # Publication date
    pdf_url: str               # Direct PDF link
    url: str                   # URL to paper page
    source: str                # Source platform (e.g., 'arxiv', 'core')

    # 可选字段
    updated_date: Optional[datetime] = None        # Last updated date
    categories: Optional[List[str]] = None         # Subject categories
    keywords: Optional[List[str]] = None           # Keywords
    citations: Optional[int] = None                 # Citation count, if reported
    references: Optional[List[str]] = None         # List of reference IDs/DOIs
    extra: Optional[Dict] = None                   # Source-specific extra metadata

    def __post_init__(self):
        """Post-initialization to handle default values"""
        if self.authors is None:
            self.authors = []
        if self.categories is None:
            self.categories = []
        if self.keywords is None:
            self.keywords = []
        if self.references is None:
            self.references = []
        if self.extra is None:
            self.extra = {}

    def to_dict(self) -> Dict:
        """Convert a paper to the compact public search-result schema."""
        topics = []
        for values in (self.categories, self.keywords):
            if isinstance(values, str):
                values = [values]
            for value in values or []:
                normalized = str(value or "").strip()
                if normalized and normalized not in topics:
                    topics.append(normalized)

        return {
            'paper_id': self.paper_id,
            'title': self.title,
            'authors': list(self.authors or []),
            'abstract': self.abstract,
            'doi': self.doi or None,
            'published_date': self.published_date.isoformat() if self.published_date else None,
            'pdf_url': self.pdf_url or None,
            'url': self.url or None,
            'sources': [self.source] if self.source else [],
            'topics': topics,
            'citations': self.citations,
        }
