from fastmcp import FastMCP
from dotenv import load_dotenv
from finnhub import Client
import logging
import os
import time
from typing import Any

MCP_SERVER_NAME = "mcp-finnhub"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(MCP_SERVER_NAME)

load_dotenv()

finnhub_client = Client(api_key=os.getenv("FINNHUB_API_KEY"))

# 改动 1：删除 dependencies=deps（V2 不支持）
mcp = FastMCP(MCP_SERVER_NAME)


def _normalize_freq(freq: str) -> str:
    normalized_freq = freq.strip().lower()
    if normalized_freq not in {"all", "annual", "quarterly"}:
        raise ValueError("freq must be one of: 'all', 'annual', 'quarterly'")
    return normalized_freq


def _split_csv(csv_text: str) -> list[str]:
    if not csv_text.strip():
        return []
    return [item.strip() for item in csv_text.split(",") if item.strip()]


def _series_bucket(
    payload: dict[str, Any],
    freq: str,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    series = payload.get("series")
    if not isinstance(series, dict):
        return {}

    if freq == "all":
        result: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for bucket_name in ("annual", "quarterly"):
            bucket = series.get(bucket_name)
            if isinstance(bucket, dict):
                result[bucket_name] = bucket
        return result

    bucket = series.get(freq)
    if not isinstance(bucket, dict):
        return {}
    return {freq: bucket}


def _sort_entries(
    entries: list[dict[str, Any]], newest_first: bool
) -> list[dict[str, Any]]:
    return sorted(
        entries,
        key=lambda row: str(row.get("period", "")),
        reverse=newest_first,
    )


def _pick_by_period(
    entries: list[dict[str, Any]],
    period: str,
    newest_first: bool,
) -> dict[str, Any] | None:
    sorted_entries = _sort_entries(entries, newest_first=newest_first)
    if period.lower() == "latest":
        return sorted_entries[0] if sorted_entries else None
    for row in sorted_entries:
        if str(row.get("period", "")) == period:
            return row
    return None


def _period_value(row: dict[str, Any] | None) -> Any:
    if not row:
        return None
    if "v" in row:
        return row.get("v")
    if "value" in row:
        return row.get("value")
    for key, value in row.items():
        if key != "period":
            return value
    return None


def _limit_entries(
    entries: list[dict[str, Any]], limit: int, newest_first: bool
) -> list[dict[str, Any]]:
    sorted_entries = _sort_entries(entries, newest_first=newest_first)
    if limit > 0:
        return sorted_entries[:limit]
    return sorted_entries


def _filter_keys(
    source: dict[str, Any], keys: list[str], max_items: int
) -> dict[str, Any]:
    if keys:
        return {key: source[key] for key in keys if key in source}
    items = list(source.items())
    if max_items > 0:
        items = items[:max_items]
    return dict(items)


def _cap_metric_names(
    metric_names: list[str], series_metric_limit: int
) -> tuple[list[str], bool]:
    if series_metric_limit < 0:
        raise ValueError("INVALID_LIMIT: series_metric_limit must be >= 0")
    if series_metric_limit == 0 or len(metric_names) <= series_metric_limit:
        return metric_names, False
    return metric_names[:series_metric_limit], True


def _normalize_report_sections(sections_csv: str) -> list[str]:
    aliases = {
        "income": "ic",
        "income_statement": "ic",
        "income-statement": "ic",
        "ic": "ic",
        "balance": "bs",
        "balance_sheet": "bs",
        "balance-sheet": "bs",
        "bs": "bs",
        "cash": "cf",
        "cashflow": "cf",
        "cash_flow": "cf",
        "cash-flow": "cf",
        "cf": "cf",
    }

    requested = _split_csv(sections_csv)
    if not requested:
        return []

    normalized: list[str] = []
    for value in requested:
        lowered = value.lower()
        if lowered == "all":
            return []
        mapped = aliases.get(lowered)
        if mapped and mapped not in normalized:
            normalized.append(mapped)

    if not normalized:
        raise ValueError(
            "INVALID_SECTION: sections_csv must include one of ic, bs, cf or aliases"
        )
    return normalized


def _reported_data(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _reported_period_id(row: dict[str, Any]) -> str:
    end_date = row.get("endDate")
    if isinstance(end_date, str) and end_date:
        return end_date[:10]

    year = row.get("year")
    quarter = row.get("quarter")
    if year is not None and quarter is not None:
        return f"{year}-Q{quarter}"
    if year is not None:
        return str(year)
    return ""


def _sort_reported_rows(
    rows: list[dict[str, Any]], newest_first: bool
) -> list[dict[str, Any]]:
    return sorted(rows, key=_reported_period_id, reverse=newest_first)


def _pick_reported_row(
    rows: list[dict[str, Any]],
    period: str,
    newest_first: bool,
) -> dict[str, Any] | None:
    sorted_rows = _sort_reported_rows(rows, newest_first=newest_first)
    if period.lower() == "latest":
        return sorted_rows[0] if sorted_rows else None

    normalized_period = period.strip()
    for row in sorted_rows:
        if _reported_period_id(row) == normalized_period:
            return row
    return None


def _extract_report_sections(
    row: dict[str, Any],
    selected_sections: list[str],
) -> dict[str, list[dict[str, Any]]]:
    report = row.get("report")
    if not isinstance(report, dict):
        return {}

    section_names = selected_sections or [
        name for name in ("ic", "bs", "cf") if isinstance(report.get(name), list)
    ]

    extracted: dict[str, list[dict[str, Any]]] = {}
    for section_name in section_names:
        values = report.get(section_name)
        if not isinstance(values, list):
            continue
        extracted[section_name] = [entry for entry in values if isinstance(entry, dict)]
    return extracted


def _concept_name(entry: dict[str, Any]) -> str:
    concept = entry.get("concept")
    if isinstance(concept, str) and concept:
        return concept
    label = entry.get("label")
    if isinstance(label, str) and label:
        return label
    return ""


def _concept_value(entry: dict[str, Any]) -> Any:
    if "value" in entry:
        return entry.get("value")
    if "v" in entry:
        return entry.get("v")
    return None


def _filter_report_entries(
    entries: list[dict[str, Any]],
    concepts: list[str],
    concept_limit: int,
) -> tuple[list[dict[str, Any]], bool, int, int]:
    if concept_limit < 0:
        raise ValueError("INVALID_LIMIT: concept_limit must be >= 0")

    requested_entries = entries
    if concepts:
        wanted = {item.lower() for item in concepts}
        requested_entries = [
            entry for entry in entries if _concept_name(entry).lower() in wanted
        ]

    # COMPACT ENTRY: strip non-essential keys
    compact_entries: list[dict[str, Any]] = []
    for entry in requested_entries:
        compact_entry: dict[str, Any] = {}
        for key in ("concept", "label", "unit", "value", "v"):
            if key in entry:
                compact_entry[key] = entry[key]
        compact_entries.append(compact_entry)

    if concept_limit == 0 or len(compact_entries) <= concept_limit:
        return compact_entries, False, len(requested_entries), len(compact_entries)

    limited = compact_entries[:concept_limit]
    return limited, True, len(requested_entries), len(limited)


def _format_as_csv(
    data: list[dict[str, Any]],
    keys: list[str],
) -> str:
    if not data:
        return ""
    lines = [",".join(keys)]
    for row in data:
        row_vals = []
        for k in keys:
            val = row.get(k, "")
            # Escape strings with commas or quotes
            val_str = str(val)
            if "," in val_str or '"' in val_str:
                escaped_inner = val_str.replace('"', '""')
                val_str = f'"{escaped_inner}"'
            row_vals.append(val_str)
        lines.append(",".join(row_vals))
    return "\n".join(lines)


def _summarize_reported_row(
    row: dict[str, Any],
    section_entries: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "period": _reported_period_id(row),
    }
    for key in ("endDate", "year", "quarter", "accessNumber", "symbol"):
        if key in row:
            summary[key] = row.get(key)
    summary["report"] = section_entries
    return summary


def _discover_reported_concepts(
    rows: list[dict[str, Any]],
    selected_sections: list[str],
    search: str,
) -> dict[str, Any]:
    search_key = search.strip().lower()

    concepts: dict[str, dict[str, Any]] = {}
    for row in rows:
        period_id = _reported_period_id(row)
        sections = _extract_report_sections(row, selected_sections)
        for section_name, entries in sections.items():
            for entry in entries:
                concept = _concept_name(entry)
                if not concept:
                    continue

                raw_label = entry.get("label")
                label = raw_label if isinstance(raw_label, str) else ""
                if (
                    search_key
                    and search_key not in concept.lower()
                    and search_key not in label.lower()
                ):
                    continue

                unit = entry.get("unit") if isinstance(entry.get("unit"), str) else ""
                concept_obj = concepts.setdefault(
                    concept,
                    {
                        "concept": concept,
                        "label": label,
                        "units": set(),
                        "sections": set(),
                        "periods": set(),
                    },
                )

                if label and not concept_obj["label"]:
                    concept_obj["label"] = label
                if unit:
                    concept_obj["units"].add(unit)
                concept_obj["sections"].add(section_name)
                if period_id:
                    concept_obj["periods"].add(period_id)

    normalized: list[dict[str, Any]] = []
    section_counts = {"ic": 0, "bs": 0, "cf": 0}
    for concept_name in sorted(concepts.keys()):
        item = concepts[concept_name]
        sections_sorted = sorted(item["sections"])
        for section_name in sections_sorted:
            if section_name in section_counts:
                section_counts[section_name] += 1

        normalized.append(
            {
                "concept": concept_name,
                "label": item["label"],
                "units": sorted(item["units"]),
                "sections": sections_sorted,
                "period_count": len(item["periods"]),
            }
        )

    return {
        "concepts": normalized,
        "section_counts": section_counts,
    }


@mcp.tool(name="list_news", description="List all latest market news")
def list_news(category: str = "general", count: int = 10):
    logger.info(f"Fetching {category} news")
    news = finnhub_client.general_news(category)
    return news[:count]


@mcp.tool(name="get_market_data", description="Get market data for a given stock")
def get_market_data(stock: str):
    logger.info(f"Fetching market data for {stock}")
    return finnhub_client.quote(stock)


@mcp.tool(
    name="get_basic_financials", description="Get basic financials for a given stock"
)
def get_basic_financials(stock: str, metric: str = "all"):
    logger.info(f"Fetching basic financials for {stock}")
    return finnhub_client.company_basic_financials(stock, metric)


@mcp.tool(
    name="list_basic_financial_metrics",
    description=(
        "List available basic-financial metric keys for a stock, grouped by "
        "metric fields and series fields (annual/quarterly)."
    ),
)
def list_basic_financial_metrics(stock: str, freq: str = "all", search: str = ""):
    """Discover metric keys before querying large datasets.

    Args:
        stock: Ticker symbol, e.g. "AAPL".
        freq: one of "all", "annual", "quarterly".
        search: Optional case-insensitive keyword filter.
    """

    normalized_freq = _normalize_freq(freq)
    payload = finnhub_client.company_basic_financials(stock, "all")

    metric_obj = payload.get("metric")
    metric_keys = sorted(metric_obj.keys()) if isinstance(metric_obj, dict) else []
    series_obj = _series_bucket(payload, normalized_freq)

    series_keys: dict[str, list[str]] = {}
    for bucket_name, bucket in series_obj.items():
        series_keys[bucket_name] = sorted(bucket.keys())

    search_term = search.strip().lower()
    if search_term:
        metric_keys = [key for key in metric_keys if search_term in key.lower()]
        for bucket_name in list(series_keys.keys()):
            series_keys[bucket_name] = [
                key for key in series_keys[bucket_name] if search_term in key.lower()
            ]

    return {
        "symbol": stock,
        "freq": normalized_freq,
        "search": search,
        "metric_keys": metric_keys,
        "series_metric_keys": series_keys,
        "counts": {
            "metric": len(metric_keys),
            "annual": len(series_keys.get("annual", [])),
            "quarterly": len(series_keys.get("quarterly", [])),
        },
        "meta": {
            "truncated": False,
            "requested_vs_returned_counts": {
                "metric": {"requested": len(metric_keys), "returned": len(metric_keys)},
                "annual": {
                    "requested": len(series_keys.get("annual", [])),
                    "returned": len(series_keys.get("annual", [])),
                },
                "quarterly": {
                    "requested": len(series_keys.get("quarterly", [])),
                    "returned": len(series_keys.get("quarterly", [])),
                },
            },
            "applied_limits": {
                "freq": normalized_freq,
                "search": search,
            },
            "fetched_at": int(time.time()),
            "source": "finnhub.company_basic_financials",
        },
    }


@mcp.tool(
    name="get_basic_financials_by_period",
    description=(
        "Get all series metrics for a single reporting period. "
        "Use period='latest' to fetch the newest report period."
    ),
)
def get_basic_financials_by_period(
    stock: str,
    period: str = "latest",
    freq: str = "annual",
    metrics_csv: str = "",
    series_metric_limit: int = 0,
    include_missing_reason: bool = False,
):
    """Return one reporting period across many metrics.

    Args:
        stock: Ticker symbol, e.g. "AAPL".
        period: e.g. "2024-12-31" or "latest".
        freq: one of "annual", "quarterly".
        metrics_csv: Optional comma-separated metric keys to limit response.
    """

    normalized_freq = _normalize_freq(freq)
    if normalized_freq == "all":
        raise ValueError("freq must be either 'annual' or 'quarterly'")

    if series_metric_limit < 0:
        raise ValueError("INVALID_LIMIT: series_metric_limit must be >= 0")

    payload = finnhub_client.company_basic_financials(stock, "all")
    buckets = _series_bucket(payload, normalized_freq)
    bucket = buckets.get(normalized_freq, {})

    selected_metrics = _split_csv(metrics_csv)
    requested_metric_names = selected_metrics or sorted(bucket.keys())
    metric_names, metric_names_truncated = _cap_metric_names(
        requested_metric_names,
        series_metric_limit,
    )

    report: dict[str, Any] = {}
    missing_metrics: list[str] = []
    missing_reason = {"metric_not_found": [], "period_not_found": []}
    resolved_period = period

    for metric_name in metric_names:
        rows = bucket.get(metric_name)
        if not isinstance(rows, list):
            missing_metrics.append(metric_name)
            missing_reason["metric_not_found"].append(metric_name)
            continue

        target_row = _pick_by_period(rows, period=period, newest_first=True)
        if target_row is None:
            missing_metrics.append(metric_name)
            missing_reason["period_not_found"].append(metric_name)
            continue

        if period.lower() == "latest":
            resolved_period = str(target_row.get("period", period))

        report[metric_name] = _period_value(target_row)

    response = {
        "symbol": stock,
        "freq": normalized_freq,
        "requested_period": period,
        "resolved_period": resolved_period,
        "report": report,
        "count": len(report),
        "missing_metrics": missing_metrics,
        "meta": {
            "truncated": metric_names_truncated,
            "requested_vs_returned_counts": {
                "series_metrics": {
                    "requested": len(requested_metric_names),
                    "returned": len(metric_names),
                },
                "resolved_values": {
                    "requested": len(metric_names),
                    "returned": len(report),
                },
            },
            "applied_limits": {
                "series_metric_limit": series_metric_limit,
                "freq": normalized_freq,
                "period": period,
            },
            "fetched_at": int(time.time()),
            "source": "finnhub.company_basic_financials",
        },
    }

    if include_missing_reason:
        response["missing_reason"] = missing_reason
    return response


@mcp.tool(
    name="get_basic_financial_metric_timeseries",
    description=(
        "Get one series metric across periods (single metric, multi-period). "
        "Designed to avoid huge payloads."
    ),
)
def get_basic_financial_metric_timeseries(
    stock: str,
    metric_key: str,
    freq: str = "annual",
    limit: int = 0,
    newest_first: bool = True,
):
    """Return time series for one metric key.

    Args:
        stock: Ticker symbol, e.g. "AAPL".
        metric_key: Series metric key, e.g. "eps".
        freq: one of "annual", "quarterly".
        limit: Max number of periods to return. 0 means all.
        newest_first: True for descending period order.
    """

    normalized_freq = _normalize_freq(freq)
    if normalized_freq == "all":
        raise ValueError("freq must be either 'annual' or 'quarterly'")
    if limit < 0:
        raise ValueError("limit must be >= 0")

    payload = finnhub_client.company_basic_financials(stock, "all")
    bucket = _series_bucket(payload, normalized_freq).get(normalized_freq, {})
    rows = bucket.get(metric_key)
    if not isinstance(rows, list):
        raise ValueError(
            f"metric_key '{metric_key}' not found in {normalized_freq} series"
        )

    limited_rows = _limit_entries(rows, limit=limit, newest_first=newest_first)
    values = [
        {
            "period": row.get("period"),
            "value": _period_value(row),
        }
        for row in limited_rows
    ]

    return {
        "symbol": stock,
        "freq": normalized_freq,
        "metric_key": metric_key,
        "count": len(values),
        "values": values,
        "meta": {
            "truncated": limit > 0 and len(rows) > len(limited_rows),
            "requested_vs_returned_counts": {
                "periods": {
                    "requested": len(rows),
                    "returned": len(limited_rows),
                }
            },
            "applied_limits": {
                "limit": limit,
                "freq": normalized_freq,
                "newest_first": newest_first,
            },
            "fetched_at": int(time.time()),
            "source": "finnhub.company_basic_financials",
        },
    }


@mcp.tool(
    name="get_basic_financials_compact",
    description=(
        "Compact basic financials response with optional field filters and caps "
        "to keep payload small for agents."
    ),
)
def get_basic_financials_compact(
    stock: str,
    metric_fields_csv: str = "",
    series_metrics_csv: str = "",
    freq: str = "all",
    period_limit: int = 4,
    metric_field_limit: int = 40,
    series_metric_limit: int = 0,
    newest_first: bool = True,
    output_format: str = "csv",
):
    """Get compact payload using filters and limits.

    Args:
        stock: Ticker symbol, e.g. "AAPL".
        metric_fields_csv: Optional comma-separated top-level metric fields.
        series_metrics_csv: Optional comma-separated series metric keys.
        freq: one of "all", "annual", "quarterly".
        period_limit: max periods per series metric, 0 means all.
        metric_field_limit: max top-level metric fields when no explicit filter.
        newest_first: order periods from newest to oldest when limiting.
    """

    normalized_freq = _normalize_freq(freq)
    if period_limit < 0:
        raise ValueError("INVALID_LIMIT: period_limit must be >= 0")
    if metric_field_limit < 0:
        raise ValueError("INVALID_LIMIT: metric_field_limit must be >= 0")
    if series_metric_limit < 0:
        raise ValueError("INVALID_LIMIT: series_metric_limit must be >= 0")

    payload = finnhub_client.company_basic_financials(stock, "all")
    metric_obj = payload.get("metric")
    metric_obj = metric_obj if isinstance(metric_obj, dict) else {}

    metric_fields = _split_csv(metric_fields_csv)
    series_metrics = _split_csv(series_metrics_csv)

    compact_metric = _filter_keys(
        metric_obj,
        keys=metric_fields,
        max_items=metric_field_limit,
    )

    metric_truncated = (
        len(metric_fields) == 0
        and metric_field_limit > 0
        and len(metric_obj) > len(compact_metric)
    )

    compact_series: dict[str, dict[str, list[dict[str, Any]]]] = {}
    series_metric_requested = 0
    series_metric_returned = 0
    period_truncated = False
    series_metric_truncated = False
    for bucket_name, bucket in _series_bucket(payload, normalized_freq).items():
        requested_names = series_metrics or sorted(bucket.keys())
        selected_names, bucket_metric_truncated = _cap_metric_names(
            requested_names,
            series_metric_limit,
        )
        series_metric_truncated = series_metric_truncated or bucket_metric_truncated
        series_metric_requested += len(requested_names)
        series_metric_returned += len(selected_names)

        compact_bucket: dict[str, list[dict[str, Any]]] = {}
        for metric_name in selected_names:
            rows = bucket.get(metric_name)
            if not isinstance(rows, list):
                continue
            limited_rows = _limit_entries(
                rows,
                limit=period_limit,
                newest_first=newest_first,
            )
            if period_limit > 0 and len(rows) > len(limited_rows):
                period_truncated = True
            compact_bucket[metric_name] = limited_rows
        compact_series[bucket_name] = compact_bucket

    if output_format == "csv":
        flat_data = []
        for bucket_name, bucket_data in compact_series.items():
            for metric_name, rows in bucket_data.items():
                for row in rows:
                    flat_data.append(
                        {
                            "period": row.get("period", ""),
                            "freq": bucket_name,
                            "metric": metric_name,
                            "value": row.get("v", row.get("value", "")),
                        }
                    )
        
        csv_string = _format_as_csv(
            flat_data, ["period", "freq", "metric", "value"]
        )
        
        metric_str = _format_as_csv(
            [{"metric": k, "value": v} for k, v in compact_metric.items()],
            ["metric", "value"]
        )
        
        return f"=== BASIC METRICS ===\n{metric_str}\n\n=== SERIES DATA ===\n{csv_string}"

    return {
        "symbol": stock,
        "freq": normalized_freq,
        "metric": compact_metric,
        "series": compact_series,
        "meta": {
            "metric_fields_filter": metric_fields,
            "series_metrics_filter": series_metrics,
            "period_limit": period_limit,
            "metric_field_limit": metric_field_limit,
            "series_metric_limit": series_metric_limit,
            "newest_first": newest_first,
            "truncated": metric_truncated
            or series_metric_truncated
            or period_truncated,
            "requested_vs_returned_counts": {
                "metric_fields": {
                    "requested": len(metric_fields)
                    if metric_fields
                    else len(metric_obj),
                    "returned": len(compact_metric),
                },
                "series_metrics": {
                    "requested": series_metric_requested,
                    "returned": series_metric_returned,
                },
            },
            "applied_limits": {
                "period_limit": period_limit,
                "metric_field_limit": metric_field_limit,
                "series_metric_limit": series_metric_limit,
                "freq": normalized_freq,
            },
            "fetched_at": int(time.time()),
            "source": "finnhub.company_basic_financials",
        },
    }


@mcp.tool(
    name="get_recommendation_trends",
    description="Get recommendation trends for a given stock",
)
def get_recommendation_trends(stock: str):
    logger.info(f"Fetching recommendation trends for {stock}")
    return finnhub_client.recommendation_trends(stock)


@mcp.tool(
    name="get_daily_volume",
    description=(
        "Get daily volume candles for a stock over the past N trading days. "
        "Returns Finnhub candle payload including v (volume) and t (timestamps)."
    ),
)
def get_daily_volume(stock: str, days: int = 10):
    """Fetch daily candle data and return the Finnhub payload.

    Finnhub's /stock/candle endpoint returns arrays:
    - t: UNIX timestamps (seconds)
    - v: volume
    - o/h/l/c: OHLC
    - s: status

    Note: weekends/holidays mean fewer than `days` data points may be returned.
    """

    if not isinstance(days, int):
        raise ValueError("days must be an int")
    if days <= 0:
        raise ValueError("days must be > 0")
    if days > 365:
        raise ValueError("days too large (max 365)")

    # Fetch a wider window to cover non-trading days.
    now = int(time.time())
    _from = now - int(days * 3 * 24 * 60 * 60)
    logger.info(f"Fetching daily volume candles for {stock} over ~{days} days")
    return finnhub_client.stock_candles(stock, "D", _from, now)


@mcp.tool(
    name="get_financials_reported",
    description=(
        "Get standardized reported financial statements for a stock from "
        "Finnhub's /stock/financials-reported endpoint."
    ),
)
def get_financials_reported(stock: str, freq: str = "annual"):
    """Fetch reported financial statements from Finnhub.

    Args:
        stock: Ticker symbol, e.g. "AAPL".
        freq: Reporting frequency, either "annual" or "quarterly".
    """

    normalized_freq = freq.strip().lower()
    if normalized_freq not in {"annual", "quarterly"}:
        raise ValueError("freq must be either 'annual' or 'quarterly'")

    logger.info(
        f"Fetching financials reported for {stock} with frequency {normalized_freq}"
    )
    return finnhub_client.financials_reported(symbol=stock, freq=normalized_freq)


@mcp.tool(
    name="list_financials_reported_concepts",
    description=(
        "Discover available financials_reported concepts to support discover->query flow."
    ),
)
def list_financials_reported_concepts(
    stock: str,
    freq: str = "annual",
    sections_csv: str = "",
    search: str = "",
    period_limit: int = 0,
    concept_limit: int = 0,
):
    normalized_freq = freq.strip().lower()
    if normalized_freq not in {"annual", "quarterly"}:
        raise ValueError("INVALID_FREQ: freq must be either 'annual' or 'quarterly'")
    if period_limit < 0:
        raise ValueError("INVALID_LIMIT: period_limit must be >= 0")
    if concept_limit < 0:
        raise ValueError("INVALID_LIMIT: concept_limit must be >= 0")

    selected_sections = _normalize_report_sections(sections_csv)
    payload = finnhub_client.financials_reported(symbol=stock, freq=normalized_freq)
    rows = _sort_reported_rows(_reported_data(payload), newest_first=True)

    requested_periods = len(rows)
    if period_limit > 0:
        rows = rows[:period_limit]

    discovered = _discover_reported_concepts(rows, selected_sections, search)
    concepts = discovered["concepts"]
    section_counts = discovered["section_counts"]

    requested_concepts = len(concepts)
    if concept_limit > 0:
        concepts = concepts[:concept_limit]

    return {
        "symbol": stock,
        "freq": normalized_freq,
        "search": search,
        "sections": selected_sections or ["ic", "bs", "cf"],
        "concepts": concepts,
        "count": len(concepts),
        "section_counts": section_counts,
        "meta": {
            "truncated": (period_limit > 0 and requested_periods > len(rows))
            or (concept_limit > 0 and requested_concepts > len(concepts)),
            "requested_vs_returned_counts": {
                "periods": {"requested": requested_periods, "returned": len(rows)},
                "concepts": {
                    "requested": requested_concepts,
                    "returned": len(concepts),
                },
            },
            "applied_limits": {
                "period_limit": period_limit,
                "concept_limit": concept_limit,
                "sections_csv": sections_csv,
                "search": search,
            },
            "fetched_at": int(time.time()),
            "source": "finnhub.financials_reported",
        },
    }


@mcp.tool(
    name="get_financials_reported_by_period",
    description=(
        "Get reported financial statements for one period. "
        "Use period='latest' for newest report."
    ),
)
def get_financials_reported_by_period(
    stock: str,
    period: str = "latest",
    freq: str = "annual",
    sections_csv: str = "",
    concepts_csv: str = "",
    concept_limit: int = 0,
):
    normalized_freq = freq.strip().lower()
    if normalized_freq not in {"annual", "quarterly"}:
        raise ValueError("INVALID_FREQ: freq must be either 'annual' or 'quarterly'")

    selected_sections = _normalize_report_sections(sections_csv)
    selected_concepts = _split_csv(concepts_csv)

    payload = finnhub_client.financials_reported(symbol=stock, freq=normalized_freq)
    rows = _reported_data(payload)
    target = _pick_reported_row(rows, period=period, newest_first=True)
    if not target:
        raise ValueError(f"PERIOD_NOT_FOUND: period '{period}' not found")

    report_sections = _extract_report_sections(target, selected_sections)
    compact_sections: dict[str, list[dict[str, Any]]] = {}

    any_truncated = False
    requested_concepts = 0
    returned_concepts = 0
    for section_name, entries in report_sections.items():
        filtered, truncated, requested_count, returned_count = _filter_report_entries(
            entries,
            concepts=selected_concepts,
            concept_limit=concept_limit,
        )
        any_truncated = any_truncated or truncated
        requested_concepts += requested_count
        returned_concepts += returned_count
        compact_sections[section_name] = filtered

    report = _summarize_reported_row(target, compact_sections)
    return {
        "symbol": stock,
        "freq": normalized_freq,
        "requested_period": period,
        "resolved_period": _reported_period_id(target),
        "report": report,
        "meta": {
            "truncated": any_truncated,
            "requested_vs_returned_counts": {
                "concepts": {
                    "requested": requested_concepts,
                    "returned": returned_concepts,
                }
            },
            "applied_limits": {
                "concept_limit": concept_limit,
                "sections_csv": sections_csv,
                "concepts_csv": concepts_csv,
            },
            "fetched_at": int(time.time()),
            "source": "finnhub.financials_reported",
        },
    }


@mcp.tool(
    name="get_financials_reported_concept_timeseries",
    description=(
        "Get one reported concept across periods. "
        "Useful for small payload metric trend queries."
    ),
)
def get_financials_reported_concept_timeseries(
    stock: str,
    concept: str,
    freq: str = "annual",
    section: str = "all",
    limit: int = 0,
    newest_first: bool = True,
):
    normalized_freq = freq.strip().lower()
    if normalized_freq not in {"annual", "quarterly"}:
        raise ValueError("INVALID_FREQ: freq must be either 'annual' or 'quarterly'")
    if limit < 0:
        raise ValueError("INVALID_LIMIT: limit must be >= 0")

    selected_sections = _normalize_report_sections(section)
    payload = finnhub_client.financials_reported(symbol=stock, freq=normalized_freq)
    rows = _sort_reported_rows(_reported_data(payload), newest_first=newest_first)

    concept_key = concept.strip().lower()
    values: list[dict[str, Any]] = []
    for row in rows:
        sections = _extract_report_sections(row, selected_sections)
        for section_name, entries in sections.items():
            matched_entry = next(
                (
                    entry
                    for entry in entries
                    if _concept_name(entry).lower() == concept_key
                ),
                None,
            )
            if matched_entry is None:
                continue

            values.append(
                {
                    "period": _reported_period_id(row),
                    "endDate": row.get("endDate"),
                    "year": row.get("year"),
                    "quarter": row.get("quarter"),
                    "section": section_name,
                    "concept": _concept_name(matched_entry),
                    "label": matched_entry.get("label"),
                    "unit": matched_entry.get("unit"),
                    "value": _concept_value(matched_entry),
                }
            )
            break

        if limit > 0 and len(values) >= limit:
            break

    return {
        "symbol": stock,
        "freq": normalized_freq,
        "concept": concept,
        "count": len(values),
        "values": values,
        "meta": {
            "truncated": limit > 0 and len(values) >= limit,
            "requested_vs_returned_counts": {
                "periods": {
                    "requested": len(rows),
                    "returned": len(values),
                }
            },
            "applied_limits": {
                "limit": limit,
                "section": section,
                "newest_first": newest_first,
            },
            "fetched_at": int(time.time()),
            "source": "finnhub.financials_reported",
        },
    }


@mcp.tool(
    name="get_financials_reported_compact",
    description=(
        "Get compact financials_reported payload with period/concept caps.\n"
        "Use output_format='csv' for extremely dense, agent-friendly return format "
        "to prevent payload truncation."
    ),
)
def get_financials_reported_compact(
    stock: str,
    freq: str = "annual",
    sections_csv: str = "",
    concepts_csv: str = "",
    period_limit: int = 3,
    concept_limit: int = 40,
    newest_first: bool = True,
    output_format: str = "csv",
):
    normalized_freq = freq.strip().lower()
    if normalized_freq not in {"annual", "quarterly"}:
        raise ValueError("INVALID_FREQ: freq must be either 'annual' or 'quarterly'")
    if period_limit < 0:
        raise ValueError("INVALID_LIMIT: period_limit must be >= 0")
    if concept_limit < 0:
        raise ValueError("INVALID_LIMIT: concept_limit must be >= 0")

    selected_sections = _normalize_report_sections(sections_csv)
    selected_concepts = _split_csv(concepts_csv)

    payload = finnhub_client.financials_reported(symbol=stock, freq=normalized_freq)
    rows = _sort_reported_rows(_reported_data(payload), newest_first=newest_first)
    requested_periods = len(rows)

    if period_limit > 0:
        rows = rows[:period_limit]

    compact_reports: list[dict[str, Any]] = []
    concept_requested = 0
    concept_returned = 0
    concept_truncated = False
    for row in rows:
        sections = _extract_report_sections(row, selected_sections)
        compact_sections: dict[str, list[dict[str, Any]]] = {}
        for section_name, entries in sections.items():
            filtered, truncated, requested_count, returned_count = (
                _filter_report_entries(
                    entries,
                    concepts=selected_concepts,
                    concept_limit=concept_limit,
                )
            )
            concept_requested += requested_count
            concept_returned += returned_count
            concept_truncated = concept_truncated or truncated
            compact_sections[section_name] = filtered
        compact_reports.append(_summarize_reported_row(row, compact_sections))

    if output_format == "csv":
        flat_data = []
        for report_item in compact_reports:
            period = report_item.get("period", "")
            for sec, entries in report_item.get("report", {}).items():
                for entry in entries:
                    flat_data.append(
                        {
                            "period": period,
                            "section": sec,
                            "concept": entry.get("concept", ""),
                            "label": entry.get("label", ""),
                            "value": entry.get("value", entry.get("v", "")),
                            "unit": entry.get("unit", ""),
                        }
                    )
        
        csv_string = _format_as_csv(
            flat_data, ["period", "section", "concept", "label", "value", "unit"]
        )
        return csv_string

    return {
        "symbol": stock,
        "freq": normalized_freq,
        "reports": compact_reports,
        "count": len(compact_reports),
        "meta": {
            "truncated": concept_truncated
            or (period_limit > 0 and requested_periods > len(rows)),
            "requested_vs_returned_counts": {
                "periods": {"requested": requested_periods, "returned": len(rows)},
                "concepts": {
                    "requested": concept_requested,
                    "returned": concept_returned,
                },
            },
            "applied_limits": {
                "period_limit": period_limit,
                "concept_limit": concept_limit,
                "sections_csv": sections_csv,
                "concepts_csv": concepts_csv,
                "newest_first": newest_first,
            },
            "fetched_at": int(time.time()),
            "source": "finnhub.financials_reported",
        },
    }


# 改动 2：加上启动入口
if __name__ == "__main__":
    mcp.run()
