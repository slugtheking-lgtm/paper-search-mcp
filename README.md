# paper-search-mcp

An MCP server and command-line tool for searching academic papers from seven
default sources:

- arXiv
- CORE
- DOAJ
- Semantic Scholar (`semantic`)
- OpenAlex
- Crossref
- DataCite (`datacite`)

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
paper-search search "momentum factor" -y 2020-2024 -s arxiv -n 20 -au "Clifford Asness"
paper-search search "momentum factor" -y 2020-2024 -s datacite -n 20 -au "Clifford Asness"
paper-search download arxiv 2106.15928 -o ./downloads
paper-search read arxiv 2106.15928 -o ./downloads
```

Search parameters are `query`, `-y/--year`, `-s/--sources`,
`-n/--max-results`, and `-au/--author`. `-s all` searches all seven sources,
including DataCite. Every connector uses relevance ordering; the CLI and MCP
interfaces do not expose a sorting parameter.

## HTTP API

Start the FastAPI service on all network interfaces and port 8000:

```bash
paper-search-api
```

The host and port can be changed with `PAPER_SEARCH_API_HOST` and
`PAPER_SEARCH_API_PORT`. The service provides `GET /health`, `POST /search`,
interactive documentation at `/docs`, and an OpenAPI schema at
`/openapi.json`.

Example request:

```json
{
  "query": "asset pricing",
  "year": "2020-",
  "sources": "all",
  "max_results": 10,
  "author": null
}
```

Search responses contain only the `papers` collection.

arXiv and CORE send their native relevance option, DOAJ preserves the search
engine's default relevance order, Semantic Scholar uses its native relevance
search endpoint, OpenAlex sorts by relevance score, and Crossref and DataCite
request relevance order explicitly. Semantic Scholar can return at most the
first 1,000 native relevance-ranked results in one search.

OpenAlex restricts works to the Finance field and article/review types,
resolves author names to OpenAlex Author IDs, and uses cursor pagination for
more than 100 results. Crossref sends finance terms through its bibliographic
query as a soft relevance constraint, while year and author use native fields;
requests above 1,000 results use cursor pagination.

DataCite searches titles, descriptions, and subjects, applies finance and
literature-type query blocks, and always uses the API's native relevance
ordering. Public metadata search requires no DataCite API key. An optional contact email
improves the public API rate-limit tier. DataCite accepts at most 10,000 results
per search so that relevance ordering can be preserved with numbered pages.
DataCite version records are deduplicated by normalized DOI and by normalized
title plus authors; pagination continues after duplicates so `max_results`
remains a limit on unique results. The same two-key strategy is used when
merging results across sources.

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
PAPER_SEARCH_MCP_DATACITE_MAILTO=your-email@example.com
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
