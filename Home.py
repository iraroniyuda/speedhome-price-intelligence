import hashlib
import re
from datetime import datetime
from html import escape
from urllib.parse import urlparse

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    from streamlit_searchbox import st_searchbox
except Exception:
    st_searchbox = None

from analyzer import (
    build_dataframe,
    create_best_value_opportunities,
    create_data_quality_report,
    create_outlier_report,
    create_price_summary,
    create_rental_type_coverage,
    create_sample_size_confidence,
    generate_insights,
)
from exporter import to_excel_bytes
from history_store import clear_scrape_history, save_dataset_to_history
from scraper import (
    AREA_SUGGESTIONS,
    SpeedhomeFetchError,
    clear_speedhome_cache,
    normalize_input_to_url,
    scrape_speedhome_with_metadata,
)
from ui_style import inject_global_css


st.set_page_config(
    page_title="SPEEDHOME Property Price Intelligence",
    layout="wide",
)

inject_global_css()

st.title("SPEEDHOME Property Price Intelligence")
st.caption("Rental price intelligence dashboard for SPEEDHOME public property pages.")

st.divider()


CUSTOM_TARGET_PREFIX = "Use direct SPEEDHOME URL: "
CUSTOM_UNLISTED_TARGET_PREFIX = "Use custom SPEEDHOME rent URL: "
NO_MATCH_OPTION = "No matching area found"

# Target-area matching helpers.
# SPEEDHOME search pages can include nearby or unrelated cards even when the
# page headline says a target area. Source page alone is therefore not enough
# evidence that a listing is actually inside the requested area.
TARGET_AREA_ALIASES = {
    "bukit bintang": [
        "bukit bintang",
        "bukit ceylon",
        "jalan alor",
        "changkat",
        "raja chulan",
        "imbi",
        "pavilion",
        "berjaya times square",
        "jalan bukit bintang",
    ],
    "mont kiara": ["mont kiara"],
    "klcc": ["klcc", "kuala lumpur city centre"],
    "city centre": ["city centre", "kuala lumpur city centre", "kl city centre"],
}

# Extra locality names that often appear in SPEEDHOME cards/details but are not
# always present in AREA_SUGGESTIONS. Used only to reject obvious false positives
# when strict target-area filtering is enabled.
EXTRA_LOCATION_TOKENS = [
    "port dickson",
    "maluri",
    "cochrane",
    "dutamas",
    "chow kit",
    "kampung pandan",
    "pandan indah",
    "pandanmas",
    "jalan sultan ismail",
    "cheras",
    "sentul",
]


# ---------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------
def make_filename(input_value: str, extension: str) -> str:
    today = datetime.now().strftime("%Y%m%d")
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", str(input_value).strip())
    clean = clean.strip("_") or "speedhome"
    return f"SPEEDHOME_{clean}_{today}.{extension}"


def make_dataset_hash(value: str) -> str:
    return hashlib.md5(str(value).encode("utf-8")).hexdigest()[:10]


def reset_filter_widget_state() -> None:
    keys_to_remove = [
        key
        for key in list(st.session_state.keys())
        if str(key).startswith("result_filter_")
    ]

    for key in keys_to_remove:
        st.session_state.pop(key, None)


def clear_analysis_state() -> None:
    keys_to_remove = [
        "analysis_ready",
        "analysis_input",
        "analysis_raw_df",
        "analysis_metadata",
        "analysis_target_area",
        "analysis_normalized_url",
        "analysis_dataset_key",
        "analysis_dataset_hash",
    ]

    for key in keys_to_remove:
        st.session_state.pop(key, None)

    reset_filter_widget_state()


def is_speedhome_url_like(value: str) -> bool:
    value = (value or "").strip().lower()

    return (
        value.startswith("http://")
        or value.startswith("https://")
        or value.startswith("/rent/")
    )


def slugify_for_direct_url_validation(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def get_supported_rent_slugs() -> set:
    """Return built-in suggestion slugs for UX classification only.

    AREA_SUGGESTIONS is not a scraping whitelist. It only decides whether a
    direct SPEEDHOME /rent/<slug> URL is a familiar suggestion or a custom URL
    that should ask for explicit confirmation before scraping.
    """
    return {slugify_for_direct_url_validation(area) for area in AREA_SUGGESTIONS}


def _is_speedhome_host(hostname: str) -> bool:
    hostname = str(hostname or "").strip().lower().split(":")[0]
    return hostname == "speedhome.com" or hostname.endswith(".speedhome.com")


def extract_rent_slug_from_url_like(value: str) -> str:
    normalized_url = normalize_input_to_url(value)
    parsed = urlparse(normalized_url)

    if not _is_speedhome_host(parsed.netloc):
        raise ValueError("Only public SPEEDHOME URLs are supported for scraping.")

    path = (parsed.path or "").strip("/")
    parts = [part for part in path.split("/") if part]

    if len(parts) < 2 or parts[0].lower() != "rent":
        raise ValueError("Only SPEEDHOME rent pages are supported. Please use a /rent/<area-or-apartment> URL.")

    slug = slugify_for_direct_url_validation(parts[1])

    if not slug:
        raise ValueError("Could not detect a valid SPEEDHOME rent area slug.")

    return slug


def validate_direct_speedhome_rent_url(value: str) -> str:
    normalized_url = normalize_input_to_url(value)
    parsed = urlparse(normalized_url)
    slug = extract_rent_slug_from_url_like(normalized_url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "speedhome.com"
    return f"{scheme}://{netloc}/rent/{slug}"


def is_direct_speedhome_rent_url_outside_suggestions(value: str) -> bool:
    try:
        slug = extract_rent_slug_from_url_like(value)
        return slug not in get_supported_rent_slugs()
    except Exception:
        return False


def is_supported_direct_speedhome_rent_url(value: str) -> bool:
    try:
        validate_direct_speedhome_rent_url(value)
        return True
    except Exception:
        return False


def extract_listing_id_from_url(url):
    if not url:
        return "-"

    url_text = str(url).rstrip("/")
    last_segment = url_text.split("/")[-1]

    if not last_segment:
        return "-"

    return last_segment


def add_listing_display_identity(df):
    display_df = df.copy()

    if "listing_url" not in display_df.columns:
        display_df["open_listing"] = None
        display_df["listing_id"] = "-"
        return display_df

    display_df["open_listing"] = display_df["listing_url"]
    display_df["listing_id"] = display_df["listing_url"].apply(extract_listing_id_from_url)

    return display_df


# ---------------------------------------------------------------------
# Search input helpers
# ---------------------------------------------------------------------
def format_direct_url_option(value: str) -> str:
    return f'{CUSTOM_TARGET_PREFIX}"{value.strip()}"'


def format_custom_direct_url_option(value: str) -> str:
    return f'{CUSTOM_UNLISTED_TARGET_PREFIX}"{value.strip()}"'


def extract_target_from_searchbox_value(value: str) -> str:
    if not value:
        return ""

    value = str(value).strip()

    for prefix in [CUSTOM_TARGET_PREFIX, CUSTOM_UNLISTED_TARGET_PREFIX]:
        if value.startswith(prefix):
            raw_value = value.replace(prefix, "", 1).strip()
            raw_value = raw_value.strip('"').strip("'").strip()
            return raw_value

    return value


def reset_main_custom_url_confirmation_state(target: str, requires_confirmation: bool) -> None:
    previous_target = st.session_state.get("search_input_custom_url_value", "")

    st.session_state["search_input_custom_url_requires_confirmation"] = bool(requires_confirmation)
    st.session_state["search_input_custom_url_value"] = target if requires_confirmation else ""

    if not requires_confirmation:
        st.session_state["search_input_custom_url_confirmed"] = False
    elif previous_target != target:
        st.session_state["search_input_custom_url_confirmed"] = False


def strict_area_search(searchterm: str):
    searchterm = (searchterm or "").strip()

    if not searchterm:
        return AREA_SUGGESTIONS[:40]

    if is_speedhome_url_like(searchterm):
        if is_supported_direct_speedhome_rent_url(searchterm):
            if is_direct_speedhome_rent_url_outside_suggestions(searchterm):
                return [format_custom_direct_url_option(searchterm)]
            return [format_direct_url_option(searchterm)]
        return [NO_MATCH_OPTION]

    searchterm_lower = searchterm.lower()

    matches = [
        area
        for area in AREA_SUGGESTIONS
        if searchterm_lower in area.lower()
    ]

    matches = sorted(
        matches,
        key=lambda area: (
            not area.lower().startswith(searchterm_lower),
            len(area),
            area.lower(),
        ),
    )

    if matches:
        return matches[:40]

    return [NO_MATCH_OPTION]


def render_speedhome_searchbox():
    """
    One autocomplete searchbox:
    - Typing area/apartment names shows suggestions.
    - Random text cannot become a valid scraping target.
    - Supported direct SPEEDHOME rent URLs are accepted.
    """
    st.markdown("**SPEEDHOME URL / Area / Apartment**")

    current_target = st.session_state.get("selected_search_target", "Mont Kiara")

    if st_searchbox is None:
        st.error(
            "streamlit-searchbox is not installed. Install it with: pip install streamlit-searchbox"
        )
        st.session_state["search_input_is_valid"] = False
        return ""

    try:
        selected_value = st_searchbox(
            strict_area_search,
            key="main_speedhome_searchbox",
            default=current_target,
            default_options=AREA_SUGGESTIONS[:40],
            placeholder="Type area, apartment, or SPEEDHOME URL",
            clear_on_submit=False,
        )
    except TypeError:
        # Fallback for older streamlit-searchbox versions.
        selected_value = st_searchbox(
            strict_area_search,
            key="main_speedhome_searchbox",
        )

    if not selected_value:
        st.session_state["search_input_is_valid"] = False
        reset_main_custom_url_confirmation_state("", False)
        return ""

    if selected_value == NO_MATCH_OPTION:
        st.session_state["selected_search_target"] = ""
        st.session_state["search_input_is_valid"] = False
        reset_main_custom_url_confirmation_state("", False)
        st.warning(
            "No matching area found. Please choose a suggested area/apartment or paste a supported SPEEDHOME rent URL."
        )
        return ""

    selected_target = extract_target_from_searchbox_value(selected_value)

    if is_speedhome_url_like(selected_target):
        try:
            validate_direct_speedhome_rent_url(selected_target)
            requires_confirmation = is_direct_speedhome_rent_url_outside_suggestions(selected_target)

            st.session_state["selected_search_target"] = selected_target
            st.session_state["search_input_is_valid"] = True
            reset_main_custom_url_confirmation_state(selected_target, requires_confirmation)
            return selected_target

        except Exception as exc:
            st.session_state["selected_search_target"] = ""
            st.session_state["search_input_is_valid"] = False
            reset_main_custom_url_confirmation_state("", False)
            st.warning(str(exc))
            return ""

    valid_area_names = {area.lower() for area in AREA_SUGGESTIONS}

    if selected_target.lower() not in valid_area_names:
        st.session_state["selected_search_target"] = ""
        st.session_state["search_input_is_valid"] = False
        reset_main_custom_url_confirmation_state("", False)
        st.warning(
            "Please choose one of the suggested SPEEDHOME areas/apartments or paste a supported SPEEDHOME rent URL."
        )
        return ""

    st.session_state["selected_search_target"] = selected_target
    st.session_state["search_input_is_valid"] = True
    reset_main_custom_url_confirmation_state("", False)

    return selected_target


# ---------------------------------------------------------------------
# Display formatting helpers
# ---------------------------------------------------------------------
def prettify_summary_columns(summary):
    return summary.rename(
        columns={
            "bedroom_type": "Unit Type",
            "unit_count": "Unit Count",
            "sample_size_confidence": "Sample Size Confidence",
            "confidence_note": "Confidence Note",
            "average_price_rm": "Average Price (RM)",
            "median_price_rm": "Median Price (RM)",
            "mode_price_rm": "Mode Price (RM)",
            "fair_price_rm": "Fair Price (RM)",
            "average_size_sqft": "Average Size (sqft)",
            "average_price_per_sqft_rm": "Average RM / sqft",
            "average_data_completeness_score": "Avg. Data Completeness (%)",
        }
    )


def prettify_listing_columns(df):
    return df.rename(
        columns={
            "open_listing": "Open",
            "listing_id": "Listing ID",
            "title": "Listing Title",
            "property_area": "Property / Area",
            "source_area": "Scraped Source Area",
            "target_area_match": "Area Match",
            "area_quality_note": "Area Quality Note",
            "bedroom_type": "Unit Type",
            "bedrooms": "Bedrooms",
            "bathrooms": "Bathrooms",
            "bathroom_type_label": "Bathroom Type",
            "carparks": "Car Parks",
            "monthly_price_rm": "Monthly Price (RM)",
            "estimated_yearly_price_rm": "Estimated Yearly Price (RM)",
            "explicit_yearly_price_rm": "Explicit Yearly Price (RM)",
            "daily_price_rm": "Daily Price (RM)",
            "size_sqft": "Size (sqft)",
            "price_per_sqft_rm": "RM / sqft",
            "furnishing": "Furnishing",
            "detected_rental_type": "Detected Rental Type",
            "daily_price_status": "Daily Price Status",
            "monthly_price_status": "Monthly Price Status",
            "yearly_price_status": "Yearly Price Status",
            "data_completeness_score": "Data Completeness (%)",
            "listing_url": "SPEEDHOME Link",
        }
    )


def clean_listing_display_values(display_df):
    cleaned_df = display_df.copy()

    text_columns = [
        "Listing ID",
        "Listing Title",
        "Property / Area",
        "Scraped Source Area",
        "Area Match",
        "Area Quality Note",
        "Unit Type",
        "Bedrooms",
        "Bathrooms",
        "Bathroom Type",
        "Car Parks",
        "Furnishing",
        "Detected Rental Type",
        "Daily Price Status",
        "Monthly Price Status",
        "Yearly Price Status",
    ]

    for column in text_columns:
        if column in cleaned_df.columns:
            cleaned_df[column] = cleaned_df[column].fillna("Unknown").astype(str)

    if "Furnishing" in cleaned_df.columns:
        cleaned_df["Furnishing"] = cleaned_df["Furnishing"].apply(normalize_furnishing_for_display)

    return cleaned_df


def listing_table_column_config():
    return {
        "Open": st.column_config.LinkColumn(
            "Open",
            display_text="Open Listing",
        ),
        "SPEEDHOME Link": st.column_config.LinkColumn("SPEEDHOME Link"),
        "Monthly Price (RM)": st.column_config.NumberColumn(
            "Monthly Price (RM)",
            format="RM %d",
        ),
        "Estimated Yearly Price (RM)": st.column_config.NumberColumn(
            "Estimated Yearly Price (RM)",
            format="RM %d",
        ),
        "Explicit Yearly Price (RM)": st.column_config.NumberColumn(
            "Explicit Yearly Price (RM)",
            format="RM %d",
        ),
        "Daily Price (RM)": st.column_config.NumberColumn(
            "Daily Price (RM)",
            format="RM %d",
        ),
        "Size (sqft)": st.column_config.NumberColumn(
            "Size (sqft)",
            format="%d",
        ),
        "RM / sqft": st.column_config.NumberColumn(
            "RM / sqft",
            format="%.2f",
        ),
        "Data Completeness (%)": st.column_config.ProgressColumn(
            "Data Completeness (%)",
            min_value=0,
            max_value=100,
            format="%d%%",
        ),
    }




def render_metric_card_grid(items, min_width_px: int = 160):
    """Render metric cards with native Streamlit containers.

    Earlier versions used raw HTML cards. Some Streamlit/Markdown combinations
    can render the first HTML card as a code block. Native containers avoid that
    completely while keeping labels readable and responsive.
    """
    if not items:
        return

    normalized_items = []
    for item in items:
        if len(item) == 2:
            label, value = item
            note = ""
        else:
            label, value, note = item

        normalized_items.append(
            (
                str(label),
                str(value if value is not None else "N/A"),
                str(note or ""),
            )
        )

    # Use three columns on desktop to prevent long labels/values from being
    # clipped. Streamlit stacks columns on small screens, so this remains mobile-safe.
    cards_per_row = 3

    for start in range(0, len(normalized_items), cards_per_row):
        row_items = normalized_items[start:start + cards_per_row]
        columns = st.columns(len(row_items))

        for column, (label, value, note) in zip(columns, row_items):
            with column:
                with st.container(border=True):
                    st.markdown(
                        f"<div style='font-size:0.78rem;font-weight:700;line-height:1.35;color:#CBD5E1;white-space:normal;overflow-wrap:anywhere;'>{escape(label)}</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"<div style='font-size:clamp(1.45rem,2.1vw,2.15rem);font-weight:500;line-height:1.15;color:#F8FAFC;margin-top:0.45rem;white-space:normal;overflow-wrap:anywhere;'>{escape(value)}</div>",
                        unsafe_allow_html=True,
                    )
                    if note:
                        st.markdown(
                            f"<div style='font-size:0.76rem;line-height:1.35;color:#94A3B8;margin-top:0.45rem;white-space:normal;overflow-wrap:anywhere;'>{escape(note)}</div>",
                            unsafe_allow_html=True,
                        )


def render_confirmed_action_button(
    label: str,
    confirm_message: str,
    key: str,
    *,
    confirm_label: str = "Confirm",
    cancel_label: str = "Cancel",
    button_type: str = "secondary",
    disabled: bool = False,
):
    """Two-step confirmation button for destructive or interrupting actions."""
    confirm_state_key = f"{key}_confirm_open"

    if disabled:
        st.button(label, width="stretch", disabled=True, key=f"{key}_disabled")
        return False

    if not st.session_state.get(confirm_state_key, False):
        if st.button(label, width="stretch", type=button_type, key=f"{key}_open"):
            st.session_state[confirm_state_key] = True
            st.rerun()
        return False

    st.warning(confirm_message)

    confirm_col, cancel_col = st.columns(2)

    with confirm_col:
        confirmed = st.button(
            confirm_label,
            width="stretch",
            type="primary",
            key=f"{key}_confirm",
        )

    with cancel_col:
        cancelled = st.button(
            cancel_label,
            width="stretch",
            key=f"{key}_cancel",
        )

    if cancelled:
        st.session_state[confirm_state_key] = False
        st.rerun()

    if confirmed:
        st.session_state[confirm_state_key] = False
        return True

    return False


# ---------------------------------------------------------------------
# Scrape/cache helpers
# ---------------------------------------------------------------------
@st.cache_data(ttl=3600)
def cached_scrape(input_value: str, crawl_mode: str, enrich_missing_details: bool):
    return scrape_speedhome_with_metadata(
        input_value,
        crawl_mode=crawl_mode,
        enrich_missing_details=enrich_missing_details,
    )


def get_target_area_label(input_value: str) -> str:
    try:
        url = normalize_input_to_url(input_value)
        slug = url.split("/rent/")[-1].split("/")[0]
        return slug.replace("-", " ").title()
    except Exception:
        return str(input_value).strip().title()


def normalize_for_comparison(value: str) -> str:
    try:
        return normalize_input_to_url(value)
    except Exception:
        return str(value or "").strip().lower()


def slugify_for_match(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value



def _contains_area_token(text: str, token: str) -> bool:
    text = str(text or "").lower()
    token = str(token or "").strip().lower()

    if not token:
        return False

    slug = slugify_for_match(token)
    compact_text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")

    if slug and slug in compact_text:
        return True

    pattern = r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])"
    return bool(re.search(pattern, text))


def _target_aliases(target_area: str):
    target_key = str(target_area or "").strip().lower()
    aliases = TARGET_AREA_ALIASES.get(target_key, [target_key])

    output = []
    for item in [target_key, *aliases]:
        item = str(item or "").strip().lower()
        if item and item not in output:
            output.append(item)

    return output


def _conflicting_location_tokens(target_area: str):
    allowed = set(_target_aliases(target_area))
    allowed_slugs = {slugify_for_match(item) for item in allowed}

    tokens = []

    for item in [*AREA_SUGGESTIONS, *EXTRA_LOCATION_TOKENS]:
        item = str(item or "").strip().lower()
        if not item:
            continue

        item_slug = slugify_for_match(item)

        if item in allowed or item_slug in allowed_slugs:
            continue

        if item not in tokens:
            tokens.append(item)

    return tokens


def _row_target_evidence(row, target_area: str) -> bool:
    title = row.get("title", "")
    property_area = row.get("property_area", "")
    source_area = row.get("source_area", "")
    listing_url = row.get("listing_url", "")
    raw_text = row.get("raw_text", "")

    # property_area is often just a fallback copy of source_area. Do not use that
    # as evidence, otherwise every card on a broad SPEEDHOME search page passes.
    property_text = ""
    if str(property_area or "").strip().lower() != str(source_area or "").strip().lower():
        property_text = property_area

    combined = " ".join([
        str(title or ""),
        str(property_text or ""),
        str(listing_url or ""),
        str(raw_text or ""),
    ])

    return any(_contains_area_token(combined, alias) for alias in _target_aliases(target_area))


def _row_has_conflicting_location(row, target_area: str) -> bool:
    title = row.get("title", "")
    property_area = row.get("property_area", "")
    listing_url = row.get("listing_url", "")
    raw_text = row.get("raw_text", "")

    combined = " ".join([
        str(title or ""),
        str(property_area or ""),
        str(listing_url or ""),
        str(raw_text or ""),
    ])

    return any(_contains_area_token(combined, token) for token in _conflicting_location_tokens(target_area))


def apply_target_area_filter(df, target_area: str):
    """Keep verified listing cards from the selected SPEEDHOME result page.

    Important design decision: for SPEEDHOME /rent/<area> pages, the source of
    truth is the set of visible cards returned by that exact URL. We should not
    over-filter by guessing whether a building name is inside/outside the area,
    because many valid cards do not repeat the area in the title or slug.

    This filter therefore only removes non-listing artifacts: rows without a
    direct /details/ URL. It also adds transparent notes so the evaluator knows
    each row came from the selected SPEEDHOME result page.
    """
    if df.empty:
        return pd.DataFrame(columns=df.columns), 0

    required_columns = ["title", "property_area", "source_area", "listing_url", "raw_text"]

    working_df = df.copy()

    for column in required_columns:
        if column not in working_df.columns:
            working_df[column] = ""

    listing_url_series = working_df["listing_url"].fillna("").astype(str)
    direct_detail_link = listing_url_series.str.lower().str.contains(
        "/details/",
        na=False,
        regex=False,
    )

    target_evidence = working_df.apply(
        lambda row: _row_target_evidence(row, target_area),
        axis=1,
    )

    working_df["target_area_match"] = target_evidence.map(
        lambda value: "Strong" if value else "Source Page"
    )
    working_df["area_quality_note"] = [
        "Direct target evidence found in listing text or URL."
        if bool(has_target)
        else "Included because this card appears on the selected SPEEDHOME result page."
        for has_target in target_evidence
    ]

    filtered_df = working_df[direct_detail_link].copy()

    if filtered_df.empty:
        return pd.DataFrame(columns=working_df.columns), 0

    return filtered_df, len(filtered_df)




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



def summarize_reported_vs_rendered(metadata: dict, collected_count: int) -> dict:
    reported_total = _safe_int((metadata or {}).get("source_reported_total_count"))
    collected_count = int(collected_count or 0)

    if not reported_total:
        return {
            "reported_total": None,
            "collected_count": collected_count,
            "coverage_ratio": None,
            "raw_ratio": None,
            "status": "Reported total unavailable",
            "note": "SPEEDHOME did not expose a clear reported target count on this page.",
        }

    raw_ratio = round((collected_count / reported_total) * 100, 2)

    if collected_count < reported_total:
        return {
            "reported_total": reported_total,
            "collected_count": collected_count,
            "coverage_ratio": raw_ratio,
            "raw_ratio": raw_ratio,
            "status": "Partial rendered sample",
            "note": (
                "This run captured fewer rendered direct listing cards than the SPEEDHOME-reported target count. "
                "Treat market metrics as an observed public-page sample, not the full website inventory."
            ),
        }

    if collected_count == reported_total:
        return {
            "reported_total": reported_total,
            "collected_count": collected_count,
            "coverage_ratio": 100.0,
            "raw_ratio": 100.0,
            "status": "Matches reported total",
            "note": "Collected direct listing cards match the SPEEDHOME-reported target count.",
        }

    return {
        "reported_total": reported_total,
        "collected_count": collected_count,
        "coverage_ratio": None,
        "raw_ratio": raw_ratio,
        "status": "Rendered cards exceed reported target count",
        "note": (
            "SPEEDHOME rendered more direct listing cards than the target-count headline. "
            "This can happen when nearby or broader source-page cards appear on the selected result page. "
            "Use Area Match and Area Quality Note to distinguish strong target-evidence cards from source-page cards."
        ),
    }



def format_percent(value):
    if value is None:
        return "N/A"
    try:
        if pd.isna(value):
            return "N/A"
    except Exception:
        pass
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return "N/A"




FURNISHING_NOT_DETECTED_LABEL = "Not detected on result card"
KNOWN_FURNISHING_STATUSES = [
    "Fully Furnished",
    "Partially Furnished",
    "Unfurnished",
]


def normalize_furnishing_for_display(value):
    """Normalize furnishing values for UI/export without guessing missing data.

SPEEDHOME result cards do not always print the furnishing status even when
the website has furnishing filters. When the card text does not expose the
status, keep it explicit instead of pretending the unit is unfurnished.
"""
    text = str(value or "").strip()

    if not text or text.lower() in ["unknown", "not specified", "none", "nan"]:
        return FURNISHING_NOT_DETECTED_LABEL

    lower = text.lower()

    if "fully furnished" in lower:
        return "Fully Furnished"

    if "partially furnished" in lower or "partly furnished" in lower:
        return "Partially Furnished"

    if "unfurnished" in lower or "not furnished" in lower:
        return "Unfurnished"

    if lower == "furnished":
        return "Furnished"

    return text


def sort_furnishing_options(values):
    unique_values = []
    for value in values:
        normalized = normalize_furnishing_for_display(value)
        if normalized not in unique_values:
            unique_values.append(normalized)

    preferred_order = [*KNOWN_FURNISHING_STATUSES, "Furnished", FURNISHING_NOT_DETECTED_LABEL]
    ordered = [item for item in preferred_order if item in unique_values]
    ordered.extend(sorted([item for item in unique_values if item not in ordered]))
    return ordered

def apply_interactive_filters_and_sorting(df, key_prefix="main"):
    """Render Unit Listings-only filter controls in the main page.

    These controls intentionally do not live in the sidebar because sidebar
    controls are commonly interpreted as global dashboard filters. This function
    only changes the Unit Listings table view; core analytics and default exports
    continue to use the full analyzed dataset.
    """
    base_df = df.copy()
    filtered_df = base_df.copy()

    selected_price_range = None
    selected_size_range = None

    if "bedroom_type" not in base_df.columns:
        base_df["bedroom_type"] = "Unknown"
        filtered_df["bedroom_type"] = "Unknown"

    if "furnishing" not in base_df.columns:
        base_df["furnishing"] = "Unknown"
        filtered_df["furnishing"] = "Unknown"

    if "monthly_price_rm" not in base_df.columns:
        base_df["monthly_price_rm"] = pd.NA
        filtered_df["monthly_price_rm"] = pd.NA

    if "size_sqft" not in base_df.columns:
        base_df["size_sqft"] = pd.NA
        filtered_df["size_sqft"] = pd.NA

    base_df["furnishing_display"] = base_df["furnishing"].apply(normalize_furnishing_for_display)
    filtered_df["furnishing_display"] = filtered_df["furnishing"].apply(normalize_furnishing_for_display)

    available_unit_types = sorted(
        [
            str(item)
            for item in base_df["bedroom_type"].dropna().unique().tolist()
            if str(item).strip() not in ["", "Unknown"]
        ]
    )

    available_furnishing = sort_furnishing_options(
        base_df["furnishing_display"].dropna().unique().tolist()
    )

    sort_options = {
        "Original SPEEDHOME card order": (None, True),
        "Lowest monthly price": ("monthly_price_rm", True),
        "Highest monthly price": ("monthly_price_rm", False),
        "Best RM / sqft": ("price_per_sqft_rm", True),
        "Largest size": ("size_sqft", False),
        "Highest data completeness": ("data_completeness_score", False),
    }

    selected_unit_types = available_unit_types
    selected_furnishing = available_furnishing
    selected_sort = "Original SPEEDHOME card order"

    st.markdown("#### Unit Listing View Controls")
    st.caption(
        "These controls only affect the Unit Listings table below. "
        "Price Summary, charts, insights, and default downloads remain based on all analyzed listings."
    )

    filters_enabled = st.checkbox(
        "Apply filters to Unit Listings table",
        value=False,
        help="Off by default so the Unit Listings table shows all listing cards collected from the selected SPEEDHOME page.",
        key=f"result_filter_{key_prefix}_enabled",
    )

    if not filters_enabled:
        st.caption(f"Filters are off. Showing all {len(base_df)} analyzed listing card(s) in the table.")
        return filtered_df, {
            "filters_enabled": False,
            "selected_unit_types": selected_unit_types,
            "selected_furnishing": selected_furnishing,
            "selected_price_range": selected_price_range,
            "selected_size_range": selected_size_range,
            "sort_by": selected_sort,
        }

    control_col_1, control_col_2 = st.columns(2)

    with control_col_1:
        selected_unit_types = st.multiselect(
            "Unit Type",
            available_unit_types,
            default=available_unit_types,
            key=f"result_filter_{key_prefix}_unit_types",
        )

    with control_col_2:
        selected_furnishing = st.multiselect(
            "Detected Furnishing",
            available_furnishing,
            default=available_furnishing,
            help=(
                "Uses furnishing labels detected in the rendered SPEEDHOME listing cards. "
                "Rows shown as 'Not detected on result card' did not expose Fully/Partially/Unfurnished text on the public result card."
            ),
            key=f"result_filter_{key_prefix}_furnishing",
        )

    range_col_1, range_col_2 = st.columns(2)

    price_series = pd.to_numeric(base_df["monthly_price_rm"], errors="coerce").dropna()

    with range_col_1:
        if not price_series.empty:
            min_price = int(price_series.min())
            max_price = int(price_series.max())

            if min_price < max_price:
                selected_price_range = st.slider(
                    "Monthly Price Range (RM)",
                    min_value=min_price,
                    max_value=max_price,
                    value=(min_price, max_price),
                    step=50,
                    key=f"result_filter_{key_prefix}_monthly_price",
                )
            else:
                st.caption("Monthly price range is not available because all detected prices are the same.")
        else:
            st.caption("Monthly price range is not available because monthly prices were not detected.")

    size_series = pd.to_numeric(base_df["size_sqft"], errors="coerce").dropna()

    with range_col_2:
        if not size_series.empty:
            min_size = int(size_series.min())
            max_size = int(size_series.max())

            if min_size < max_size:
                selected_size_range = st.slider(
                    "Size Range (sqft)",
                    min_value=min_size,
                    max_value=max_size,
                    value=(min_size, max_size),
                    step=50,
                    key=f"result_filter_{key_prefix}_size",
                )
            else:
                st.caption("Size range is not available because all detected sizes are the same.")
        else:
            st.caption("Size range is not available because sizes were not detected.")

    selected_sort = st.selectbox(
        "Sort Unit Listings Table",
        list(sort_options.keys()),
        key=f"result_filter_{key_prefix}_sort",
    )

    if available_unit_types:
        filtered_df = filtered_df[filtered_df["bedroom_type"].isin(selected_unit_types)]

    if available_furnishing:
        filtered_df = filtered_df[filtered_df["furnishing_display"].isin(selected_furnishing)]

    if selected_price_range is not None:
        monthly_numeric = pd.to_numeric(filtered_df["monthly_price_rm"], errors="coerce")
        filtered_df = filtered_df[
            (monthly_numeric >= selected_price_range[0])
            & (monthly_numeric <= selected_price_range[1])
        ]

    if selected_size_range is not None:
        size_numeric = pd.to_numeric(filtered_df["size_sqft"], errors="coerce")
        filtered_df = filtered_df[
            (size_numeric >= selected_size_range[0])
            & (size_numeric <= selected_size_range[1])
        ]

    sort_column, ascending = sort_options[selected_sort]

    if sort_column and sort_column in filtered_df.columns:
        filtered_df = filtered_df.sort_values(
            by=sort_column,
            ascending=ascending,
            na_position="last",
        )

    return filtered_df, {
        "filters_enabled": True,
        "selected_unit_types": selected_unit_types,
        "selected_furnishing": selected_furnishing,
        "selected_price_range": selected_price_range,
        "selected_size_range": selected_size_range,
        "sort_by": selected_sort,
    }


# ---------------------------------------------------------------------
# Session default
# ---------------------------------------------------------------------
if "selected_search_target" not in st.session_state:
    st.session_state["selected_search_target"] = "Mont Kiara"

if "search_input_is_valid" not in st.session_state:
    st.session_state["search_input_is_valid"] = True

if "search_input_custom_url_requires_confirmation" not in st.session_state:
    st.session_state["search_input_custom_url_requires_confirmation"] = False

if "search_input_custom_url_confirmed" not in st.session_state:
    st.session_state["search_input_custom_url_confirmed"] = False

if "search_input_custom_url_value" not in st.session_state:
    st.session_state["search_input_custom_url_value"] = ""

if "main_scrape_in_progress" not in st.session_state:
    st.session_state["main_scrape_in_progress"] = False

if "main_scrape_cancel_requested" not in st.session_state:
    st.session_state["main_scrape_cancel_requested"] = False

if "main_scrape_start_requested" not in st.session_state:
    st.session_state["main_scrape_start_requested"] = False


# ---------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------
with st.sidebar:
    st.header("Search Input")

    st.caption(
        "Type an area/apartment name to get suggestions, or paste a direct SPEEDHOME rent URL."
    )

    user_input = render_speedhome_searchbox()

    st.caption("Selected target:")
    st.write(user_input if user_input else "-")

    try:
        preview_url = normalize_input_to_url(user_input)
        st.caption("Target URL:")
        st.caption(preview_url)
    except Exception as exc:
        st.warning(str(exc))

    custom_url_requires_confirmation = bool(
        st.session_state.get("search_input_custom_url_requires_confirmation", False)
    )

    if custom_url_requires_confirmation:
        st.warning(
            "This SPEEDHOME rent URL is not in the built-in suggestion list. "
            "It may be a new valid SPEEDHOME page or a typo. Confirm before scraping. "
            "If the page does not expose any public listing cards, the scraper will stop early and show diagnostics."
        )
        st.session_state["search_input_custom_url_confirmed"] = bool(
            st.checkbox(
                "I understand, continue with this custom SPEEDHOME rent URL",
                key="search_input_custom_url_confirmed_checkbox",
            )
        )

    st.info("Scraper uses robots.txt check, reasonable request delay, and local cache fallback.")

    strict_area_filter = st.checkbox(
        "Keep valid listing cards from selected result page",
        value=True,
        help=(
            "Keeps verified SPEEDHOME /details/ listing cards collected from the selected public result page. "
            "This prevents non-listing artifacts from entering the analysis without guessing too aggressively by area name."
        ),
        key="strict_area_filter",
    )

    enrich_missing_details = st.checkbox(
        "Enrich missing furnishing from detail pages",
        value=True,
        help=(
            "Slower but more accurate. Opens accepted SPEEDHOME detail pages only when result cards do not expose furnishing/unit details. "
            "Useful because some detail pages show Fully Furnished / Partially Furnished / Unfurnished even when the result card does not."
        ),
        key="enrich_missing_details",
    )

    if enrich_missing_details:
        st.caption(
            "Detail enrichment is on. First scrape may be slower because the app opens missing-detail listing pages."
        )

    st.caption(
        "Scrape mode: public rendered listing cards. The app automatically checks public pagination when available."
    )

    # Keep one safe default mode in the UI. Quick vs Deeper was useful during debugging,
    # but it can confuse evaluators when SPEEDHOME exposes the same public subset.
    scrape_crawl_mode = "deeper"

    main_scrape_busy = bool(st.session_state.get("main_scrape_in_progress"))
    search_input_ready = bool(st.session_state.get("search_input_is_valid", False))
    custom_url_ready = (
        not custom_url_requires_confirmation
        or bool(st.session_state.get("search_input_custom_url_confirmed", False))
    )
    analyze_button_disabled = (not search_input_ready) or (not custom_url_ready)

    analyze_clicked = False

    if main_scrape_busy:
        # During Streamlit execution, clicking any widget triggers a rerun.
        # Therefore cancel is intentionally single-click, not confirmation-based.
        if st.button(
            "Cancel Scrape",
            type="primary",
            width="stretch",
            key="main_cancel_scrape_direct",
        ):
            st.session_state["main_scrape_cancel_requested"] = True
            st.session_state["main_scrape_start_requested"] = False
            st.session_state["main_scrape_in_progress"] = False
            st.warning("Scrape cancellation requested.")
            st.rerun()
    else:
        if st.button(
            "Analyze SPEEDHOME Data",
            type="primary",
            width="stretch",
            disabled=analyze_button_disabled,
        ):
            # First rerun to immediately redraw the same button slot as Cancel Scrape
            # before the long Playwright scrape starts.
            st.session_state["main_scrape_in_progress"] = True
            st.session_state["main_scrape_cancel_requested"] = False
            st.session_state["main_scrape_start_requested"] = True
            st.rerun()

    # Public deployment safety:
    # Cache/history reset is intentionally not exposed in the public app.
    # The deployed assessment version relies on committed fallback datasets, so
    # a random visitor should not be able to delete runtime cache/history files.
    st.caption("Cache reset is disabled in the public deployed version to preserve fallback datasets.")


# ---------------------------------------------------------------------
# Analyze action
# Important:
# This block only fetches and stores data.
# Rendering happens below using st.session_state, so filters survive reruns.
# ---------------------------------------------------------------------
if st.session_state.pop("main_scrape_start_requested", False):
    st.session_state["main_scrape_in_progress"] = True

    if st.session_state.get("main_scrape_cancel_requested", False):
        st.session_state["main_scrape_in_progress"] = False
        st.session_state["main_scrape_cancel_requested"] = False
        st.warning("Scrape cancelled before it started.")
        st.stop()

    if not st.session_state.get("search_input_is_valid", False):
        clear_analysis_state()
        st.warning(
            "Please choose a valid area/apartment suggestion or paste a supported SPEEDHOME rent URL."
        )
        st.session_state["main_scrape_in_progress"] = False
        st.stop()

    if (
        st.session_state.get("search_input_custom_url_requires_confirmation", False)
        and not st.session_state.get("search_input_custom_url_confirmed", False)
    ):
        clear_analysis_state()
        st.warning("Please confirm the custom SPEEDHOME rent URL before scraping.")
        st.session_state["main_scrape_in_progress"] = False
        st.stop()

    if not user_input or not str(user_input).strip():
        clear_analysis_state()
        st.warning("Please enter a SPEEDHOME URL, area name, or apartment name.")
        st.session_state["main_scrape_in_progress"] = False
        st.stop()

    clear_analysis_state()

    try:
        with st.spinner("Fetching public SPEEDHOME listing data..."):
            listings, metadata = cached_scrape(user_input, scrape_crawl_mode, enrich_missing_details)

        raw_df = build_dataframe(listings)

        if raw_df.empty:
            st.error("No listing data found for this SPEEDHOME rent URL. The page may not exist, may have no public listings, or may not expose listing cards. No previous dataset was reused.")

            with st.expander("Scrape diagnostics"):
                st.write(f"Input: {user_input}")
                st.write(f"Target URL: {metadata.get('normalized_url', '-')}")
                st.write(f"Fetch Method: {metadata.get('fetch_method', '-')}")
                st.write(f"Cache Used: {'Yes' if metadata.get('cache_used') else 'No'}")
                st.write(f"Scrape Time: {metadata.get('scrape_duration_label', '-')}")
                st.write(f"HTML Length: {metadata.get('html_length', 0)}")
                st.write(f"Robots Allowed: {metadata.get('robots_allowed', '-')}")
                st.write(f"Scraper Notes: {metadata.get('notes', [])}")

            st.session_state["main_scrape_in_progress"] = False
            st.stop()

        target_area = get_target_area_label(user_input)
        normalized_url = normalize_input_to_url(user_input)
        dataset_key = f"{target_area}__{normalized_url}"
        dataset_hash = make_dataset_hash(dataset_key)

        st.session_state["analysis_ready"] = True
        st.session_state["analysis_input"] = user_input
        st.session_state["analysis_raw_df"] = raw_df
        st.session_state["analysis_metadata"] = metadata
        st.session_state["analysis_target_area"] = target_area
        st.session_state["analysis_normalized_url"] = normalized_url
        st.session_state["analysis_dataset_key"] = dataset_key
        st.session_state["analysis_dataset_hash"] = dataset_hash
        st.session_state["analysis_crawl_mode"] = scrape_crawl_mode
        st.session_state["analysis_detail_enrichment"] = enrich_missing_details

        reset_filter_widget_state()

        st.session_state["main_scrape_in_progress"] = False
        st.rerun()

    except SpeedhomeFetchError as exc:
        st.session_state["main_scrape_in_progress"] = False
        st.error(str(exc))

        metadata = getattr(exc, "metadata", {}) or {}

        with st.expander("Fetch failure diagnostics"):
            st.write(f"Input: {metadata.get('input', '-')}")
            st.write(f"Target URL: {metadata.get('normalized_url', '-')}")
            st.write(f"Fetch Method: {metadata.get('fetch_method', '-')}")
            st.write(f"Cache Used: {'Yes' if metadata.get('cache_used') else 'No'}")
            st.write(f"Scrape Time: {metadata.get('scrape_duration_label', '-')}")
            st.write(f"Current Run Time: {metadata.get('current_run_duration_label', '-')}")
            st.write(f"Robots Allowed: {metadata.get('robots_allowed', '-')}")
            st.write(f"Robots Policy Source: {metadata.get('robots_policy_source', '-')}")
            st.write(f"Requests Status Code: {metadata.get('requests_status_code', '-')}")
            st.write(f"Requests Final URL: {metadata.get('requests_final_url', '-')}")
            st.write(f"Playwright Status: {metadata.get('playwright_response_status', '-')}")
            st.write(f"Playwright Final URL: {metadata.get('playwright_final_url', '-')}")
            st.write(f"HTML Length: {metadata.get('html_length', 0)}")
            st.write(f"Scraper Notes: {metadata.get('notes', [])}")

        st.warning(
            "If cloud fetching is blocked later during deploy, use the local cache generated in data/speedhome_cache.json."
        )
        st.stop()

    except Exception as exc:
        st.session_state["main_scrape_in_progress"] = False
        st.error(str(exc))
        st.warning(
            "If cloud fetching is blocked later during deploy, use the local cache generated in data/speedhome_cache.json."
        )
        st.stop()


# ---------------------------------------------------------------------
# Guard: no analysis yet
# ---------------------------------------------------------------------
if not st.session_state.get("analysis_ready"):
    st.info("Choose or type a SPEEDHOME URL, area name, or apartment name from the sidebar, then click Analyze.")
    st.stop()


# ---------------------------------------------------------------------
# Load persisted analysis state
# ---------------------------------------------------------------------
raw_df = st.session_state["analysis_raw_df"]
metadata = st.session_state["analysis_metadata"]
analysis_input = st.session_state["analysis_input"]
target_area = st.session_state["analysis_target_area"]
normalized_url = st.session_state["analysis_normalized_url"]
dataset_key = st.session_state["analysis_dataset_key"]
dataset_hash = st.session_state["analysis_dataset_hash"]
analysis_crawl_mode = st.session_state.get("analysis_crawl_mode", metadata.get("crawl_mode", "quick"))


# ---------------------------------------------------------------------
# Stale input guard
# Prevent showing old analysis when sidebar input has changed.
# ---------------------------------------------------------------------
current_sidebar_input = user_input

current_sidebar_target = normalize_for_comparison(current_sidebar_input)
active_analysis_target = normalize_for_comparison(analysis_input)

if current_sidebar_target != active_analysis_target:
    st.warning(
        f"Input target has changed to '{current_sidebar_input}', "
        f"but the current displayed analysis is still for '{analysis_input}'. "
        "Click Analyze SPEEDHOME Data to refresh the result."
    )
    st.stop()

# ---------------------------------------------------------------------
# Target area filter
# ---------------------------------------------------------------------
before_filter_count = len(raw_df)
target_df = raw_df.copy()

if strict_area_filter:
    filtered_target_df, filtered_count = apply_target_area_filter(target_df, target_area)

    if filtered_count > 0:
        target_df = filtered_target_df
        strong_count = 0
        if "target_area_match" in target_df.columns:
            strong_count = int((target_df["target_area_match"] == "Strong").sum())

        st.info(
            f"Source-page card filter active: showing {len(target_df)} of "
            f"{before_filter_count} direct listing card(s) from the {target_area} result page. "
            f"{strong_count} listing(s) have strong target-area evidence; the rest are verified cards from the same SPEEDHOME page."
        )
    else:
        st.error(
            f"No reliable listing match found for '{target_area}'. "
            "Please choose a valid suggested area or paste a direct SPEEDHOME rent URL."
        )

        with st.expander("Scrape diagnostics"):
            st.write(f"Input: {analysis_input}")
            st.write(f"Target URL: {normalized_url}")
            st.write(f"Raw listings detected: {before_filter_count}")
            st.write(f"Fetch method: {metadata.get('fetch_method', '-')}")
            st.write(f"Cache used: {'Yes' if metadata.get('cache_used') else 'No'}")
            st.write(f"Scrape time: {metadata.get('scrape_duration_label', '-')}")
            st.write(f"Notes: {metadata.get('notes', [])}")

        st.stop()
else:
    st.info(
        f"Source-page card filter inactive: showing all {before_filter_count} scraped listing(s)."
    )


# ---------------------------------------------------------------------
# Save target dataset to history
# This overwrites the same dataset key on reruns, which is fine.
# ---------------------------------------------------------------------
try:
    save_dataset_to_history(
        dataset_key=dataset_key,
        target_area=target_area,
        source_url=normalized_url,
        raw_count=len(raw_df),
        filtered_count=len(target_df),
        fetch_method=metadata.get("fetch_method", "-"),
        cache_used=metadata.get("cache_used", False),
        df=target_df,
        metadata=metadata,
    )
except Exception as exc:
    st.warning(f"Could not save scrape history: {exc}")


# ---------------------------------------------------------------------
# Base analysis dataset
# ---------------------------------------------------------------------
analysis_df = target_df.copy()
filter_key_prefix = f"{dataset_hash}_{'strict' if strict_area_filter else 'all'}"


# ---------------------------------------------------------------------
# Analysis tables
# ---------------------------------------------------------------------
summary = create_price_summary(analysis_df)
rental_coverage = create_rental_type_coverage(analysis_df)
data_quality = create_data_quality_report(
    raw_df=raw_df,
    filtered_df=analysis_df,
    metadata=metadata,
    strict_area_filter=strict_area_filter,
    target_area=target_area,
)
sample_confidence = create_sample_size_confidence(analysis_df)
insights = generate_insights(analysis_df, summary)
best_value = create_best_value_opportunities(analysis_df)
outlier_report = create_outlier_report(analysis_df)


# ---------------------------------------------------------------------
# Top status
# ---------------------------------------------------------------------
count_reconciliation = summarize_reported_vs_rendered(metadata, len(analysis_df))
reported_total = count_reconciliation.get("reported_total")
coverage_ratio = count_reconciliation.get("coverage_ratio")
raw_rendered_reported_ratio = count_reconciliation.get("raw_ratio")
count_status = count_reconciliation.get("status")
count_note = count_reconciliation.get("note")

strong_target_count = int((analysis_df.get("target_area_match", pd.Series(dtype=str)).fillna("") == "Strong").sum()) if "target_area_match" in analysis_df.columns else 0
source_page_count = int((analysis_df.get("target_area_match", pd.Series(dtype=str)).fillna("") == "Source Page").sum()) if "target_area_match" in analysis_df.columns else len(analysis_df)

if reported_total:
    st.success(
        f"Collected {len(analysis_df)} valid direct listing card(s) from the rendered public SPEEDHOME page(s). "
        f"SPEEDHOME reports {reported_total} target result(s) for this search."
    )

    if len(analysis_df) < reported_total:
        st.warning(
            f"Coverage note: this run captured {format_percent(coverage_ratio)} of the SPEEDHOME-reported target count. "
            "Treat market metrics as an observed public-page sample, not the full website inventory."
        )
    elif len(analysis_df) > reported_total:
        st.info(
            "Rendered-card note: collected cards exceed the SPEEDHOME-reported target count. "
            "This is not shown as >100% coverage because SPEEDHOME may render nearby or broader source-page cards. "
            "Use Area Match and Area Quality Note to review target strength."
        )
else:
    st.success(f"Successfully collected {len(analysis_df)} valid direct listing card(s).")

monthly_prices = analysis_df["monthly_price_rm"].dropna() if "monthly_price_rm" in analysis_df.columns else pd.Series(dtype=float)
median_rent = int(monthly_prices.median()) if not monthly_prices.empty else None
average_rent = int(monthly_prices.mean()) if not monthly_prices.empty else None

metric_items = [
    ("Collected Cards", len(analysis_df)),
]

if reported_total:
    metric_items.extend(
        [
            ("SPEEDHOME Reported", reported_total),
            ("Strong Target Evidence", strong_target_count),
        ]
    )
else:
    metric_items.append(("Strong Target Evidence", strong_target_count))

available_segments = analysis_df["bedroom_type"].nunique() if "bedroom_type" in analysis_df.columns else 0
metric_items.extend(
    [
        ("Median Rent", f"RM {median_rent:,}" if median_rent else "N/A"),
        ("Average Rent", f"RM {average_rent:,}" if average_rent else "N/A"),
        ("Unit Segments", available_segments),
    ]
)

render_metric_card_grid(metric_items, min_width_px=165)

st.caption(f"Reported vs rendered status: {count_status}. {count_note}")
if source_page_count:
    st.caption(f"Area evidence split: {strong_target_count} strong target-evidence card(s), {source_page_count} source-page card(s).")

# ---------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------
st.subheader("Data Quality & Scraping Diagnostics")

render_metric_card_grid(
    [
        ("Raw Listings", len(raw_df)),
        ("Analysis Listings", len(analysis_df)),
        ("Fetch Method", str(metadata.get("fetch_method", "-")).title()),
        ("Public Pages", metadata.get("playwright_paginated_pages_fetched", 1)),
        ("Scrape Time", metadata.get("scrape_duration_label", "-")),
        ("Cache Used", "Yes" if metadata.get("cache_used") else "No"),
    ],
    min_width_px=160,
)

st.caption(
    f"Analytics and default downloads use all {len(analysis_df)} analyzed listing card(s). "
    "Unit Listings table filters appear later inside the Unit Listings section and only affect that table view."
)

with st.expander("View full data quality report"):
    st.dataframe(data_quality, width="stretch", hide_index=True)

with st.expander("Network/API diagnostics for deeper coverage"):
    st.caption(
        "Use this section to find whether SPEEDHOME exposes a search/listing API behind the rendered page. "
        "The current app still uses visible listing cards as the safe source of truth."
    )

    debug_file = metadata.get("network_debug_file")
    if debug_file:
        st.write(f"Network debug file: `{debug_file}`")
    else:
        st.write("Network debug file: `-`")

    top_likely_urls = metadata.get("network_top_likely_listing_urls") or metadata.get("playwright_top_debug_json_urls") or []
    captured_json_urls = metadata.get("playwright_captured_json_urls") or []
    candidate_urls = metadata.get("playwright_candidate_response_urls") or []

    st.write(f"Captured JSON payloads: {metadata.get('playwright_captured_json_payload_count', 0)}")
    st.write(f"Candidate network URLs: {len(candidate_urls)}")

    if top_likely_urls:
        st.markdown("**Top likely listing/search API candidates**")
        st.dataframe(
            pd.DataFrame({"URL": top_likely_urls}),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No high-confidence listing/search API candidate was detected yet.")

    if captured_json_urls:
        with st.expander("All captured JSON URLs"):
            st.dataframe(
                pd.DataFrame({"Captured JSON URL": captured_json_urls}),
                width="stretch",
                hide_index=True,
            )

    if candidate_urls:
        with st.expander("Candidate response URLs observed by Playwright"):
            st.dataframe(
                pd.DataFrame({"Candidate Response URL": candidate_urls}),
                width="stretch",
                hide_index=True,
            )


# ---------------------------------------------------------------------
# Sample confidence
# ---------------------------------------------------------------------
st.subheader("Sample Size Confidence")

overall_confidence_rows = sample_confidence[sample_confidence["Scope"] == "Overall"]

if not overall_confidence_rows.empty:
    overall_confidence = overall_confidence_rows.iloc[0]

    segment_count = sample_confidence[sample_confidence["Scope"] == "Unit Segment"].shape[0]
    render_metric_card_grid(
        [
            ("Confidence", overall_confidence["Confidence"]),
            ("Listing Count", int(overall_confidence["Listing Count"])),
            ("Unit Segments Checked", segment_count),
        ],
        min_width_px=190,
    )

    st.caption(overall_confidence["Interpretation"])

with st.expander("View confidence by unit segment"):
    st.dataframe(
        sample_confidence,
        width="stretch",
        hide_index=True,
    )


# ---------------------------------------------------------------------
# Price summary
# ---------------------------------------------------------------------
st.subheader("Price Summary")

display_summary = prettify_summary_columns(summary)

st.dataframe(
    display_summary,
    width="stretch",
    hide_index=True,
)

with st.expander("How Fair Price is calculated"):
    st.write(
        "Fair Price is an outlier-resistant estimate. For small samples, it uses the median. "
        "For larger samples, it removes extreme outliers using IQR, then combines median and trimmed mean."
    )


# ---------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------
st.subheader("Price Visualization")

chart_col_1, chart_col_2 = st.columns(2)

with chart_col_1:
    if summary.empty:
        st.info("No summary data available for charting.")
    else:
        fair_price_chart = px.bar(
            summary,
            x="bedroom_type",
            y="fair_price_rm",
            text="fair_price_rm",
            title="Fair Price by Unit Type",
            labels={
                "bedroom_type": "Unit Type",
                "fair_price_rm": "Fair Price (RM)",
            },
        )
        st.plotly_chart(fair_price_chart, width="stretch")

with chart_col_2:
    if analysis_df.empty or "monthly_price_rm" not in analysis_df.columns:
        st.info("No monthly rent data available for distribution chart.")
    else:
        distribution_chart = px.box(
            analysis_df,
            x="bedroom_type",
            y="monthly_price_rm",
            points="all",
            title="Monthly Rent Distribution",
            labels={
                "bedroom_type": "Unit Type",
                "monthly_price_rm": "Monthly Rent (RM)",
            },
        )
        st.plotly_chart(distribution_chart, width="stretch")


# ---------------------------------------------------------------------
# Best value
# ---------------------------------------------------------------------
st.subheader("Best Value Opportunities")

if best_value.empty:
    st.info("No best value opportunities could be calculated from the analyzed dataset.")
else:
    opportunity_categories = best_value["Category"].dropna().unique().tolist()

    best_rm_sqft_rows = best_value[best_value["Category"] == "Best RM / sqft"]
    if not best_rm_sqft_rows.empty:
        best_rm_sqft_value = best_rm_sqft_rows["RM / sqft"].min()
        best_rm_sqft_display = f"RM {best_rm_sqft_value:.2f}"
    else:
        best_rm_sqft_display = "N/A"

    render_metric_card_grid(
        [
            ("Opportunity Categories", len(opportunity_categories)),
            ("Ranked Opportunities", len(best_value)),
            ("Best RM / sqft", best_rm_sqft_display),
        ],
        min_width_px=190,
    )

    st.caption("Opportunities are grouped by investment/value angle, not only by lowest rent.")

    selected_opportunity_category = st.selectbox(
        "Choose opportunity category",
        opportunity_categories,
        key="best_value_opportunity_category",
        help=(
            "Using a dropdown keeps every opportunity category visible on mobile. "
            "This replaces horizontal tabs that can be easy to miss on small screens."
        ),
    )

    category_df = best_value[best_value["Category"] == selected_opportunity_category].copy()

    st.write(f"**{selected_opportunity_category}**")

    if not category_df.empty:
        st.caption(category_df["Reason"].iloc[0])

    st.dataframe(
        category_df.drop(columns=["Category"]),
        width="stretch",
        hide_index=True,
        column_config={
            "SPEEDHOME Link": st.column_config.LinkColumn("SPEEDHOME Link"),
            "Monthly Price (RM)": st.column_config.NumberColumn(
                "Monthly Price (RM)",
                format="RM %d",
            ),
            "RM / sqft": st.column_config.NumberColumn(
                "RM / sqft",
                format="%.2f",
            ),
            "Data Completeness (%)": st.column_config.ProgressColumn(
                "Data Completeness (%)",
                min_value=0,
                max_value=100,
                format="%d%%",
            ),
        },
    )

    with st.expander("View all opportunity rankings"):
        st.dataframe(
            best_value,
            width="stretch",
            hide_index=True,
            column_config={
                "SPEEDHOME Link": st.column_config.LinkColumn("SPEEDHOME Link"),
                "Monthly Price (RM)": st.column_config.NumberColumn(
                    "Monthly Price (RM)",
                    format="RM %d",
                ),
                "RM / sqft": st.column_config.NumberColumn(
                    "RM / sqft",
                    format="%.2f",
                ),
                "Data Completeness (%)": st.column_config.ProgressColumn(
                    "Data Completeness (%)",
                    min_value=0,
                    max_value=100,
                    format="%d%%",
                ),
            },
        )


# ---------------------------------------------------------------------
# Outlier
# ---------------------------------------------------------------------
st.subheader("Outlier Intelligence")

if outlier_report.empty:
    st.info("No outlier report could be generated from the analyzed dataset.")
else:
    outlier_counts = outlier_report["Outlier Status"].value_counts().to_dict()

    render_metric_card_grid(
        [
            ("Normal Listings", outlier_counts.get("Normal", 0)),
            ("Potentially Underpriced", outlier_counts.get("Potentially Underpriced", 0)),
            ("Potentially Overpriced", outlier_counts.get("Potentially Overpriced", 0)),
        ],
        min_width_px=190,
    )

    st.dataframe(
        outlier_report,
        width="stretch",
        hide_index=True,
        column_config={
            "SPEEDHOME Link": st.column_config.LinkColumn("SPEEDHOME Link"),
            "Monthly Price (RM)": st.column_config.NumberColumn(
                "Monthly Price (RM)",
                format="RM %d",
            ),
            "RM / sqft": st.column_config.NumberColumn(
                "RM / sqft",
                format="%.2f",
            ),
            "IQR Lower Bound": st.column_config.NumberColumn(
                "IQR Lower Bound",
                format="RM %.2f",
            ),
            "IQR Upper Bound": st.column_config.NumberColumn(
                "IQR Upper Bound",
                format="RM %.2f",
            ),
        },
    )

    with st.expander("How outlier detection works"):
        st.write(
            "Outlier detection uses the IQR method. Listings below Q1 - 1.5xIQR are marked as potentially underpriced, "
            "while listings above Q3 + 1.5xIQR are marked as potentially overpriced. "
            "This is directional market intelligence, not a final valuation."
        )


# ---------------------------------------------------------------------
# Unit listings
# ---------------------------------------------------------------------
st.subheader("Unit Listings")

before_interactive_filter_count = len(analysis_df)

# Important: this is a Unit Listings table-view control only.
# Core analytics, headline metrics, insights, and default exports already used
# the full analysis_df above.
display_df_working, filter_state = apply_interactive_filters_and_sorting(
    analysis_df,
    key_prefix=filter_key_prefix,
)

if filter_state.get("filters_enabled"):
    if display_df_working.empty:
        st.warning(
            "Unit Listings filters are active and currently hide all listing rows. "
            "Price Summary, charts, insights, and default downloads still use all analyzed listings."
        )
    elif len(display_df_working) != before_interactive_filter_count:
        st.info(
            f"Unit Listings filters active: showing {len(display_df_working)} of "
            f"{before_interactive_filter_count} analyzed listing card(s) in this table only. "
            "Price Summary, charts, insights, and default downloads still use all analyzed listings."
        )
    else:
        st.info(
            "Unit Listings filters are active, but no listing rows are currently excluded. "
            "Core analytics still use all analyzed listings."
        )
else:
    st.info(
        f"Unit Listings filters are off: showing all {len(display_df_working)} analyzed listing card(s)."
    )

listing_display_source = add_listing_display_identity(display_df_working)

display_columns = [
    "open_listing",
    "listing_id",
    "title",
    "monthly_price_rm",
    "estimated_yearly_price_rm",
    "explicit_yearly_price_rm",
    "daily_price_rm",
    "size_sqft",
    "price_per_sqft_rm",
    "bedroom_type",
    "bedrooms",
    "bathrooms",
    "bathroom_type_label",
    "carparks",
    "furnishing",
    "property_area",
    "source_area",
    "target_area_match",
    "area_quality_note",
    "detected_rental_type",
    "daily_price_status",
    "monthly_price_status",
    "yearly_price_status",
    "data_completeness_score",
    "listing_url",
]

existing_display_columns = [
    column
    for column in display_columns
    if column in listing_display_source.columns
]

unit_display_df = prettify_listing_columns(
    listing_display_source[existing_display_columns]
)

unit_display_df = clean_listing_display_values(unit_display_df)

st.caption(
    "The Open and Listing ID columns are placed first so visually similar listings can still be distinguished quickly. "
    "Furnishing values are detected from rendered result-card text; missing card-level labels are shown as "
    "'Not detected on result card' instead of being guessed."
)

st.dataframe(
    unit_display_df,
    width="stretch",
    hide_index=True,
    column_config=listing_table_column_config(),
)


# ---------------------------------------------------------------------
# Rental coverage
# ---------------------------------------------------------------------
st.subheader("Rental Type Coverage")

st.dataframe(
    rental_coverage,
    width="stretch",
    hide_index=True,
)

st.caption(
    "Yearly rent is marked as estimated when explicit yearly rental data is not detected from the public listing page."
)


# ---------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------
st.subheader("Automatic Insights")

for insight in insights:
    st.write(f"- {insight}")


# ---------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------
st.subheader("Download Data")

def build_export_listing_display(source_df):
    export_source = add_listing_display_identity(source_df)
    export_existing_columns = [
        column
        for column in display_columns
        if column in export_source.columns
    ]

    export_df = prettify_listing_columns(
        export_source[export_existing_columns]
    )

    return clean_listing_display_values(export_df)

all_export_df = build_export_listing_display(analysis_df)

csv_data = all_export_df.to_csv(index=False).encode("utf-8")
excel_data = to_excel_bytes(
    unit_df=all_export_df,
    summary_df=display_summary,
    rental_coverage_df=rental_coverage,
    data_quality_df=data_quality,
    best_value_df=best_value,
    outlier_report_df=outlier_report,
)

st.caption(
    "Default downloads include all analyzed listing cards. "
    "Filtered-view downloads appear separately when interactive filters are active."
)

download_col_1, download_col_2 = st.columns(2)

with download_col_1:
    st.download_button(
        label="Download All CSV",
        data=csv_data,
        file_name=make_filename(analysis_input, "csv"),
        mime="text/csv",
        width="stretch",
    )

with download_col_2:
    st.download_button(
        label="Download All Excel",
        data=excel_data,
        file_name=make_filename(analysis_input, "xlsx"),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )

if filter_state.get("filters_enabled") and len(display_df_working) != len(analysis_df):
    filtered_summary = prettify_summary_columns(create_price_summary(display_df_working))
    filtered_rental_coverage = create_rental_type_coverage(display_df_working)
    filtered_data_quality = create_data_quality_report(
        raw_df=raw_df,
        filtered_df=display_df_working,
        metadata=metadata,
        strict_area_filter=strict_area_filter,
        target_area=target_area,
    )
    filtered_best_value = create_best_value_opportunities(display_df_working)
    filtered_outlier_report = create_outlier_report(display_df_working)

    filtered_csv_data = unit_display_df.to_csv(index=False).encode("utf-8")
    filtered_excel_data = to_excel_bytes(
        unit_df=unit_display_df,
        summary_df=filtered_summary,
        rental_coverage_df=filtered_rental_coverage,
        data_quality_df=filtered_data_quality,
        best_value_df=filtered_best_value,
        outlier_report_df=filtered_outlier_report,
    )

    filtered_col_1, filtered_col_2 = st.columns(2)

    with filtered_col_1:
        st.download_button(
            label="Download Filtered View CSV",
            data=filtered_csv_data,
            file_name=make_filename(f"{analysis_input}_Filtered_View", "csv"),
            mime="text/csv",
            width="stretch",
        )

    with filtered_col_2:
        st.download_button(
            label="Download Filtered View Excel",
            data=filtered_excel_data,
            file_name=make_filename(f"{analysis_input}_Filtered_View", "xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
