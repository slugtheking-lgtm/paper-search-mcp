"""Finance-focused metadata search through the DataCite Public REST API."""

from __future__ import annotations

from datetime import datetime
import logging
import math
import re
import time
from typing import Any, Dict, List, Optional

import requests

from ..config import get_env
from ..dedup import identity_keys
from ..paper import Paper
from .base import PaperSource

logger = logging.getLogger(__name__)


DATACITE_FINANCE_TERMS = (
    "Finance",
    "Financial",
    "Economics",
    "Econometrics",
    "Banking",
    "Investment",
    "Securities",
    "Derivatives",
    "Fintech",
    '"Financial Economics"',
    '"Asset Pricing"',
    '"Corporate Finance"',
    '"Portfolio Management"',
    '"Capital Markets"',
    '"Risk Management"',
    '"Market Microstructure"',
    '"Behavioral Finance"',
)
_DATACITE_FINANCE_TERMS_QUERY = " OR ".join(DATACITE_FINANCE_TERMS)
DATACITE_FINANCE_FILTER = (
    "("
    f"subjects.subject:({_DATACITE_FINANCE_TERMS_QUERY}) OR "
    f"titles.title:({_DATACITE_FINANCE_TERMS_QUERY}) OR "
    f"descriptions.description:({_DATACITE_FINANCE_TERMS_QUERY})"
    ")"
)

DATACITE_RESOURCE_TYPE_FILTER = (
    "types.resourceTypeGeneral:("
    "JournalArticle OR Preprint OR ConferencePaper OR ConferenceProceeding OR "
    "DataPaper OR Dissertation OR Report OR Book OR BookChapter OR Text"
    ")"
)


class DataCiteSearcher(PaperSource):
    """Search finance-related literature registered with DataCite."""

    DOIS_URL = "https://api.datacite.org/dois"
    MAX_PAGE_SIZE = 1000
    MAX_RESULTS = 10_000
    DEDUPE_SCAN_FACTOR = 5
    MAX_ATTEMPTS = 3
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
    YEAR_ERROR = (
        "year must use one of: YYYY, YYYY-YYYY, YYYY-, or -YYYY "
        '(for example: "2024", "2020-2024", "2020-", "-2020")'
    )
    SEARCH_FIELDS = (
        "titles.title",
        "descriptions.description",
        "subjects.subject",
    )

    def __init__(self, mailto: Optional[str] = None) -> None:
        configured_mailto = get_env("DATACITE_MAILTO", "").strip()
        crossref_mailto = get_env("CROSSREF_MAILTO", "").strip()
        self.mailto = (
            mailto.strip()
            if mailto is not None
            else configured_mailto or crossref_mailto
        )

        contact = f"; mailto:{self.mailto}" if self.mailto else ""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.api+json",
                "User-Agent": (
                    "paper-search-mcp/1.0 "
                    f"(https://github.com/openags/paper-search-mcp{contact})"
                ),
            }
        )

    @staticmethod
    def _normalize_phrase(value: str, field_name: str) -> str:
        """Normalize public input into a literal OpenSearch phrase."""
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")
        phrase = " ".join(value.strip().split())
        if len(phrase) >= 2 and phrase.startswith('"') and phrase.endswith('"'):
            phrase = " ".join(phrase[1:-1].strip().split())
        if not phrase:
            raise ValueError(f"{field_name} must not be empty")
        if '"' in phrase or "\\" in phrase:
            raise ValueError(
                f"{field_name} must be plain text and must not contain "
                "embedded double quotes or backslashes"
            )
        return f'"{phrase}"'

    @classmethod
    def _build_year_clause(cls, year: str) -> str:
        if not isinstance(year, str):
            raise ValueError(cls.YEAR_ERROR)
        value = year.strip()
        single = re.fullmatch(r"(\d{4})", value)
        closed = re.fullmatch(r"(\d{4})-(\d{4})", value)
        since = re.fullmatch(r"(\d{4})-", value)
        until = re.fullmatch(r"-(\d{4})", value)

        if single:
            return f"publicationYear:{single.group(1)}"
        if closed:
            start_year, end_year = int(closed.group(1)), int(closed.group(2))
            if start_year > end_year:
                raise ValueError("year start must not be greater than year end")
            return f"publicationYear:[{start_year:04d} TO {end_year:04d}]"
        if since:
            return f"publicationYear:[{since.group(1)} TO *]"
        if until:
            return f"publicationYear:[* TO {until.group(1)}]"
        raise ValueError(cls.YEAR_ERROR)

    @classmethod
    def _build_search_query(
        cls,
        query: str,
        year: Optional[str] = None,
        author: Optional[str] = None,
    ) -> str:
        query_phrase = cls._normalize_phrase(query, "query")
        content_clause = "(" + " OR ".join(
            f"{field}:{query_phrase}" for field in cls.SEARCH_FIELDS
        ) + ")"

        parts = [content_clause]
        if author is not None:
            parts.append(
                f"creators.name:{cls._normalize_phrase(author, 'author')}"
            )
        if year is not None:
            parts.append(cls._build_year_clause(year))
        parts.append(DATACITE_FINANCE_FILTER)
        parts.append(DATACITE_RESOURCE_TYPE_FILTER)
        return " AND ".join(parts)

    @classmethod
    def _validate_max_results(cls, max_results: int) -> None:
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or not 1 <= max_results <= cls.MAX_RESULTS
        ):
            raise ValueError(
                f"max_results must be an integer between 1 and {cls.MAX_RESULTS}"
            )

    def _request_params(
        self, search_query: str, page_size: int, page_number: int
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "query": search_query,
            "sort": "relevance",
            "page[size]": page_size,
            "page[number]": page_number,
            "disable-facets": "true",
        }
        if self.mailto:
            params["mailto"] = self.mailto
        return params

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After", "").strip()
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return float(2 ** attempt)

    @staticmethod
    def _response_detail(response: Optional[requests.Response]) -> str:
        if response is None:
            return ""
        try:
            payload = response.json()
        except ValueError:
            return response.text[:300]
        if not isinstance(payload, dict):
            return str(payload)[:300]
        errors = payload.get("errors")
        if isinstance(errors, list):
            messages = []
            for error in errors:
                if isinstance(error, dict):
                    messages.append(
                        str(error.get("detail") or error.get("title") or error)
                    )
                else:
                    messages.append(str(error))
            return "; ".join(messages)[:300]
        return str(payload.get("message") or payload.get("error") or "")[:300]

    def _request_json(self, params: Dict[str, Any]) -> dict:
        response: Optional[requests.Response] = None
        last_exception: Optional[requests.RequestException] = None

        for attempt in range(self.MAX_ATTEMPTS):
            try:
                response = self.session.get(
                    self.DOIS_URL, params=params, timeout=30
                )
            except requests.RequestException as exc:
                last_exception = exc
                if attempt + 1 < self.MAX_ATTEMPTS:
                    time.sleep(float(2 ** attempt))
                    continue
                raise RuntimeError(
                    "DataCite API request failed before receiving a response"
                ) from exc

            if response.status_code in self.RETRYABLE_STATUS_CODES:
                if attempt + 1 < self.MAX_ATTEMPTS:
                    delay = self._retry_delay(response, attempt)
                    logger.debug(
                        "DataCite request returned %s (attempt %s/%s); "
                        "retrying in %ss",
                        response.status_code,
                        attempt + 1,
                        self.MAX_ATTEMPTS,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                detail = self._response_detail(response)
                suffix = f": {detail}" if detail else ""
                raise RuntimeError(
                    f"DataCite API request failed "
                    f"(status={response.status_code}){suffix}"
                )

            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                detail = self._response_detail(response)
                suffix = f": {detail}" if detail else ""
                raise RuntimeError(
                    f"DataCite API request failed "
                    f"(status={response.status_code}){suffix}"
                ) from exc

            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("DataCite returned an invalid JSON response") from exc
            if not isinstance(payload, dict):
                raise RuntimeError("DataCite returned an unexpected JSON response")
            return payload

        raise RuntimeError("DataCite API request failed") from last_exception

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if value is None or value == "":
            return None
        text = str(value).strip()
        if re.fullmatch(r"\d{4}", text):
            return datetime(int(text), 1, 1)
        if re.fullmatch(r"\d{4}-\d{2}", text):
            return datetime.strptime(text, "%Y-%m")
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _primary_title(titles: Any) -> str:
        if not isinstance(titles, list):
            return ""
        valid = [title for title in titles if isinstance(title, dict) and title.get("title")]
        if not valid:
            return ""
        primary = next(
            (title for title in valid if not title.get("titleType")), valid[0]
        )
        return str(primary.get("title") or "").strip()

    @staticmethod
    def _creator_names(creators: Any) -> List[str]:
        if not isinstance(creators, list):
            return []
        names: List[str] = []
        for creator in creators:
            if not isinstance(creator, dict):
                continue
            name = str(creator.get("name") or "").strip()
            if not name:
                given = str(creator.get("givenName") or "").strip()
                family = str(creator.get("familyName") or "").strip()
                name = " ".join(part for part in (given, family) if part)
            if name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _abstract(descriptions: Any) -> str:
        if not isinstance(descriptions, list):
            return ""
        abstracts = []
        for description in descriptions:
            if not isinstance(description, dict):
                continue
            description_type = str(
                description.get("descriptionType") or ""
            ).casefold()
            text = str(description.get("description") or "").strip()
            if description_type == "abstract" and text:
                abstracts.append(text)
        return "\n\n".join(abstracts)

    @classmethod
    def _published_date(cls, attributes: Dict[str, Any]) -> Optional[datetime]:
        published = cls._parse_datetime(attributes.get("published"))
        if published is not None:
            return published
        dates = attributes.get("dates")
        if isinstance(dates, list):
            for date in dates:
                if not isinstance(date, dict):
                    continue
                if str(date.get("dateType") or "").casefold() == "issued":
                    issued = cls._parse_datetime(date.get("date"))
                    if issued is not None:
                        return issued
        return cls._parse_datetime(attributes.get("publicationYear"))

    @staticmethod
    def _subjects(subjects: Any) -> List[str]:
        if not isinstance(subjects, list):
            return []
        values: List[str] = []
        for subject in subjects:
            if isinstance(subject, dict):
                value = str(subject.get("subject") or "").strip()
            else:
                value = str(subject).strip()
            if value and value not in values:
                values.append(value)
        return values

    @staticmethod
    def _publisher_name(publisher: Any) -> str:
        if isinstance(publisher, dict):
            return str(publisher.get("name") or "").strip()
        return str(publisher or "").strip()

    @staticmethod
    def _pdf_url(attributes: Dict[str, Any]) -> str:
        content_urls = attributes.get("contentUrl") or []
        if isinstance(content_urls, str):
            content_urls = [content_urls]
        if not isinstance(content_urls, list):
            content_urls = []
        candidates = [str(url).strip() for url in content_urls if str(url).strip()]
        landing_url = str(attributes.get("url") or "").strip()
        if landing_url:
            candidates.append(landing_url)
        for candidate in candidates:
            path = candidate.split("?", 1)[0].casefold()
            if path.endswith(".pdf"):
                return candidate
        return ""

    @staticmethod
    def _references(related_identifiers: Any) -> List[str]:
        if not isinstance(related_identifiers, list):
            return []
        references: List[str] = []
        for related in related_identifiers:
            if not isinstance(related, dict):
                continue
            relation_type = str(related.get("relationType") or "").casefold()
            identifier_type = str(
                related.get("relatedIdentifierType") or ""
            ).casefold()
            identifier = str(related.get("relatedIdentifier") or "").strip()
            if relation_type == "references" and identifier_type == "doi" and identifier:
                references.append(identifier)
        return references

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _paper_quality_score(paper: Paper) -> int:
        """Prefer the richer record when DataCite returns several versions."""
        return sum(
            (
                bool(getattr(paper, "abstract", "")),
                bool(getattr(paper, "authors", [])),
                bool(getattr(paper, "published_date", None)),
                bool(getattr(paper, "updated_date", None)),
                bool(getattr(paper, "pdf_url", "")),
                bool(getattr(paper, "url", "")),
                bool(getattr(paper, "categories", [])),
            )
        )

    def _parse_item(self, item: Dict[str, Any]) -> Optional[Paper]:
        if not isinstance(item, dict):
            return None
        attributes = item.get("attributes") or {}
        if not isinstance(attributes, dict):
            return None

        doi = str(attributes.get("doi") or item.get("id") or "").strip().lower()
        title = self._primary_title(attributes.get("titles"))
        if not doi or not title:
            return None

        subjects = self._subjects(attributes.get("subjects"))
        types = attributes.get("types") or {}
        if not isinstance(types, dict):
            types = {}
        publisher = self._publisher_name(attributes.get("publisher"))
        relationships = item.get("relationships") or {}
        client_data = (
            (relationships.get("client") or {}).get("data")
            if isinstance(relationships, dict)
            else None
        )
        client_id = (
            str(client_data.get("id") or "")
            if isinstance(client_data, dict)
            else ""
        )

        landing_url = str(attributes.get("url") or "").strip()
        if not landing_url:
            landing_url = f"https://doi.org/{doi}"

        return Paper(
            paper_id=doi,
            title=title,
            authors=self._creator_names(attributes.get("creators")),
            abstract=self._abstract(attributes.get("descriptions")),
            doi=doi,
            published_date=self._published_date(attributes),
            updated_date=self._parse_datetime(attributes.get("updated")),
            pdf_url=self._pdf_url(attributes),
            url=landing_url,
            source="datacite",
            categories=subjects,
            keywords=subjects.copy(),
            citations=self._optional_int(attributes.get("citationCount")),
            references=self._references(attributes.get("relatedIdentifiers")),
            extra={
                "resource_type_general": types.get("resourceTypeGeneral") or "",
                "resource_type": types.get("resourceType") or "",
                "publisher": publisher,
                "language": attributes.get("language") or "",
                "rights_list": attributes.get("rightsList") or [],
                "client_id": client_id,
                "schema_version": attributes.get("schemaVersion") or "",
                "view_count": self._safe_int(attributes.get("viewCount")),
                "download_count": self._safe_int(attributes.get("downloadCount")),
            },
        )

    def search(
        self,
        query: str,
        max_results: int = 10,
        year: Optional[str] = None,
        author: Optional[str] = None,
    ) -> List[Paper]:
        """Search DataCite with a fixed native relevance sort."""
        self._validate_max_results(max_results)
        search_query = self._build_search_query(query, year=year, author=author)
        page_size = min(self.MAX_PAGE_SIZE, max_results)
        scan_limit = min(
            self.MAX_RESULTS,
            max_results * self.DEDUPE_SCAN_FACTOR,
        )
        pages_to_request = math.ceil(scan_limit / page_size)

        papers: List[Paper] = []
        identity_to_index: Dict[str, int] = {}
        for page_number in range(1, pages_to_request + 1):
            params = self._request_params(search_query, page_size, page_number)
            payload = self._request_json(params)
            items = payload.get("data") or []
            if not isinstance(items, list):
                raise RuntimeError("DataCite response field 'data' must be a list")
            if not items:
                break

            for item in items:
                try:
                    paper = self._parse_item(item)
                except Exception as exc:
                    logger.debug("Error parsing DataCite item: %s", exc)
                    continue
                if paper is None:
                    continue

                doi_key, bibliographic_key = identity_keys(
                    getattr(paper, "doi", ""),
                    getattr(paper, "title", ""),
                    getattr(paper, "authors", []),
                )
                keys = [key for key in (doi_key, bibliographic_key) if key]
                duplicate_indexes = {
                    identity_to_index[key]
                    for key in keys
                    if key in identity_to_index
                }
                if duplicate_indexes:
                    duplicate_index = min(duplicate_indexes)
                    existing = papers[duplicate_index]
                    if self._paper_quality_score(paper) > self._paper_quality_score(existing):
                        papers[duplicate_index] = paper
                    for key in keys:
                        identity_to_index[key] = duplicate_index
                    continue

                new_index = len(papers)
                papers.append(paper)
                for key in keys:
                    identity_to_index[key] = new_index
                if len(papers) >= max_results:
                    break

            if len(papers) >= max_results or len(items) < page_size:
                break
            links = payload.get("links") or {}
            if not isinstance(links, dict) or not links.get("next"):
                break

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError(
            "DataCite indexes metadata and does not guarantee a downloadable PDF. "
            "Use the returned pdf_url or landing-page URL when available."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        raise NotImplementedError(
            "DataCite does not provide a native paper-reading endpoint."
        )
