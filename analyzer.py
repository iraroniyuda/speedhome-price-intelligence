from typing import Dict, List

import numpy as np
import pandas as pd


EXPECTED_COLUMNS = [
    "title",
    "property_area",
    "source_area",
    "bedrooms",
    "bedroom_type",
    "room_type_label",
    "bathrooms",
    "bathroom_type_label",
    "carparks",
    "monthly_price_rm",
    "daily_price_rm",
    "explicit_yearly_price_rm",
    "estimated_yearly_price_rm",
    "size_sqft",
    "furnishing",
    "detected_rental_type",
    "listing_url",
    "raw_text",
]


def calculate_sample_size_confidence(sample_size: int):
    sample_size = int(sample_size or 0)

    if sample_size <= 0:
        return (
            "No sample",
            "No listings are available, so market confidence cannot be calculated.",
        )

    if sample_size < 5:
        return (
            "Low",
            "Fewer than 5 listings. Treat the result as a rough directional signal only.",
        )

    if sample_size < 15:
        return (
            "Medium",
            "5 to 14 listings. Useful for directional comparison, but still sensitive to outliers.",
        )

    return (
        "High",
        "15 or more listings. Stronger sample for this scraped public dataset.",
    )


def create_sample_size_confidence(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Scope",
        "Segment",
        "Listing Count",
        "Confidence",
        "Interpretation",
    ]

    rows = []

    total_count = len(df) if df is not None else 0
    confidence, note = calculate_sample_size_confidence(total_count)

    rows.append(
        {
            "Scope": "Overall",
            "Segment": "All listings",
            "Listing Count": total_count,
            "Confidence": confidence,
            "Interpretation": note,
        }
    )

    if df is None or df.empty or "bedroom_type" not in df.columns:
        return pd.DataFrame(rows, columns=columns)

    for segment, group in df.groupby("bedroom_type", dropna=False):
        if pd.isna(segment) or segment in ["", None]:
            segment_label = "Unknown"
        else:
            segment_label = str(segment)

        segment_count = len(group)
        segment_confidence, segment_note = calculate_sample_size_confidence(segment_count)

        rows.append(
            {
                "Scope": "Unit Segment",
                "Segment": segment_label,
                "Listing Count": segment_count,
                "Confidence": segment_confidence,
                "Interpretation": segment_note,
            }
        )

    result = pd.DataFrame(rows, columns=columns)

    scope_order = {
        "Overall": 0,
        "Unit Segment": 1,
    }

    result["_scope_order"] = result["Scope"].map(scope_order).fillna(99)
    result = result.sort_values(
        by=["_scope_order", "Listing Count", "Segment"],
        ascending=[True, False, True],
    ).drop(columns=["_scope_order"])

    return result.reset_index(drop=True)


def build_dataframe(listings: List[Dict]) -> pd.DataFrame:
    df = pd.DataFrame(listings)

    for column in EXPECTED_COLUMNS:
        if column not in df.columns:
            df[column] = None

    if df.empty:
        return df[EXPECTED_COLUMNS]

    numeric_columns = [
        "bedrooms",
        "bathrooms",
        "carparks",
        "monthly_price_rm",
        "daily_price_rm",
        "explicit_yearly_price_rm",
        "estimated_yearly_price_rm",
        "size_sqft",
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["estimated_yearly_price_rm"] = np.where(
        df["monthly_price_rm"].notna(),
        df["monthly_price_rm"] * 12,
        df["estimated_yearly_price_rm"],
    )

    df["price_per_sqft_rm"] = np.where(
        df["size_sqft"].notna() & (df["size_sqft"] > 0) & df["monthly_price_rm"].notna(),
        df["monthly_price_rm"] / df["size_sqft"],
        np.nan,
    )

    df["daily_price_status"] = np.where(
        df["daily_price_rm"].notna(),
        "Available",
        "Not detected on public page",
    )

    df["monthly_price_status"] = np.where(
        df["monthly_price_rm"].notna(),
        "Available",
        "Not detected",
    )

    df["yearly_price_status"] = np.where(
        df["explicit_yearly_price_rm"].notna(),
        "Available - explicit",
        np.where(
            df["monthly_price_rm"].notna(),
            "Estimated from monthly x 12",
            "Not available",
        ),
    )

    df["direct_listing_url_valid"] = df["listing_url"].apply(is_direct_speedhome_listing_url)

    df["data_completeness_score"] = df.apply(calculate_data_completeness_score, axis=1)

    return df



def is_direct_speedhome_listing_url(value) -> bool:
    text = str(value or "").strip().lower()
    return "speedhome.com" in text and "/details/" in text



def _safe_int(value):
    try:
        if value is None or pd.isna(value):
            return None
    except Exception:
        if value is None:
            return None

    try:
        return int(value)
    except Exception:
        return None



def summarize_reported_vs_rendered(metadata: Dict, collected_count: int) -> Dict:
    reported_total = _safe_int((metadata or {}).get("source_reported_total_count"))
    collected_count = int(collected_count or 0)

    if not reported_total:
        return {
            "reported_total": None,
            "collected_count": collected_count,
            "coverage_ratio": None,
            "raw_ratio": None,
            "status": "Reported total unavailable",
            "note": "SPEEDHOME did not expose a clear reported total on this page.",
        }

    raw_ratio = round((collected_count / reported_total) * 100, 2)
    metadata_status = str((metadata or {}).get("reported_vs_rendered_status") or "").strip()

    if collected_count < reported_total:
        return {
            "reported_total": reported_total,
            "collected_count": collected_count,
            "coverage_ratio": raw_ratio,
            "raw_ratio": raw_ratio,
            "status": "Partial rendered sample",
            "note": "Collected cards are fewer than the SPEEDHOME-reported target count; treat the analysis as an observed public-page sample.",
        }

    if collected_count == reported_total:
        return {
            "reported_total": reported_total,
            "collected_count": collected_count,
            "coverage_ratio": 100.0,
            "raw_ratio": 100.0,
            "status": "Matches reported total",
            "note": "Collected cards match the SPEEDHOME-reported target count.",
        }

    return {
        "reported_total": reported_total,
        "collected_count": collected_count,
        "coverage_ratio": None,
        "raw_ratio": raw_ratio,
        "status": "Rendered cards exceed reported target count" if not metadata_status else metadata_status.replace("_", " ").title(),
        "note": "SPEEDHOME rendered more direct listing cards than the target-count headline. This can happen when nearby or broader source-page cards are displayed. Do not interpret this as >100% coverage.",
    }



def calculate_data_completeness_score(row: pd.Series) -> int:
    direct_listing_url_valid = is_direct_speedhome_listing_url(row.get("listing_url"))

    checks = [
        row.get("title"),
        row.get("property_area"),
        row.get("bedroom_type"),
        row.get("monthly_price_rm"),
        row.get("estimated_yearly_price_rm"),
        row.get("size_sqft"),
        row.get("bedrooms"),
        row.get("bathrooms"),
        row.get("carparks"),
    ]

    score = 0

    for value in checks:
        if pd.notna(value) and value not in ["", None, "Unknown"]:
            score += 1

    # The assessment asks for a clickable direct listing link.
    # A generic /rent/<area> page should not count as complete listing data.
    if direct_listing_url_valid:
        score += 1

    furnishing = row.get("furnishing")

    if pd.notna(furnishing) and furnishing not in ["", "Not specified", "Unknown"]:
        score += 1

    return int(round((score / 11) * 100))

def _mode_value(series: pd.Series):
    clean = series.dropna()

    if clean.empty:
        return None

    mode = clean.mode()

    if mode.empty:
        return None

    return int(mode.iloc[0])


def _fair_price(series: pd.Series):
    clean = series.dropna()

    if clean.empty:
        return None

    if len(clean) < 4:
        return int(round(clean.median()))

    q1 = clean.quantile(0.25)
    q3 = clean.quantile(0.75)
    iqr = q3 - q1

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    trimmed = clean[(clean >= lower) & (clean <= upper)]

    if trimmed.empty:
        trimmed = clean

    fair = (trimmed.median() + trimmed.mean()) / 2
    return int(round(fair))


def create_price_summary(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "bedroom_type",
        "unit_count",
        "sample_size_confidence",
        "confidence_note",
        "average_price_rm",
        "median_price_rm",
        "mode_price_rm",
        "fair_price_rm",
        "average_size_sqft",
        "average_price_per_sqft_rm",
        "average_data_completeness_score",
    ]

    if df.empty:
        return pd.DataFrame(columns=columns)

    summary = (
        df.groupby("bedroom_type", dropna=False)
        .agg(
            unit_count=("title", "size"),
            average_price_rm=("monthly_price_rm", "mean"),
            median_price_rm=("monthly_price_rm", "median"),
            mode_price_rm=("monthly_price_rm", _mode_value),
            fair_price_rm=("monthly_price_rm", _fair_price),
            average_size_sqft=("size_sqft", "mean"),
            average_price_per_sqft_rm=("price_per_sqft_rm", "mean"),
            average_data_completeness_score=("data_completeness_score", "mean"),
        )
        .reset_index()
    )

    confidence_values = summary["unit_count"].apply(calculate_sample_size_confidence)
    summary["sample_size_confidence"] = confidence_values.apply(lambda item: item[0])
    summary["confidence_note"] = confidence_values.apply(lambda item: item[1])

    round_columns = [
        "average_price_rm",
        "median_price_rm",
        "average_size_sqft",
        "average_price_per_sqft_rm",
        "average_data_completeness_score",
    ]

    for column in round_columns:
        summary[column] = summary[column].round(2)

    summary = summary.sort_values(
        by=["unit_count", "fair_price_rm"],
        ascending=[False, True],
        na_position="last",
    )

    return summary[columns]


def create_rental_type_coverage(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            [
                {
                    "Rental Type": "Daily",
                    "Status": "Not available",
                    "Source": "No listings found",
                    "Notes": "No listing data available for this search.",
                },
                {
                    "Rental Type": "Monthly",
                    "Status": "Not available",
                    "Source": "No listings found",
                    "Notes": "No listing data available for this search.",
                },
                {
                    "Rental Type": "Yearly",
                    "Status": "Not available",
                    "Source": "No listings found",
                    "Notes": "No listing data available for this search.",
                },
            ]
        )

    daily_available = df["daily_price_rm"].notna().any()
    monthly_available = df["monthly_price_rm"].notna().any()
    explicit_yearly_available = df["explicit_yearly_price_rm"].notna().any()
    estimated_yearly_available = df["estimated_yearly_price_rm"].notna().any()

    rows = []

    rows.append(
        {
            "Rental Type": "Daily",
            "Status": "Available" if daily_available else "Not detected",
            "Source": "SPEEDHOME public listing" if daily_available else "Public page checked",
            "Notes": (
                "Daily rental price was detected in the scraped page."
                if daily_available
                else "No daily rental price was detected in the public listing cards."
            ),
        }
    )

    rows.append(
        {
            "Rental Type": "Monthly",
            "Status": "Available" if monthly_available else "Not detected",
            "Source": "SPEEDHOME public listing" if monthly_available else "Public page checked",
            "Notes": (
                "Monthly rent is the primary price shown by SPEEDHOME listing cards."
                if monthly_available
                else "No monthly rental price was detected."
            ),
        }
    )

    if explicit_yearly_available:
        yearly_status = "Available"
        yearly_source = "SPEEDHOME public listing"
        yearly_notes = "Explicit yearly rental price was detected from the public page."
    elif estimated_yearly_available:
        yearly_status = "Estimated"
        yearly_source = "Monthly rent x 12"
        yearly_notes = "Explicit yearly rental price was not detected, so yearly rent is estimated from monthly rent."
    else:
        yearly_status = "Not available"
        yearly_source = "Public page checked"
        yearly_notes = "No yearly rental price could be detected or estimated."

    rows.append(
        {
            "Rental Type": "Yearly",
            "Status": yearly_status,
            "Source": yearly_source,
            "Notes": yearly_notes,
        }
    )

    return pd.DataFrame(rows)


def create_data_quality_report(
    raw_df: pd.DataFrame,
    filtered_df: pd.DataFrame,
    metadata: Dict,
    strict_area_filter: bool,
    target_area: str,
) -> pd.DataFrame:
    raw_count = len(raw_df)
    filtered_count = len(filtered_df)

    missing_size = int(filtered_df["size_sqft"].isna().sum()) if not filtered_df.empty else 0

    if not filtered_df.empty:
        furnishing_series = filtered_df["furnishing"].fillna("Not specified")
        missing_furnishing = int(
            furnishing_series.isin(["", "Not specified", "Unknown", "Not detected on result card", None]).sum()
        )
        detected_furnishing = int(len(filtered_df) - missing_furnishing)
        furnishing_detection_rate = round((detected_furnishing / len(filtered_df)) * 100, 2) if len(filtered_df) else 0
    else:
        missing_furnishing = 0
        detected_furnishing = 0
        furnishing_detection_rate = 0

    missing_bedrooms = int(filtered_df["bedrooms"].isna().sum()) if not filtered_df.empty else 0
    missing_links = int(filtered_df["listing_url"].isna().sum()) if not filtered_df.empty else 0
    invalid_direct_links = (
        int((~filtered_df["listing_url"].apply(is_direct_speedhome_listing_url)).sum())
        if not filtered_df.empty and "listing_url" in filtered_df.columns
        else 0
    )

    average_completeness = (
        round(float(filtered_df["data_completeness_score"].mean()), 2)
        if not filtered_df.empty
        else 0
    )

    confidence, confidence_note = calculate_sample_size_confidence(filtered_count)

    normalized_url = (
        metadata.get("normalized_url")
        or metadata.get("source_url")
        or metadata.get("requests_final_url")
        or metadata.get("playwright_final_url")
        or "-"
    )

    notes = metadata.get("notes") or metadata.get("errors") or []

    if isinstance(notes, str):
        notes = [notes]

    reconciliation = summarize_reported_vs_rendered(metadata, filtered_count)

    if not filtered_df.empty and "target_area_match" in filtered_df.columns:
        strong_target_count = int((filtered_df["target_area_match"].fillna("") == "Strong").sum())
        source_page_count = int((filtered_df["target_area_match"].fillna("") == "Source Page").sum())
    else:
        strong_target_count = 0
        source_page_count = filtered_count

    coverage_display = (
        f"{reconciliation['coverage_ratio']}%"
        if reconciliation.get("coverage_ratio") is not None
        else "N/A - rendered cards exceed reported target" if reconciliation.get("raw_ratio") and reconciliation["raw_ratio"] > 100
        else "-"
    )

    rows = [
        {"Metric": "Input", "Value": metadata.get("input", "-")},
        {"Metric": "Source URL", "Value": normalized_url},
        {"Metric": "Source Area", "Value": metadata.get("source_area", target_area or "-")},
        {"Metric": "Fetch Method", "Value": metadata.get("fetch_method", "-")},
        {"Metric": "Scrape Mode", "Value": "Public rendered cards + public pagination" if metadata.get("crawl_mode") in ["deeper", "full", "extended"] else "Public rendered cards"},
        {"Metric": "Cache Used", "Value": "Yes" if metadata.get("cache_used") else "No"},
        {"Metric": "Scraped At", "Value": metadata.get("scraped_at", "-")},
        {"Metric": "Scrape Started At", "Value": metadata.get("scrape_started_at", "-")},
        {"Metric": "Scrape Finished At", "Value": metadata.get("scrape_finished_at", "-")},
        {"Metric": "Live Scrape Duration", "Value": metadata.get("scrape_duration_label", "-")},
        {"Metric": "Live Scrape Duration Seconds", "Value": metadata.get("scrape_duration_seconds", "-")},
        {"Metric": "Current Run Duration", "Value": metadata.get("current_run_duration_label", metadata.get("scrape_duration_label", "-"))},
        {"Metric": "Current Run Duration Seconds", "Value": metadata.get("current_run_duration_seconds", metadata.get("scrape_duration_seconds", "-"))},
        {"Metric": "Cache Lookup Duration", "Value": metadata.get("cache_lookup_duration_label", "-")},
        {"Metric": "Detail Enrichment Enabled", "Value": "Yes" if metadata.get("detail_enrichment_enabled") else "No"},
        {"Metric": "Detail Enrichment Attempted", "Value": metadata.get("detail_enrichment_attempted", "-")},
        {"Metric": "Detail Enrichment Successful", "Value": metadata.get("detail_enrichment_successful", "-")},
        {"Metric": "Detail Enrichment Failed", "Value": metadata.get("detail_enrichment_failed", "-")},
        {"Metric": "Detail Enrichment Duration", "Value": metadata.get("detail_enrichment_duration_label", "-")},
        {"Metric": "SPEEDHOME Reported Target Count", "Value": reconciliation.get("reported_total") or "-"},
        {"Metric": "Rendered Direct Listing Cards", "Value": filtered_count},
        {"Metric": "Reported vs Rendered Status", "Value": reconciliation.get("status", "-")},
        {"Metric": "Raw Rendered / Reported Ratio", "Value": f"{reconciliation.get('raw_ratio')}%" if reconciliation.get("raw_ratio") is not None else "-"},
        {"Metric": "Scrape Coverage", "Value": coverage_display},
        {"Metric": "Coverage Interpretation", "Value": reconciliation.get("note", "-")},
        {"Metric": "Strong Target Evidence Count", "Value": strong_target_count},
        {"Metric": "Source Page Card Count", "Value": source_page_count},
        {"Metric": "Playwright Scroll Rounds", "Value": metadata.get("playwright_scroll_rounds_completed", "-")},
        {"Metric": "Playwright Detail Links Seen", "Value": metadata.get("playwright_best_detail_link_count_seen", "-")},
        {"Metric": "Playwright Pages Fetched", "Value": metadata.get("playwright_paginated_pages_fetched", "-")},
        {"Metric": "Playwright Page Detail Counts", "Value": metadata.get("playwright_page_detail_counts", "-")},
        {"Metric": "Playwright Page URLs", "Value": metadata.get("playwright_page_urls_fetched", "-")},
        {"Metric": "Captured JSON Payloads", "Value": metadata.get("playwright_captured_json_payload_count", "-")},
        {"Metric": "Candidate Network URLs", "Value": metadata.get("network_candidate_response_url_count", len(metadata.get("playwright_candidate_response_urls") or []))},
        {"Metric": "Network Debug Records", "Value": metadata.get("network_debug_record_count", "-")},
        {"Metric": "Network Debug File", "Value": metadata.get("network_debug_file", "-")},
        {"Metric": "Top Likely Network URLs", "Value": metadata.get("network_top_likely_listing_urls", metadata.get("playwright_top_debug_json_urls", []))},
        {"Metric": "Robots Allowed", "Value": metadata.get("robots_allowed", "-")},
        {"Metric": "Robots URL", "Value": metadata.get("robots_url", "-")},
        {"Metric": "Requests Status Code", "Value": metadata.get("requests_status_code", "-")},
        {"Metric": "Requests Final URL", "Value": metadata.get("requests_final_url", "-")},
        {"Metric": "Playwright Final URL", "Value": metadata.get("playwright_final_url", "-")},
        {"Metric": "HTML Length", "Value": metadata.get("html_length", 0)},
        {"Metric": "Raw Listings Detected", "Value": raw_count},
        {"Metric": "Listings After Current Filters", "Value": filtered_count},
        {"Metric": "Sample Size Confidence", "Value": confidence},
        {"Metric": "Sample Size Confidence Note", "Value": confidence_note},
        {"Metric": "Target Area Filter", "Value": "Active" if strict_area_filter else "Inactive"},
        {"Metric": "Target Area", "Value": target_area or "-"},
        {"Metric": "Missing Size Count", "Value": missing_size},
        {"Metric": "Detected Furnishing Count", "Value": detected_furnishing},
        {"Metric": "Missing Furnishing Count", "Value": missing_furnishing},
        {"Metric": "Furnishing Detection Rate", "Value": f"{furnishing_detection_rate}%"},
        {"Metric": "Furnishing Detection Note", "Value": "Furnishing status is counted when Fully Furnished, Partially Furnished, or Unfurnished text is detected in the public rendered listing card. If detail enrichment is enabled, accepted listing detail pages are opened to fill missing furnishing values."},
        {"Metric": "Missing Bedrooms Count", "Value": missing_bedrooms},
        {"Metric": "Missing Link Count", "Value": missing_links},
        {"Metric": "Invalid Direct Listing Link Count", "Value": invalid_direct_links},
        {"Metric": "Average Data Completeness Score", "Value": f"{average_completeness}%"},
    ]

    if notes:
        rows.append({"Metric": "Scraper Notes", "Value": " | ".join([str(note) for note in notes])})
    else:
        rows.append({"Metric": "Scraper Notes", "Value": "No scraper note recorded."})

    report = pd.DataFrame(rows)

    def normalize_report_value(value):
        if value is None:
            return "-"

        if isinstance(value, (list, tuple, set)):
            return " | ".join([str(item) for item in value]) if value else "-"

        try:
            if pd.isna(value):
                return "-"
        except Exception:
            pass

        return str(value)

    if "Value" in report.columns:
        report["Value"] = report["Value"].apply(normalize_report_value)

    return report


def generate_insights(df: pd.DataFrame, summary: pd.DataFrame) -> List[str]:
    insights = []

    if df.empty:
        return ["No listings found for this input."]

    monthly_prices = df["monthly_price_rm"].dropna()

    if monthly_prices.empty:
        insights.append("No monthly rent data was detected in the selected listings.")
        return insights

    total_units = len(df)
    median_price = int(round(monthly_prices.median()))
    average_price = int(round(monthly_prices.mean()))
    confidence, confidence_note = calculate_sample_size_confidence(total_units)

    insights.append(f"Found {total_units} rental listings in the selected dataset.")
    insights.append(f"Sample size confidence is {confidence}. {confidence_note}")
    insights.append(f"Median monthly rent is RM {median_price:,}, while average rent is RM {average_price:,}.")

    if not summary.empty:
        top_segment = summary.sort_values("unit_count", ascending=False).iloc[0]
        insights.append(
            f"Most common unit segment is {top_segment['bedroom_type']} "
            f"with {int(top_segment['unit_count'])} listings."
        )

    cheapest = df.sort_values("monthly_price_rm").iloc[0]
    insights.append(
        f"Cheapest listing is {cheapest['title']} at RM {int(cheapest['monthly_price_rm']):,}/month."
    )

    valid_value = df.dropna(subset=["price_per_sqft_rm"])

    if not valid_value.empty:
        best_value = valid_value.sort_values("price_per_sqft_rm").iloc[0]
        insights.append(
            f"Best value by RM/sqft is {best_value['title']} "
            f"at RM {best_value['price_per_sqft_rm']:.2f}/sqft."
        )

    if len(df) >= 4:
        q1 = monthly_prices.quantile(0.25)
        q3 = monthly_prices.quantile(0.75)
        iqr = q3 - q1
        high_outliers = df[df["monthly_price_rm"] > q3 + 1.5 * iqr]

        if not high_outliers.empty:
            insights.append(
                f"Detected {len(high_outliers)} potentially high-priced outlier listing(s) using IQR method."
            )
        else:
            insights.append("No extreme high-price outlier detected using IQR method.")

    return insights


def create_best_value_opportunities(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Category",
        "Rank",
        "Listing Title",
        "Unit Type",
        "Monthly Price (RM)",
        "Size (sqft)",
        "RM / sqft",
        "Data Completeness (%)",
        "Reason",
        "SPEEDHOME Link",
    ]

    if df.empty:
        return pd.DataFrame(columns=columns)

    opportunities = []

    def add_rows(category: str, source_df: pd.DataFrame, reason: str, limit: int = 5):
        if source_df.empty:
            return

        for rank, (_, row) in enumerate(source_df.head(limit).iterrows(), start=1):
            opportunities.append(
                {
                    "Category": category,
                    "Rank": rank,
                    "Listing Title": row.get("title"),
                    "Unit Type": row.get("bedroom_type"),
                    "Monthly Price (RM)": row.get("monthly_price_rm"),
                    "Size (sqft)": row.get("size_sqft"),
                    "RM / sqft": row.get("price_per_sqft_rm"),
                    "Data Completeness (%)": row.get("data_completeness_score"),
                    "Reason": reason,
                    "SPEEDHOME Link": row.get("listing_url"),
                }
            )

    valid_monthly = df.dropna(subset=["monthly_price_rm"]).copy()
    valid_value = df.dropna(subset=["price_per_sqft_rm"]).copy()
    valid_size = df.dropna(subset=["size_sqft"]).copy()
    valid_completeness = df.dropna(subset=["data_completeness_score"]).copy()

    if not valid_monthly.empty:
        add_rows(
            "Top Cheapest Listings",
            valid_monthly.sort_values("monthly_price_rm", ascending=True),
            "Lowest monthly rent among the selected listings.",
            limit=5,
        )

        median_price = valid_monthly["monthly_price_rm"].median()

        representative = (
            valid_monthly.assign(
                distance_to_median=(valid_monthly["monthly_price_rm"] - median_price).abs()
            )
            .sort_values(["distance_to_median", "monthly_price_rm"], ascending=[True, True])
        )

        add_rows(
            "Most Representative Fair-Price Listings",
            representative,
            "Closest listings to the median monthly rent.",
            limit=5,
        )

        under_median = valid_size[
            valid_size["monthly_price_rm"].notna()
            & (valid_size["monthly_price_rm"] <= median_price)
        ]

        add_rows(
            "Largest Units Under Median Rent",
            under_median.sort_values(["size_sqft", "monthly_price_rm"], ascending=[False, True]),
            "Largest units with monthly rent at or below the market median.",
            limit=5,
        )

    if not valid_value.empty:
        add_rows(
            "Best RM / sqft",
            valid_value.sort_values("price_per_sqft_rm", ascending=True),
            "Lowest rental cost per square foot.",
            limit=5,
        )

    if not valid_completeness.empty:
        add_rows(
            "Highest Data Completeness",
            valid_completeness.sort_values(
                ["data_completeness_score", "monthly_price_rm"],
                ascending=[False, True],
            ),
            "Listings with the most complete detected public data.",
            limit=5,
        )

    result = pd.DataFrame(opportunities, columns=columns)

    if result.empty:
        return result

    return result.sort_values(["Category", "Rank"]).reset_index(drop=True)


def create_outlier_report(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Listing Title",
        "Unit Type",
        "Monthly Price (RM)",
        "Size (sqft)",
        "RM / sqft",
        "Outlier Status",
        "IQR Lower Bound",
        "IQR Upper Bound",
        "Notes",
        "SPEEDHOME Link",
    ]

    if df.empty or "monthly_price_rm" not in df.columns:
        return pd.DataFrame(columns=columns)

    valid = df.dropna(subset=["monthly_price_rm"]).copy()

    if valid.empty:
        return pd.DataFrame(columns=columns)

    if len(valid) < 4:
        valid["Outlier Status"] = "Insufficient sample"
        valid["IQR Lower Bound"] = None
        valid["IQR Upper Bound"] = None
        valid["Notes"] = "At least 4 listings are recommended for IQR-based outlier detection."
    else:
        q1 = valid["monthly_price_rm"].quantile(0.25)
        q3 = valid["monthly_price_rm"].quantile(0.75)
        iqr = q3 - q1

        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr

        def classify_outlier(price):
            if price < lower_bound:
                return "Potentially Underpriced"
            if price > upper_bound:
                return "Potentially Overpriced"
            return "Normal"

        valid["Outlier Status"] = valid["monthly_price_rm"].apply(classify_outlier)
        valid["IQR Lower Bound"] = round(lower_bound, 2)
        valid["IQR Upper Bound"] = round(upper_bound, 2)
        valid["Notes"] = valid["Outlier Status"].map(
            {
                "Potentially Underpriced": "Monthly rent is below the IQR lower bound.",
                "Potentially Overpriced": "Monthly rent is above the IQR upper bound.",
                "Normal": "Monthly rent is within the expected IQR range.",
            }
        )

    report = valid.rename(
        columns={
            "title": "Listing Title",
            "bedroom_type": "Unit Type",
            "monthly_price_rm": "Monthly Price (RM)",
            "size_sqft": "Size (sqft)",
            "price_per_sqft_rm": "RM / sqft",
            "listing_url": "SPEEDHOME Link",
        }
    )

    return report[columns].sort_values(
        by=["Outlier Status", "Monthly Price (RM)"],
        ascending=[True, True],
    ).reset_index(drop=True)