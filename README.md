# paper-search-mcp

An MCP server and command-line tool for searching academic papers from six sources:

- arXiv
- CORE
- DOAJ
- Semantic Scholar (`semantic`)
- OpenAlex
- Crossref

## Install

```bash
pip install paper-search-mcp
```

## MCP server

```bash
paper-search-mcp
```

The server provides a unified `search_papers` tool, source-specific search tools, and source-specific download/read tools. Some metadata providers do not host full text and may return an explanatory error for download or read operations.

## CLI

```bash
paper-search sources
paper-search search "momentum factor" -y 2020-2024 -s arxiv -n 20 -sort date -au "Clifford Asness"
paper-search download arxiv 2106.15928 -o ./downloads
paper-search read arxiv 2106.15928 -o ./downloads
```

Search parameters are `query`, `-y/--year`, `-s/--sources`,
`-n/--max-results`, `-sort/--sorted-by`, and `-au/--author`.
`-s all` searches all six sources. arXiv, CORE, DOAJ, Semantic Scholar, and OpenAlex
apply the year, sort, and author parameters and always limit results to
finance-related records.
CORE and DOAJ accept `relevance`, `date`, and `recency`; each connector maps
those values to its native sort syntax. For DOAJ, `date` sorts by record
creation time and `recency` by the latest metadata update because DOAJ's live
API does not accept `bibjson.year` as a sortable field.

Semantic Scholar uses relevance search for `relevance` and `recency` (the API
has no native recency order), and token-paginated bulk search for `date`.

OpenAlex maps `relevance`, `date`, and `recency` to relevance score,
publication date, and metadata update date respectively. It restricts works to
the Finance field and article/review types, resolves author names to OpenAlex
Author IDs, and uses cursor pagination for more than 100 results.

Crossref maps `relevance`, `date`, and `recency` to relevance, published date,
and metadata update time. Finance terms are sent through its bibliographic
query as a soft relevance constraint, while year and author use native fields.
Requests above 1,000 results use cursor pagination.

## Configuration

The loader checks the project root `.env`, then `.env.example`, then the user
configuration file at `~/.config/paper-search-mcp/.env`. An explicit
`PAPER_SEARCH_MCP_ENV_FILE` path overrides this search. API keys are optional,
but can improve rate limits or access:

```dotenv
PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY=
PAPER_SEARCH_MCP_CORE_API_KEY=
PAPER_SEARCH_MCP_DOAJ_API_KEY=
PAPER_SEARCH_MCP_OPENALEX_API_KEY=
PAPER_SEARCH_MCP_CROSSREF_MAILTO=your-email@example.com
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
