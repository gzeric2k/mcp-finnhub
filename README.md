# Finnhub MCP Server

An MCP server to interface with Finnhub API.

### Tools

- `list_news`

  - List latest market news from Finnhub [market news endpoint](https://finnhub.io/docs/api/market-news)

- `get_market_data`

  - Get market data for a particular stock from [quote endpoint](https://finnhub.io/docs/api/quote)

- `get_basic_financials`

  - Get basic financials for a particular stock from [basic financials endpoint](https://finnhub.io/docs/api/company-basic-financials)

- `list_basic_financial_metrics`

  - Discover available metric keys (top-level `metric` and `series` keys) before querying large datasets.

- `get_basic_financials_by_period`

  - Return one reporting period (e.g. `2024-12-31` or `latest`) across many series metrics.

- `get_basic_financial_metric_timeseries`

  - Return one series metric across multiple periods, with optional `limit`.

- `get_basic_financials_compact`

  - Compact response with field filters and per-series period caps to avoid oversized JSON payloads.

- `get_recommendation_trends`
  - Get recommendation trends for a particular stock from [recommendation trend endpoint](https://finnhub.io/docs/api/company-basic-financials)

- `get_financials_reported`
  - Get reported financial statements for a particular stock from [financials reported endpoint](https://finnhub.io/docs/api/financials-reported)

- `get_financials_reported_by_period`

  - Get one reported period (or `latest`) with optional statement section/concept filters.

- `get_financials_reported_concept_timeseries`

  - Get one reported concept across periods with optional section and limit.

- `get_financials_reported_compact`

  - Get compact reported financial payload with period and concept caps. Default output is CSV to avoid token truncation.

- `list_financials_reported_concepts`

  - Discover concepts/labels/units for reported financial statements (discover step before query).

### Large JSON Mitigation (Recommended Query Flow)

For symbols like `AAPL`, `get_basic_financials(metric="all")` can be very large. Prefer this flow:

1. Discover keys:

```text
list_basic_financial_metrics(stock="AAPL", freq="annual", search="eps")
```

2. Query single metric across periods:

```text
get_basic_financial_metric_timeseries(
  stock="AAPL",
  metric_key="epsGrowth5Y",
  freq="annual",
  limit=8
)
```

3. Query single period across many metrics:

```text
get_basic_financials_by_period(
  stock="AAPL",
  period="latest",
  freq="annual",
  series_metric_limit=60
)
```

4. Get compact payload with filters:

```text
get_basic_financials_compact(
  stock="AAPL",
  freq="quarterly",
  metric_fields_csv="52WeekHigh,52WeekLow",
  series_metrics_csv="eps,roeRfy",
  period_limit=4,
  metric_field_limit=20,
  series_metric_limit=40
)
```

The new slicing tools also return a `meta` section with:

- `truncated`: whether limits reduced output
- `requested_vs_returned_counts`: requested vs returned item counts
- `applied_limits`: effective limit/frequency settings
- `fetched_at` and `source`: fetch timestamp and source marker

### Reported Financials Large JSON Mitigation

1. Single period all sections (or selected sections):

0. Discover concepts first (recommended):

```text
list_financials_reported_concepts(
  stock="AAPL",
  freq="annual",
  sections_csv="ic,bs,cf",
  search="revenue",
  period_limit=8,
  concept_limit=200
)
```

Then use the discovered `concept` values with `get_financials_reported_concept_timeseries`.

1. Single period all sections (or selected sections):

```text
get_financials_reported_by_period(
  stock="AAPL",
  period="latest",
  freq="annual",
  sections_csv="ic,bs",
  concept_limit=80
)
```

2. Single concept across periods:

```text
get_financials_reported_concept_timeseries(
  stock="AAPL",
  concept="us-gaap_Revenues",
  freq="annual",
  section="ic",
  limit=8
)
```

3. Compact multi-period payload (defaults to highly compressed CSV format):

```text
get_financials_reported_compact(
  stock="AAPL",
  freq="quarterly",
  sections_csv="ic,cf",
  concepts_csv="us-gaap_Revenues,us-gaap_NetIncomeLoss",
  period_limit=4,
  concept_limit=30,
  output_format="csv"
)
```

## Configuration

1. Run `uv sync` to install the dependencies. To install `uv` follow the instructions [here](https://docs.astral.sh/uv/). Then do `source .venv/bin/activate`.

2. Setup the `.env` file with the Finnhub API Key credentials.

```
FINNHUB_API_KEY=<FINNHUB_API_KEY>
```

### Volume / Candles

This server exposes `get_market_data` for quotes. For daily volume, use the `get_daily_volume` tool which calls Finnhub's `/stock/candle` endpoint and returns `v` (volume) plus `t` (timestamps).

Example (Apple, last 10 days window):

```
get_daily_volume(stock="AAPL", days=10)
```

3. Run `fastmcp install server.py` to install the server.

4. Open the configuration file located at:

   - On macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - On Windows: `%APPDATA%/Claude/claude_desktop_config.json`

5. Locate the command entry for `uv` and replace it with the absolute path to the `uv` executable. This ensures that the correct version of `uv` is used when starting the server.

6. Restart Claude Desktop to apply the changes.

## Development

Run `fastmcp dev server.py` to start the MCP server. MCP inspector is helpful for investigating and debugging locally.
