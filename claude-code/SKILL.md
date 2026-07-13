---
name: paper-search
description: Search, download, and read academic papers from arXiv, CORE, DOAJ, Semantic Scholar, OpenAlex, and Crossref.
---

# Paper Search

Use the `paper-search` CLI to search the six supported academic sources.

```bash
paper-search search "<query>" -y <year> -s <sources> -n <max_per_source> -sort <sorting> -au "<author>"
paper-search download <source> <paper_id> -o ./downloads
paper-search read <source> <paper_id> -o ./downloads
paper-search sources
```

Valid source names are `arxiv`, `core`, `doaj`, `semantic`, `openalex`, and `crossref`.
The arXiv sorting values are `relevance`, `date`, and `updated`. CORE accepts
`relevance`, `date`, and `recency`; DOAJ accepts the same three CORE values.
Semantic Scholar accepts those values too; `recency` falls back to relevance.
OpenAlex accepts `relevance`, `date`, and `recency`; these map to relevance
score, publication date, and metadata update date.
Crossref accepts `relevance`, `date`, and `recency`; these map to relevance,
publication date, and metadata update time.
