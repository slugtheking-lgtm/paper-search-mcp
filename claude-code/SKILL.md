---
name: paper-search
description: Search, download, and read academic papers from arXiv, CORE, DOAJ, Semantic Scholar, OpenAlex, Crossref, and DataCite.
---

# Paper Search

Use the `paper-search` CLI to search the seven supported academic sources.

```bash
paper-search search "<query>" -y <year> -s <sources> -n <max_per_source> -au "<author>"
paper-search download <source> <paper_id> -o ./downloads
paper-search read <source> <paper_id> -o ./downloads
paper-search sources
```

Valid source names are `arxiv`, `core`, `doaj`, `semantic`, `openalex`,
`crossref`, and `datacite`. Use `all` to search all seven sources. Every source
uses its native relevance ordering; the public CLI does not accept a sorting
parameter.
