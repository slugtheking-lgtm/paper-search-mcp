"""Shared identity normalization and order-preserving paper deduplication."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping, Optional, Tuple


def normalize_doi(value: Any) -> str:
    """Return a comparable DOI, collapsing known repository version suffixes."""
    doi = str(value or "").strip().casefold()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if doi.startswith(prefix):
            doi = doi[len(prefix):].strip()
            break

    # Figshare exposes the concept DOI and version DOIs such as ``.v1`` and
    # ``.v2`` as separate records. They represent versions of the same work.
    if ".figshare." in doi:
        doi = re.sub(r"\.v\d+$", "", doi)
    return doi


def normalize_words(value: Any) -> str:
    """Normalize Unicode text while retaining letters and numbers."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in text)
        .split()
    )


def normalize_authors(value: Any) -> str:
    """Normalize author order and common ``family, given`` formatting."""
    if isinstance(value, str):
        authors: Iterable[Any] = value.split(";")
    elif isinstance(value, (list, tuple, set)):
        authors = value
    else:
        authors = []

    normalized = []
    for author in authors:
        words = normalize_words(author).split()
        if words:
            # Sorting tokens makes "Fama, Eugene F." comparable to
            # "Eugene F. Fama" without relying on punctuation conventions.
            normalized.append(" ".join(sorted(words)))
    return ";".join(sorted(set(normalized)))


def identity_keys(
    doi: Any,
    title: Any,
    authors: Any,
) -> Tuple[Optional[str], Optional[str]]:
    """Build independent DOI and bibliographic keys for one paper."""
    normalized_doi = normalize_doi(doi)
    doi_key = f"doi:{normalized_doi}" if normalized_doi else None

    normalized_title = normalize_words(title)
    normalized_authors = normalize_authors(authors)
    bibliographic_key = None
    if normalized_title and normalized_authors:
        bibliographic_key = (
            f"title:{normalized_title}|authors:{normalized_authors}"
        )
    return doi_key, bibliographic_key


def mapping_identity_keys(
    paper: Mapping[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    return identity_keys(
        paper.get("doi"),
        paper.get("title"),
        paper.get("authors"),
    )


def _source_names(paper: Mapping[str, Any]) -> list[str]:
    values = paper.get("sources")
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = []
    legacy_source = str(paper.get("source") or "").strip()
    candidates = [*values, legacy_source]
    output: list[str] = []
    for value in candidates:
        source = str(value or "").strip().lower()
        if source and source not in output:
            output.append(source)
    return output


def _merge_unique_values(first: Any, second: Any) -> list[Any]:
    output: list[Any] = []
    for values in (first, second):
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple, set)):
            continue
        for value in values:
            if value not in (None, "") and value not in output:
                output.append(value)
    return output


def _merge_duplicate(target: dict[str, Any], duplicate: Mapping[str, Any]) -> None:
    sources = _merge_unique_values(_source_names(target), _source_names(duplicate))
    if sources or "sources" in target or "sources" in duplicate or "source" in duplicate:
        target["sources"] = sources
        target.pop("source", None)

    if "topics" in target or "topics" in duplicate:
        target["topics"] = _merge_unique_values(
            target.get("topics"), duplicate.get("topics")
        )

    for field in (
        "paper_id",
        "title",
        "authors",
        "abstract",
        "doi",
        "published_date",
        "pdf_url",
        "url",
    ):
        if target.get(field) in (None, "", []):
            replacement = duplicate.get(field)
            if replacement not in (None, "", []):
                target[field] = replacement

    citation_values = [
        value
        for value in (target.get("citations"), duplicate.get("citations"))
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    if citation_values:
        target["citations"] = max(citation_values)


def dedupe_paper_dicts(papers: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate papers and merge every provider that returned each work."""
    doi_indexes: dict[str, int] = {}
    bibliographic_indexes: dict[str, int] = {}
    fallback_indexes: dict[str, int] = {}
    output: list[dict[str, Any]] = []

    for paper in papers:
        normalized_paper = dict(paper)
        sources_present = "sources" in normalized_paper or "source" in normalized_paper
        if sources_present:
            normalized_paper["sources"] = _source_names(normalized_paper)
            normalized_paper.pop("source", None)

        doi_key, bibliographic_key = mapping_identity_keys(normalized_paper)
        paper_id = str(normalized_paper.get("paper_id") or "").strip().casefold()
        fallback_id = (
            f"id:{paper_id}" if paper_id and not doi_key and not bibliographic_key
            else None
        )
        existing_index = None
        for key, indexes in (
            (doi_key, doi_indexes),
            (bibliographic_key, bibliographic_indexes),
            (fallback_id, fallback_indexes),
        ):
            if key and key in indexes:
                existing_index = indexes[key]
                break

        if existing_index is not None:
            _merge_duplicate(output[existing_index], normalized_paper)
            if doi_key:
                doi_indexes[doi_key] = existing_index
            if bibliographic_key:
                bibliographic_indexes[bibliographic_key] = existing_index
            if fallback_id:
                fallback_indexes[fallback_id] = existing_index
            continue

        new_index = len(output)
        output.append(normalized_paper)
        if doi_key:
            doi_indexes[doi_key] = new_index
        if bibliographic_key:
            bibliographic_indexes[bibliographic_key] = new_index
        if fallback_id:
            fallback_indexes[fallback_id] = new_index

    return output


def sort_papers_by_date_desc(
    papers: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Sort ISO-like publication dates newest first and keep unknown dates last."""
    def key(paper: Mapping[str, Any]) -> tuple[int, int, int, int, int, int, int]:
        value = str(paper.get("published_date") or "").strip()
        match = re.match(
            r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?",
            value,
        )
        if not match:
            return (0, 0, 0, 0, 0, 0, 0)
        parts = [int(part or 0) for part in match.groups()[:6]]
        microseconds = int(((match.group(7) or "") + "000000")[:6])
        return (*parts, microseconds)

    return [dict(paper) for paper in sorted(papers, key=key, reverse=True)]
