from io import BytesIO
from datetime import datetime
from html import escape
from urllib.parse import urlparse
import re

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    from streamlit_searchbox import st_searchbox
except Exception:
    st_searchbox = None

from analyzer import (
    build_dataframe,
    create_price_summary,
    create_sample_size_confidence,
)
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
    page_title="Comparison Mode - SPEEDHOME Intelligence",
    layout="wide",
)

inject_global_css()

st.title("Comparison Mode")
st.caption(
    "Compare two SPEEDHOME rental areas using the same safe scraping behavior as the main dashboard: "
    "valid direct listing cards, public pagination/deeper crawl, transparent coverage, and no hidden analysis filters."
)

st.divider()


CUSTOM_TARGET_PREFIX = "Use direct SPEEDHOME URL: "
CUSTOM_UNLISTED_TARGET_PREFIX = "Use custom SPEEDHOME rent URL: "
NO_MATCH_OPTION = "No matching area found"


FURNISHING_NOT_DETECTED_LABEL = "Not detected on result card"


def normalize_furnishing_for_display(value):
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




def render_metric_card_grid(items, min_width_px: int = 160, cards_per_row: int = 3):
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

    # Streamlit stacks columns on small screens, so this remains mobile-safe.
    # The cards_per_row argument lets narrower half-page sections use 2 columns
    # instead of forcing long values into cramped 3-column cards.
    cards_per_row = max(1, int(cards_per_row or 3))

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
                        f"<div style='font-size:clamp(1.35rem,1.8vw,1.95rem);font-weight:500;line-height:1.15;color:#F8FAFC;margin-top:0.45rem;white-space:normal;overflow-wrap:anywhere;'>{escape(value)}</div>",
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
# Cache / basic helpers
# ---------------------------------------------------------------------
@st.cache_data(ttl=3600)
def cached_scrape(input_value: str, crawl_mode: str, enrich_missing_details: bool):
    return scrape_speedhome_with_metadata(
        input_value,
        crawl_mode=crawl_mode,
        enrich_missing_details=enrich_missing_details,
    )


def make_comparison_filename(extension: str) -> str:
    today = datetime.now().strftime("%Y%m%d")
    return f"SPEEDHOME_two_area_comparison_{today}.{extension}"


def money(value):
    if value is None:
        return "N/A"

    try:
        if pd.isna(value):
            return "N/A"
    except Exception:
        pass

    try:
        return f"RM {int(value):,}"
    except Exception:
        return "N/A"


def pct(value):
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


def coverage_display_value(coverage_ratio, collected_count=None, reported_total=None):
    """Return a reviewer-friendly coverage label.

    Coverage is only comparable when SPEEDHOME exposes a reported target count
    and collected direct listing cards do not exceed that reported count.
    If the reported count is missing, show a clear explanation instead of N/A.
    If rendered cards exceed the headline count, avoid misleading percentages
    such as 188% and show "Not comparable" instead.
    """
    if coverage_ratio is not None:
        return pct(coverage_ratio)

    try:
        if reported_total is None or pd.isna(reported_total):
            return "No target count"
    except Exception:
        if reported_total is None:
            return "No target count"

    try:
        if pd.notna(reported_total) and pd.notna(collected_count):
            if int(collected_count) > int(reported_total):
                return "Not comparable"
    except Exception:
        pass

    return "Not available"


def number_or_na(value):
    if value is None:
        return "N/A"

    try:
        if pd.isna(value):
            return "N/A"
    except Exception:
        pass

    try:
        return int(value)
    except Exception:
        return value


def reported_total_display(value):
    """Display SPEEDHOME reported count as text for Arrow-safe Streamlit tables.

    Streamlit dataframes are serialized through PyArrow. A column that mixes
    integers such as 19 with text such as "Not exposed" can trigger Arrow
    conversion warnings. This helper always returns text so reviewer-friendly
    labels do not break dataframe serialization.
    """
    if value is None:
        return "Not exposed"

    try:
        if pd.isna(value):
            return "Not exposed"
    except Exception:
        pass

    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


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


def normalize_for_comparison(value: str) -> str:
    if not value:
        return ""

    try:
        return normalize_input_to_url(value)
    except Exception:
        return str(value or "").strip().lower()


def slugify_for_match(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def validate_speedhome_target(input_value: str) -> str:
    input_value = str(input_value or "").strip()

    if not input_value:
        raise ValueError("Area input cannot be empty.")

    if is_speedhome_url_like(input_value):
        validate_direct_speedhome_rent_url(input_value)
        return input_value

    valid_area_names = {area.lower() for area in AREA_SUGGESTIONS}

    if input_value.lower() not in valid_area_names:
        raise ValueError(
            "Please choose a valid area/apartment suggestion or paste a supported SPEEDHOME rent URL."
        )

    return input_value


def extract_listing_id_from_url(url):
    if not url:
        return "-"

    url_text = str(url).rstrip("/")
    last_segment = url_text.split("/")[-1]
    return last_segment or "-"


def add_listing_display_identity(df: pd.DataFrame) -> pd.DataFrame:
    display_df = df.copy()

    if "listing_url" not in display_df.columns:
        display_df["open_listing"] = None
        display_df["listing_id"] = "-"
        return display_df

    display_df["open_listing"] = display_df["listing_url"]
    display_df["listing_id"] = display_df["listing_url"].apply(extract_listing_id_from_url)

    return display_df


# ---------------------------------------------------------------------
# Searchbox helpers — same strict behavior as app.py
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


def reset_custom_url_confirmation_state(selected_target_key: str, target: str, requires_confirmation: bool) -> None:
    flag_key = f"{selected_target_key}_custom_url_requires_confirmation"
    confirm_key = f"{selected_target_key}_custom_url_confirmed"
    value_key = f"{selected_target_key}_custom_url_value"
    previous_target = st.session_state.get(value_key, "")

    st.session_state[flag_key] = bool(requires_confirmation)
    st.session_state[value_key] = target if requires_confirmation else ""

    if not requires_confirmation:
        st.session_state[confirm_key] = False
    elif previous_target != target:
        st.session_state[confirm_key] = False


def strict_area_search(searchterm: str):
    searchterm = (searchterm or "").strip()

    if not searchterm:
        return AREA_SUGGESTIONS[:25]

    if is_speedhome_url_like(searchterm):
        if is_supported_direct_speedhome_rent_url(searchterm):
            if is_direct_speedhome_rent_url_outside_suggestions(searchterm):
                return [format_custom_direct_url_option(searchterm)]
            return [format_direct_url_option(searchterm)]
        return [NO_MATCH_OPTION]

    searchterm_lower = searchterm.lower()

    matches = [area for area in AREA_SUGGESTIONS if searchterm_lower in area.lower()]

    matches = sorted(
        matches,
        key=lambda area: (
            not area.lower().startswith(searchterm_lower),
            len(area),
            area.lower(),
        ),
    )

    if matches:
        return matches[:25]

    return [NO_MATCH_OPTION]


def render_speedhome_searchbox(
    label: str,
    selected_target_key: str,
    searchbox_key: str,
    default_value: str,
):
    st.markdown(f"**{label}**")

    valid_key = f"{selected_target_key}_is_valid"
    current_target = st.session_state.get(selected_target_key, default_value)

    if st_searchbox is None:
        st.error(
            "streamlit-searchbox is not installed. Install it with: pip install streamlit-searchbox"
        )
        st.session_state[selected_target_key] = ""
        st.session_state[valid_key] = False
        return ""

    try:
        selected_value = st_searchbox(
            strict_area_search,
            key=searchbox_key,
            default=current_target,
            default_options=AREA_SUGGESTIONS[:25],
            placeholder="Type area, apartment, or SPEEDHOME URL",
            clear_on_submit=False,
        )
    except TypeError:
        selected_value = st_searchbox(strict_area_search, key=searchbox_key)

    if not selected_value:
        st.session_state[valid_key] = False
        reset_custom_url_confirmation_state(selected_target_key, "", False)
        return ""

    if selected_value == NO_MATCH_OPTION:
        st.session_state[selected_target_key] = ""
        st.session_state[valid_key] = False
        reset_custom_url_confirmation_state(selected_target_key, "", False)
        st.warning(
            "No matching area found. Please choose a suggested area/apartment or paste a supported SPEEDHOME rent URL."
        )
        return ""

    selected_target = extract_target_from_searchbox_value(selected_value)

    if is_speedhome_url_like(selected_target):
        try:
            validate_direct_speedhome_rent_url(selected_target)
            requires_confirmation = is_direct_speedhome_rent_url_outside_suggestions(selected_target)

            st.session_state[selected_target_key] = selected_target
            st.session_state[valid_key] = True
            reset_custom_url_confirmation_state(selected_target_key, selected_target, requires_confirmation)
            return selected_target

        except Exception as exc:
            st.session_state[selected_target_key] = ""
            st.session_state[valid_key] = False
            reset_custom_url_confirmation_state(selected_target_key, "", False)
            st.warning(str(exc))
            return ""

    valid_area_names = {area.lower() for area in AREA_SUGGESTIONS}

    if selected_target.lower() not in valid_area_names:
        st.session_state[selected_target_key] = ""
        st.session_state[valid_key] = False
        reset_custom_url_confirmation_state(selected_target_key, "", False)
        st.warning(
            "Please choose one of the suggested SPEEDHOME areas/apartments or paste a supported SPEEDHOME rent URL."
        )
        return ""

    st.session_state[selected_target_key] = selected_target
    st.session_state[valid_key] = True
    reset_custom_url_confirmation_state(selected_target_key, "", False)

    return selected_target


def render_custom_url_confirmation(selected_target_key: str) -> bool:
    flag_key = f"{selected_target_key}_custom_url_requires_confirmation"
    confirm_key = f"{selected_target_key}_custom_url_confirmed"

    requires_confirmation = bool(st.session_state.get(flag_key, False))

    if not requires_confirmation:
        return True

    st.warning(
        "This SPEEDHOME rent URL is not in the built-in suggestion list. "
        "It may be a new valid SPEEDHOME page or a typo. Confirm before scraping. "
        "If the page does not expose any public listing cards, the scraper will stop early and show diagnostics."
    )

    return bool(
        st.checkbox(
            "I understand, continue with this custom SPEEDHOME rent URL",
            key=confirm_key,
        )
    )


# ---------------------------------------------------------------------
# Area processing helpers
# ---------------------------------------------------------------------
def get_target_area_label(input_value: str) -> str:
    try:
        url = normalize_input_to_url(input_value)
        slug = url.split("/rent/")[-1].split("/")[0]
        return slug.replace("-", " ").title()
    except Exception:
        return str(input_value).strip().title()


def numeric_series(df: pd.DataFrame, column: str):
    if column not in df.columns:
        return pd.Series(dtype="float")
    return pd.to_numeric(df[column], errors="coerce").dropna()


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


def _row_target_evidence(row, target_area: str) -> bool:
    title = row.get("title", "")
    property_area = row.get("property_area", "")
    source_area = row.get("source_area", "")
    listing_url = row.get("listing_url", "")
    raw_text = row.get("raw_text", "")

    property_text = ""
    if str(property_area or "").strip().lower() != str(source_area or "").strip().lower():
        property_text = property_area

    combined = " ".join(
        [
            str(title or ""),
            str(property_text or ""),
            str(listing_url or ""),
            str(raw_text or ""),
        ]
    )

    return any(_contains_area_token(combined, alias) for alias in _target_aliases(target_area))


def apply_source_page_listing_filter(df: pd.DataFrame, target_area: str):
    """Keep valid direct listing cards from the selected SPEEDHOME result page.

    Same design as the main app: do not over-filter by guessing whether a building
    name is inside/outside the area. SPEEDHOME's selected result page is treated as
    the source context; this filter mainly removes artifacts without /details/ links.
    """
    if df.empty:
        return pd.DataFrame(columns=df.columns), 0

    working_df = df.copy()

    for column in ["title", "property_area", "source_area", "listing_url", "raw_text"]:
        if column not in working_df.columns:
            working_df[column] = ""

    direct_detail_link = working_df["listing_url"].fillna("").astype(str).str.lower().str.contains(
        "/details/",
        na=False,
        regex=False,
    )

    target_evidence = working_df.apply(lambda row: _row_target_evidence(row, target_area), axis=1)

    working_df["target_area_match"] = target_evidence.map(lambda value: "Strong" if value else "Source Page")
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


def get_metadata_value(metadata: dict, key: str, default=None):
    value = (metadata or {}).get(key, default)

    if value in ["", None]:
        return default

    return value


def get_reported_total(metadata: dict):
    value = get_metadata_value(metadata, "source_reported_total_count")
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def reconcile_reported_vs_rendered(metadata: dict, collected_count: int) -> dict:
    reported_total = get_reported_total(metadata)
    collected_count = int(collected_count or 0)

    if not reported_total:
        return {
            "reported_total": None,
            "coverage_ratio": None,
            "raw_ratio": None,
            "coverage_confidence": "Unknown",
            "status": "Reported total unavailable",
            "note": "SPEEDHOME did not expose a clear reported target count on this page.",
        }

    raw_ratio = round((collected_count / reported_total) * 100, 2)

    if collected_count < reported_total:
        if raw_ratio >= 80:
            label = "High"
            note = "Collected cards cover most of the SPEEDHOME-reported target count."
        elif raw_ratio >= 40:
            label = "Medium"
            note = "Collected cards cover a partial but meaningful portion of the reported target count."
        else:
            label = "Low"
            note = "Collected cards cover a small portion of the reported target count; treat this as a rendered public-page sample."

        return {
            "reported_total": reported_total,
            "coverage_ratio": raw_ratio,
            "raw_ratio": raw_ratio,
            "coverage_confidence": label,
            "status": "Partial rendered sample",
            "note": note,
        }

    if collected_count == reported_total:
        return {
            "reported_total": reported_total,
            "coverage_ratio": 100.0,
            "raw_ratio": 100.0,
            "coverage_confidence": "High",
            "status": "Matches reported total",
            "note": "Collected direct listing cards match the SPEEDHOME-reported target count.",
        }

    return {
        "reported_total": reported_total,
        "coverage_ratio": None,
        "raw_ratio": raw_ratio,
        "coverage_confidence": "Not comparable",
        "status": "Rendered cards exceed reported target count",
        "note": "SPEEDHOME rendered more direct listing cards than the target-count headline. This may include nearby or broader source-page cards, so this is not treated as >100% coverage.",
    }


def get_coverage_ratio(metadata: dict, collected_count: int):
    return reconcile_reported_vs_rendered(metadata, collected_count).get("coverage_ratio")


def coverage_confidence_label(metadata: dict, collected_count: int):
    reconciliation = reconcile_reported_vs_rendered(metadata, collected_count)
    return reconciliation.get("coverage_confidence"), reconciliation.get("note")


def scrape_area_for_comparison(input_value: str, crawl_mode: str, enrich_missing_details: bool = False):
    input_value = validate_speedhome_target(input_value)

    source_url = normalize_input_to_url(input_value)
    target_area = get_target_area_label(input_value)

    listings, metadata = cached_scrape(input_value, crawl_mode, enrich_missing_details)
    raw_df = build_dataframe(listings)

    if raw_df.empty:
        raise SpeedhomeFetchError(
            f"No listing data found for {target_area}. The page may not exist, may have no public listings, or may not expose listing cards. No previous dataset was reused.",
            metadata=metadata,
        )

    target_df, filtered_count = apply_source_page_listing_filter(raw_df, target_area)

    if filtered_count <= 0:
        raise RuntimeError(
            f"No valid direct listing cards found for '{target_area}'. "
            "Try a different SPEEDHOME rent URL or use Deeper crawl."
        )

    reconciliation = reconcile_reported_vs_rendered(metadata, len(target_df))
    reported_total = reconciliation.get("reported_total")
    coverage_ratio = reconciliation.get("coverage_ratio")
    raw_ratio = reconciliation.get("raw_ratio")
    coverage_label = reconciliation.get("coverage_confidence")
    coverage_note = reconciliation.get("note")
    rendered_status = reconciliation.get("status")

    target_df["comparison_area"] = target_area
    target_df["comparison_source_url"] = source_url
    target_df["comparison_fetch_method"] = metadata.get("fetch_method", "-")
    target_df["comparison_cache_used"] = metadata.get("cache_used", False)
    target_df["comparison_crawl_mode"] = metadata.get("crawl_mode", crawl_mode)
    target_df["comparison_reported_total"] = reported_total
    target_df["comparison_scrape_coverage_ratio"] = coverage_ratio
    target_df["comparison_raw_rendered_reported_ratio"] = raw_ratio
    target_df["comparison_reported_vs_rendered_status"] = rendered_status
    target_df["comparison_coverage_confidence"] = coverage_label

    filter_note = (
        f"Using all {len(target_df)} valid direct listing card(s) collected from the selected SPEEDHOME public result page."
    )

    save_dataset_to_history(
        dataset_key=f"{target_area}__{source_url}__crawl={crawl_mode}",
        target_area=target_area,
        source_url=source_url,
        raw_count=len(raw_df),
        filtered_count=len(target_df),
        fetch_method=metadata.get("fetch_method", "-"),
        cache_used=metadata.get("cache_used", False),
        df=target_df,
        metadata=metadata,
    )

    return {
        "input_value": input_value,
        "area_name": target_area,
        "source_url": source_url,
        "crawl_mode": crawl_mode,
        "raw_count": len(raw_df),
        "filtered_count": len(target_df),
        "reported_total": reported_total,
        "coverage_ratio": coverage_ratio,
        "raw_rendered_reported_ratio": raw_ratio,
        "reported_vs_rendered_status": rendered_status,
        "coverage_confidence": coverage_label,
        "coverage_note": coverage_note,
        "raw_df": raw_df,
        "df": target_df,
        "metadata": metadata,
        "filter_note": filter_note,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
    }


def is_result_stale(current_input: str, current_crawl_mode: str, result: dict) -> bool:
    if not result:
        return False

    current_target = normalize_for_comparison(current_input)
    result_target = normalize_for_comparison(result.get("input_value", ""))
    result_crawl_mode = str(result.get("crawl_mode", "quick") or "quick").lower()
    current_crawl_mode = str(current_crawl_mode or "quick").lower()

    return current_target != result_target or current_crawl_mode != result_crawl_mode


# ---------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------
def area_stats(df: pd.DataFrame) -> dict:
    monthly = numeric_series(df, "monthly_price_rm")
    sqft = numeric_series(df, "size_sqft")
    rm_sqft = numeric_series(df, "price_per_sqft_rm")
    completeness = numeric_series(df, "data_completeness_score")

    return {
        "Listings": len(df),
        "Unit Segments": df["bedroom_type"].nunique() if "bedroom_type" in df.columns else 0,
        "Median Monthly Rent (RM)": monthly.median() if not monthly.empty else None,
        "Average Monthly Rent (RM)": monthly.mean() if not monthly.empty else None,
        "Lowest Monthly Rent (RM)": monthly.min() if not monthly.empty else None,
        "Highest Monthly Rent (RM)": monthly.max() if not monthly.empty else None,
        "Average Size (sqft)": sqft.mean() if not sqft.empty else None,
        "Average RM / sqft": rm_sqft.mean() if not rm_sqft.empty else None,
        "Best RM / sqft": rm_sqft.min() if not rm_sqft.empty else None,
        "Average Data Completeness (%)": completeness.mean() if not completeness.empty else None,
    }


def get_overall_confidence(df: pd.DataFrame) -> dict:
    confidence_df = create_sample_size_confidence(df)

    if confidence_df.empty:
        return {
            "Confidence": "No sample",
            "Listing Count": 0,
            "Confidence Note": "No listings are available, so market confidence cannot be calculated.",
        }

    overall_rows = confidence_df[confidence_df["Scope"] == "Overall"]

    if overall_rows.empty:
        return {
            "Confidence": "No sample",
            "Listing Count": 0,
            "Confidence Note": "No listings are available, so market confidence cannot be calculated.",
        }

    row = overall_rows.iloc[0]

    return {
        "Confidence": row["Confidence"],
        "Listing Count": int(row["Listing Count"]),
        "Confidence Note": row["Interpretation"],
    }


def create_comparison_sample_confidence(area_a_name, df_a, area_b_name, df_b):
    confidence_a = create_sample_size_confidence(df_a)
    confidence_a.insert(0, "Area", area_a_name)

    confidence_b = create_sample_size_confidence(df_b)
    confidence_b.insert(0, "Area", area_b_name)

    combined = pd.concat([confidence_a, confidence_b], ignore_index=True)
    combined = combined.rename(columns={"Interpretation": "Confidence Note"})

    return combined[
        ["Area", "Scope", "Segment", "Listing Count", "Confidence", "Confidence Note"]
    ]


def create_area_diagnostics(results):
    rows = []

    for label, result in results:
        metadata = result.get("metadata", {}) or {}
        rows.append(
            {
                "Label": label,
                "Area": result.get("area_name"),
                "Input": result.get("input_value"),
                "Source URL": result.get("source_url"),
                "Crawl Mode": result.get("crawl_mode") or metadata.get("crawl_mode"),
                "Fetch Method": metadata.get("fetch_method", "-"),
                "Cache Used": "Yes" if metadata.get("cache_used") else "No",
                "Scraped At": metadata.get("scraped_at", result.get("scraped_at", "-")),
                "Scrape Started At": metadata.get("scrape_started_at", "-"),
                "Scrape Finished At": metadata.get("scrape_finished_at", "-"),
                "Live Scrape Duration": metadata.get("scrape_duration_label", "-"),
                "Live Scrape Duration Seconds": metadata.get("scrape_duration_seconds", "-"),
                "Current Run Duration": metadata.get("current_run_duration_label", metadata.get("scrape_duration_label", "-")),
                "Current Run Duration Seconds": metadata.get("current_run_duration_seconds", metadata.get("scrape_duration_seconds", "-")),
                "Cache Lookup Duration": metadata.get("cache_lookup_duration_label", "-"),
                "SPEEDHOME Reported Total": reported_total_display(result.get("reported_total")),
                "Collected Direct Listing Cards": len(result.get("df", pd.DataFrame())),
                "Scrape Coverage": coverage_display_value(
                    result.get("coverage_ratio"),
                    len(result.get("df", pd.DataFrame())),
                    result.get("reported_total"),
                ),
                "Raw Rendered / Reported Ratio (%)": result.get("raw_rendered_reported_ratio"),
                "Reported vs Rendered Status": result.get("reported_vs_rendered_status"),
                "Coverage Confidence": result.get("coverage_confidence"),
                "Coverage Note": result.get("coverage_note"),
                "Robots Allowed": metadata.get("robots_allowed", "-"),
                "Robots Policy Source": metadata.get("robots_policy_source", "-"),
                "HTML Length": metadata.get("html_length", 0),
                "Pages Fetched": metadata.get("playwright_pages_fetched", "-"),
                "Page Detail Counts": " | ".join([str(item) for item in metadata.get("playwright_page_detail_counts", [])])
                if isinstance(metadata.get("playwright_page_detail_counts"), list)
                else metadata.get("playwright_page_detail_counts", "-"),
                "Pagination Stop Reason": metadata.get("playwright_pagination_stop_reason", "-"),
                "Candidate Anchors": metadata.get("parser_candidate_anchor_count", "-"),
                "DOM Listings": metadata.get("parser_dom_listing_count", "-"),
                "Final Parsed Listings": metadata.get("parser_parsed_listing_count", "-"),
                "Source of Truth": metadata.get("parser_source_of_truth", "-"),
                "Scraper Notes": " | ".join([str(note) for note in metadata.get("notes", [])])
                if isinstance(metadata.get("notes"), list)
                else str(metadata.get("notes", "-")),
            }
        )

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------
def prettify_listing_df(df: pd.DataFrame) -> pd.DataFrame:
    display_source = add_listing_display_identity(df)

    display_columns = [
        "open_listing",
        "listing_id",
        "comparison_area",
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
        "comparison_reported_total",
        "comparison_scrape_coverage_ratio",
        "comparison_raw_rendered_reported_ratio",
        "comparison_reported_vs_rendered_status",
        "comparison_coverage_confidence",
        "listing_url",
    ]

    available_columns = [column for column in display_columns if column in display_source.columns]

    display_df = display_source[available_columns].rename(
        columns={
            "open_listing": "Open",
            "listing_id": "Listing ID",
            "comparison_area": "Comparison Area",
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
            "comparison_reported_total": "SPEEDHOME Reported Total",
            "comparison_scrape_coverage_ratio": "Scrape Coverage (%)",
            "comparison_raw_rendered_reported_ratio": "Raw Rendered / Reported Ratio (%)",
            "comparison_reported_vs_rendered_status": "Reported vs Rendered Status",
            "comparison_coverage_confidence": "Coverage Confidence",
            "listing_url": "SPEEDHOME Link",
        }
    )

    text_columns = [
        "Listing ID",
        "Comparison Area",
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
        "Coverage Confidence",
    ]

    for column in text_columns:
        if column in display_df.columns:
            display_df[column] = display_df[column].fillna("Unknown").astype(str)

    if "Furnishing" in display_df.columns:
        display_df["Furnishing"] = display_df["Furnishing"].apply(normalize_furnishing_for_display)

    if "SPEEDHOME Reported Total" in display_df.columns:
        display_df["SPEEDHOME Reported Total"] = display_df["SPEEDHOME Reported Total"].apply(
            reported_total_display
        )

    return display_df


def listing_table_column_config():
    return {
        "Open": st.column_config.LinkColumn("Open", display_text="Open Listing"),
        "SPEEDHOME Link": st.column_config.LinkColumn("SPEEDHOME Link"),
        "Monthly Price (RM)": st.column_config.NumberColumn("Monthly Price (RM)", format="RM %d"),
        "Estimated Yearly Price (RM)": st.column_config.NumberColumn("Estimated Yearly Price (RM)", format="RM %d"),
        "Explicit Yearly Price (RM)": st.column_config.NumberColumn("Explicit Yearly Price (RM)", format="RM %d"),
        "Daily Price (RM)": st.column_config.NumberColumn("Daily Price (RM)", format="RM %d"),
        "Size (sqft)": st.column_config.NumberColumn("Size (sqft)", format="%d"),
        "RM / sqft": st.column_config.NumberColumn("RM / sqft", format="%.2f"),
        "Scrape Coverage (%)": st.column_config.NumberColumn("Scrape Coverage (%)", format="%.2f%%"),
        "Data Completeness (%)": st.column_config.ProgressColumn(
            "Data Completeness (%)", min_value=0, max_value=100, format="%d%%"
        ),
    }


def prettify_segment_summary(summary: pd.DataFrame) -> pd.DataFrame:
    display_df = summary.rename(
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

    if "Unit Type" in display_df.columns:
        display_df["Unit Type"] = display_df["Unit Type"].fillna("Unknown")

    return display_df


def to_excel_bytes(
    comparison_df,
    segment_summary_df,
    sample_confidence_df,
    area_diagnostics_df,
    combined_listing_df,
):
    output = BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        comparison_df.to_excel(writer, index=False, sheet_name="Area Comparison")
        segment_summary_df.to_excel(writer, index=False, sheet_name="Segment Summary")
        sample_confidence_df.to_excel(writer, index=False, sheet_name="Sample Confidence")
        area_diagnostics_df.to_excel(writer, index=False, sheet_name="Area Diagnostics")
        combined_listing_df.to_excel(writer, index=False, sheet_name="Combined Listings")

        methodology = pd.DataFrame(
            [
                {
                    "Topic": "Comparison Source",
                    "Explanation": "This page compares Area A and Area B after each area is scraped explicitly.",
                },
                {
                    "Topic": "Source of Truth",
                    "Explanation": "The comparison uses valid direct listing cards exposed on SPEEDHOME public rendered result pages. Rows must have direct /details/ listing URLs.",
                },
                {
                    "Topic": "Deeper Crawl",
                    "Explanation": "Deeper crawl uses the main scraper behavior, including public pagination/numeric pagination where available. It does not treat noisy SEO/schema/FAQ JSON as listing data.",
                },
                {
                    "Topic": "Coverage",
                    "Explanation": "For large areas, SPEEDHOME may report a higher total result count than the listing cards exposed through public pages. Reported total, collected cards, and coverage are exported transparently.",
                },
                {
                    "Topic": "Input Model",
                    "Explanation": "Area names must be selected from suggestions. Direct SPEEDHOME rent URLs are also supported. Random text is not accepted as a valid market target.",
                },
                {
                    "Topic": "Sample Size Confidence",
                    "Explanation": "Sample confidence is based on collected listing count. Coverage confidence separately explains how much of the SPEEDHOME-reported total was captured.",
                },
                {
                    "Topic": "Estimated Yearly Price",
                    "Explanation": "When explicit yearly rent is not detected, yearly rent is estimated as monthly rent multiplied by 12.",
                },
                {
                    "Topic": "Interpretation",
                    "Explanation": "Comparison results are directional market intelligence based on valid public listing cards collected at scraping time.",
                },
            ]
        )

        methodology.to_excel(writer, index=False, sheet_name="Methodology")

        sheets = {
            "Area Comparison": comparison_df,
            "Segment Summary": segment_summary_df,
            "Sample Confidence": sample_confidence_df,
            "Area Diagnostics": area_diagnostics_df,
            "Combined Listings": combined_listing_df,
            "Methodology": methodology,
        }

        for sheet_name, df in sheets.items():
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes(1, 0)

            for index, column in enumerate(df.columns):
                values = df[column].astype(str).head(100).tolist() if not df.empty else []
                max_value_width = max([len(str(value)) for value in values], default=0)
                width = max(12, min(60, max(len(str(column)) + 4, max_value_width + 2)))
                worksheet.set_column(index, index, width)

    output.seek(0)
    return output.getvalue()


def render_area_card(label: str, result: dict):
    df = result["df"]
    metadata = result["metadata"]
    stats = area_stats(df)
    confidence = get_overall_confidence(df)

    reported_total = result.get("reported_total")
    coverage_ratio = result.get("coverage_ratio")
    coverage_label = result.get("coverage_confidence")
    coverage_note = result.get("coverage_note")

    st.markdown(f"### {label}: {result['area_name']}")
    st.caption(result["source_url"])

    strong_count = int((df.get("target_area_match", pd.Series(dtype=str)).fillna("") == "Strong").sum()) if "target_area_match" in df.columns else 0
    source_page_count = int((df.get("target_area_match", pd.Series(dtype=str)).fillna("") == "Source Page").sum()) if "target_area_match" in df.columns else len(df)
    rendered_status = result.get("reported_vs_rendered_status") or "-"
    raw_ratio = result.get("raw_rendered_reported_ratio")

    render_metric_card_grid(
        [
            ("Collected Cards", stats["Listings"]),
            ("SPEEDHOME Reported", reported_total_display(reported_total)),
            ("Explicit Target Evidence", strong_count),
            ("Sample Confidence", confidence["Confidence"]),
            ("Median Rent", money(stats["Median Monthly Rent (RM)"])),
            ("Coverage", coverage_display_value(coverage_ratio, len(df), reported_total)),
            ("Source Page Cards", source_page_count),
            ("Scrape Time", metadata.get("scrape_duration_label", "-")),
        ],
        min_width_px=145,
        cards_per_row=2,
    )

    st.caption(confidence["Confidence Note"])
    st.caption(f"Reported vs rendered status: {rendered_status}. {coverage_note}")
    st.caption(result["filter_note"])
    st.caption(f"Fetch method: {str(metadata.get('fetch_method', '-')).title()}")
    st.caption(f"Cache used: {'Yes' if metadata.get('cache_used') else 'No'}")
    st.caption(f"Scraped at: {result.get('scraped_at', '-')}")
    st.caption(f"Scrape time: {metadata.get('scrape_duration_label', '-')} | Current run time: {metadata.get('current_run_duration_label', metadata.get('scrape_duration_label', '-'))}")

    if coverage_ratio is not None and coverage_ratio < 40:
        st.warning(
            f"Low coverage for {result['area_name']}: collected {len(df)} valid direct listing card(s) "
            f"from {reported_total} SPEEDHOME-reported target result(s). Treat this as a rendered public-page sample."
        )
    elif reported_total is not None and len(df) > reported_total:
        st.info(
            f"{result['area_name']} rendered more cards than the SPEEDHOME target-count headline. "
            "This is not interpreted as >100% coverage; check Area Match labels for target strength."
        )

    with st.expander(f"{label} scrape diagnostics"):
        st.write(f"Source URL: {result.get('source_url', '-')}")
        st.write("Scrape mode: public rendered cards + public pagination")
        st.write(f"Fetch method: {metadata.get('fetch_method', '-')}")
        st.write(f"Scraped at: {metadata.get('scraped_at', result.get('scraped_at', '-'))}")
        st.write(f"Scrape started at: {metadata.get('scrape_started_at', '-')}")
        st.write(f"Scrape finished at: {metadata.get('scrape_finished_at', '-')}")
        st.write(f"Live scrape duration: {metadata.get('scrape_duration_label', '-')}")
        st.write(f"Current run duration: {metadata.get('current_run_duration_label', metadata.get('scrape_duration_label', '-'))}")
        st.write(f"Cache lookup duration: {metadata.get('cache_lookup_duration_label', '-')}")
        st.write(f"Reported target count: {reported_total_display(reported_total)}")
        st.write(f"Collected cards: {len(df)}")
        st.write(f"Coverage: {coverage_display_value(coverage_ratio, len(df), reported_total)}")
        st.write(f"Raw rendered/reported ratio: {pct(raw_ratio)}")
        st.write(f"Status: {rendered_status}")
        st.write(f"Pages fetched: {metadata.get('playwright_pages_fetched', '-')}")
        st.write(f"Page detail counts: {metadata.get('playwright_page_detail_counts', '-')}")
        st.write(f"Pagination stop reason: {metadata.get('playwright_pagination_stop_reason', '-')}")
        st.write(f"Parser source of truth: {metadata.get('parser_source_of_truth', '-')}")
        st.write(f"Parser final listings: {metadata.get('parser_parsed_listing_count', '-')}")
        st.write(f"Robots allowed: {metadata.get('robots_allowed', '-')}")
        st.write(f"Scraper notes: {metadata.get('notes', [])}")


# ---------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------
def clear_area_a_result():
    st.session_state.pop("comparison_area_a_result", None)
    st.session_state.pop("comparison_area_a_error", None)
    st.session_state["comparison_area_a_scrape_in_progress"] = False
    st.session_state["comparison_area_a_cancel_requested"] = False


def clear_area_b_result():
    st.session_state.pop("comparison_area_b_result", None)
    st.session_state.pop("comparison_area_b_error", None)
    st.session_state["comparison_area_b_scrape_in_progress"] = False
    st.session_state["comparison_area_b_cancel_requested"] = False


def reset_comparison_page():
    st.session_state["comparison_area_a_target"] = "Mont Kiara"
    st.session_state["comparison_area_b_target"] = "Bangsar"
    st.session_state["comparison_area_a_target_is_valid"] = True
    st.session_state["comparison_area_b_target_is_valid"] = True
    # Reset the radio widget safely. Do not set a widget key after the widget
    # has been instantiated in the same Streamlit run. The Reset button only
    # sets a non-widget flag, then this function runs before widgets are created.
    st.session_state.pop("comparison_scrape_depth_choice", None)

    clear_area_a_result()
    clear_area_b_result()

    for key in [
        "comparison_area_a_searchbox",
        "comparison_area_b_searchbox",
        "comparison_area_a_searchbox_fallback",
        "comparison_area_b_searchbox_fallback",
    ]:
        st.session_state.pop(key, None)


def render_fetch_failure(exc):
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


# ---------------------------------------------------------------------
# Session defaults
# ---------------------------------------------------------------------
if "comparison_area_a_target" not in st.session_state:
    st.session_state["comparison_area_a_target"] = "Mont Kiara"

if "comparison_area_b_target" not in st.session_state:
    st.session_state["comparison_area_b_target"] = "Bangsar"

if "comparison_area_a_target_is_valid" not in st.session_state:
    st.session_state["comparison_area_a_target_is_valid"] = True

if "comparison_area_b_target_is_valid" not in st.session_state:
    st.session_state["comparison_area_b_target_is_valid"] = True

for _key in [
    "comparison_area_a_scrape_in_progress",
    "comparison_area_b_scrape_in_progress",
    "comparison_area_a_cancel_requested",
    "comparison_area_b_cancel_requested",
    "comparison_area_a_start_requested",
    "comparison_area_b_start_requested",
]:
    if _key not in st.session_state:
        st.session_state[_key] = False

# Apply pending reset before any widgets are instantiated. This avoids
# StreamlitAPIException for modifying widget-backed session_state keys
# after the radio/searchbox widgets already exist in the current run.
if st.session_state.pop("comparison_reset_requested", False):
    reset_comparison_page()


# ---------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------
with st.sidebar:
    st.header("Comparison Settings")

    st.caption(
        "Scrape mode: public rendered listing cards. The app automatically checks public pagination when available."
    )

    comparison_crawl_mode = "deeper"

    comparison_enrich_missing_details = st.checkbox(
        "Enrich missing furnishing from detail pages",
        value=True,
        help=(
            "Slower but more accurate. Opens accepted SPEEDHOME detail pages when result cards do not expose furnishing/unit details."
        ),
        key="comparison_enrich_missing_details",
    )

    st.info(
        "Use the explicit scrape buttons first. Analytics use all valid direct listing cards collected for each area. "
        "No sidebar filter silently reduces the comparison dataset."
    )

    comparison_scrape_busy = bool(
        st.session_state.get("comparison_area_a_scrape_in_progress")
        or st.session_state.get("comparison_area_b_scrape_in_progress")
    )

    if comparison_scrape_busy:
        st.warning(
            "A comparison scrape is currently running. Cache/reset actions are locked to avoid interrupting the run."
        )

    # Public deployment safety:
    # Cache/history reset is intentionally not exposed in the public app.
    # The deployed assessment version relies on committed fallback datasets, so
    # a random visitor should not be able to delete runtime cache/history files.
    st.caption("Cache reset is disabled in the public deployed version to preserve fallback datasets.")

    if render_confirmed_action_button(
        "Reset Comparison Page",
        "Reset Area A/B selections and clear the current comparison results?",
        "comparison_reset_page",
        confirm_label="Yes, Reset",
        cancel_label="Keep Page",
        disabled=comparison_scrape_busy,
    ):
        # Do not call reset_comparison_page() here because widgets have already
        # been instantiated in this run. Set a non-widget flag and rerun.
        st.session_state["comparison_reset_requested"] = True
        st.rerun()


# ---------------------------------------------------------------------
# Step 1
# ---------------------------------------------------------------------
st.subheader("Step 1 — Choose and Scrape Two Areas")

left_col, right_col = st.columns(2)

with left_col:
    st.markdown("#### Area A")

    area_a_input = render_speedhome_searchbox(
        label="Area A URL / Area / Apartment",
        selected_target_key="comparison_area_a_target",
        searchbox_key="comparison_area_a_searchbox",
        default_value="Mont Kiara",
    )

    area_a_valid = st.session_state.get("comparison_area_a_target_is_valid", False)

    st.caption("Selected Area A target:")
    st.write(area_a_input if area_a_input else "-")

    try:
        st.caption("Target URL:")
        st.caption(normalize_input_to_url(area_a_input))
    except Exception as exc:
        st.warning(str(exc))

    area_a_custom_url_ready = render_custom_url_confirmation("comparison_area_a_target")

    area_a_scrape_busy = bool(st.session_state.get("comparison_area_a_scrape_in_progress"))
    other_scrape_busy = bool(st.session_state.get("comparison_area_b_scrape_in_progress"))

    scrape_area_a_clicked = False

    if area_a_scrape_busy:
        # Cancel is intentionally single-click. A confirmation dialog would also
        # trigger a Streamlit rerun and can make cancellation feel inconsistent.
        if st.button(
            "Cancel Area A Scrape",
            type="primary",
            width="stretch",
            key="comparison_cancel_area_a_scrape_direct",
        ):
            st.session_state["comparison_area_a_cancel_requested"] = True
            st.session_state["comparison_area_a_start_requested"] = False
            st.session_state["comparison_area_a_scrape_in_progress"] = False
            st.warning("Area A scrape cancellation requested.")
            st.rerun()
    else:
        if st.button(
            "Scrape Area A",
            type="primary",
            width="stretch",
            disabled=other_scrape_busy or (not area_a_valid) or (not area_a_custom_url_ready),
        ):
            # First rerun so the Scrape button immediately redraws as Cancel.
            st.session_state["comparison_area_a_scrape_in_progress"] = True
            st.session_state["comparison_area_a_cancel_requested"] = False
            st.session_state["comparison_area_a_start_requested"] = True
            st.rerun()

    clear_a_clicked = st.button(
        "Clear Area A Result",
        width="stretch",
        disabled=area_a_scrape_busy or other_scrape_busy,
    )

    if clear_a_clicked:
        clear_area_a_result()
        st.rerun()

    if st.session_state.pop("comparison_area_a_start_requested", False):
        clear_area_a_result()
        st.session_state["comparison_area_a_scrape_in_progress"] = True

        if st.session_state.get("comparison_area_a_cancel_requested"):
            st.session_state["comparison_area_a_scrape_in_progress"] = False
            st.session_state["comparison_area_a_cancel_requested"] = False
            st.warning("Area A scrape cancelled before it started.")
        elif not area_a_valid:
            st.session_state["comparison_area_a_scrape_in_progress"] = False
            st.warning("Please choose a valid Area A suggestion or paste a supported SPEEDHOME rent URL.")
        elif not area_a_custom_url_ready:
            st.session_state["comparison_area_a_scrape_in_progress"] = False
            st.warning("Please confirm the custom SPEEDHOME rent URL for Area A before scraping.")
        elif not area_a_input or not str(area_a_input).strip():
            st.session_state["comparison_area_a_scrape_in_progress"] = False
            st.warning("Area A input cannot be empty.")
        else:
            try:
                with st.spinner("Scraping Area A from SPEEDHOME..."):
                    result_a = scrape_area_for_comparison(
                        area_a_input,
                        crawl_mode=comparison_crawl_mode,
                        enrich_missing_details=comparison_enrich_missing_details,
                    )

                st.session_state["comparison_area_a_result"] = result_a
                st.success(f"Area A scraped successfully: {result_a['area_name']}")

            except SpeedhomeFetchError as exc:
                st.session_state["comparison_area_a_error"] = str(exc)
                render_fetch_failure(exc)

            except Exception as exc:
                st.session_state["comparison_area_a_error"] = str(exc)
                st.error(str(exc))

            finally:
                st.session_state["comparison_area_a_scrape_in_progress"] = False
                st.rerun()

    result_a = st.session_state.get("comparison_area_a_result")
    error_a = st.session_state.get("comparison_area_a_error")

    if result_a:
        if is_result_stale(area_a_input, comparison_crawl_mode, result_a):
            st.warning(
                f"Area A input/depth changed. Current stored result is still for "
                f"'{result_a['input_value']}' using '{result_a.get('crawl_mode', '-')}' mode. "
                "Click Scrape Area A to refresh."
            )
        else:
            render_area_card("Area A", result_a)
    elif error_a:
        st.error(error_a)
    else:
        st.info("Click Scrape Area A to collect data for the first area.")


with right_col:
    st.markdown("#### Area B")

    area_b_input = render_speedhome_searchbox(
        label="Area B URL / Area / Apartment",
        selected_target_key="comparison_area_b_target",
        searchbox_key="comparison_area_b_searchbox",
        default_value="Bangsar",
    )

    area_b_valid = st.session_state.get("comparison_area_b_target_is_valid", False)

    st.caption("Selected Area B target:")
    st.write(area_b_input if area_b_input else "-")

    try:
        st.caption("Target URL:")
        st.caption(normalize_input_to_url(area_b_input))
    except Exception as exc:
        st.warning(str(exc))

    area_b_custom_url_ready = render_custom_url_confirmation("comparison_area_b_target")

    area_b_scrape_busy = bool(st.session_state.get("comparison_area_b_scrape_in_progress"))
    other_scrape_busy = bool(st.session_state.get("comparison_area_a_scrape_in_progress"))

    scrape_area_b_clicked = False

    if area_b_scrape_busy:
        # Cancel is intentionally single-click. A confirmation dialog would also
        # trigger a Streamlit rerun and can make cancellation feel inconsistent.
        if st.button(
            "Cancel Area B Scrape",
            type="primary",
            width="stretch",
            key="comparison_cancel_area_b_scrape_direct",
        ):
            st.session_state["comparison_area_b_cancel_requested"] = True
            st.session_state["comparison_area_b_start_requested"] = False
            st.session_state["comparison_area_b_scrape_in_progress"] = False
            st.warning("Area B scrape cancellation requested.")
            st.rerun()
    else:
        if st.button(
            "Scrape Area B",
            type="primary",
            width="stretch",
            disabled=other_scrape_busy or (not area_b_valid) or (not area_b_custom_url_ready),
        ):
            # First rerun so the Scrape button immediately redraws as Cancel.
            st.session_state["comparison_area_b_scrape_in_progress"] = True
            st.session_state["comparison_area_b_cancel_requested"] = False
            st.session_state["comparison_area_b_start_requested"] = True
            st.rerun()

    clear_b_clicked = st.button(
        "Clear Area B Result",
        width="stretch",
        disabled=area_b_scrape_busy or other_scrape_busy,
    )

    if clear_b_clicked:
        clear_area_b_result()
        st.rerun()

    if st.session_state.pop("comparison_area_b_start_requested", False):
        clear_area_b_result()
        st.session_state["comparison_area_b_scrape_in_progress"] = True

        if st.session_state.get("comparison_area_b_cancel_requested"):
            st.session_state["comparison_area_b_scrape_in_progress"] = False
            st.session_state["comparison_area_b_cancel_requested"] = False
            st.warning("Area B scrape cancelled before it started.")
        elif not area_b_valid:
            st.session_state["comparison_area_b_scrape_in_progress"] = False
            st.warning("Please choose a valid Area B suggestion or paste a supported SPEEDHOME rent URL.")
        elif not area_b_custom_url_ready:
            st.session_state["comparison_area_b_scrape_in_progress"] = False
            st.warning("Please confirm the custom SPEEDHOME rent URL for Area B before scraping.")
        elif not area_b_input or not str(area_b_input).strip():
            st.session_state["comparison_area_b_scrape_in_progress"] = False
            st.warning("Area B input cannot be empty.")
        else:
            try:
                with st.spinner("Scraping Area B from SPEEDHOME..."):
                    result_b = scrape_area_for_comparison(
                        area_b_input,
                        crawl_mode=comparison_crawl_mode,
                        enrich_missing_details=comparison_enrich_missing_details,
                    )

                st.session_state["comparison_area_b_result"] = result_b
                st.success(f"Area B scraped successfully: {result_b['area_name']}")

            except SpeedhomeFetchError as exc:
                st.session_state["comparison_area_b_error"] = str(exc)
                render_fetch_failure(exc)

            except Exception as exc:
                st.session_state["comparison_area_b_error"] = str(exc)
                st.error(str(exc))

            finally:
                st.session_state["comparison_area_b_scrape_in_progress"] = False
                st.rerun()

    result_b = st.session_state.get("comparison_area_b_result")
    error_b = st.session_state.get("comparison_area_b_error")

    if result_b:
        if is_result_stale(area_b_input, comparison_crawl_mode, result_b):
            st.warning(
                f"Area B input/depth changed. Current stored result is still for "
                f"'{result_b['input_value']}' using '{result_b.get('crawl_mode', '-')}' mode. "
                "Click Scrape Area B to refresh."
            )
        else:
            render_area_card("Area B", result_b)
    elif error_b:
        st.error(error_b)
    else:
        st.info("Click Scrape Area B to collect data for the second area.")


# ---------------------------------------------------------------------
# Step 2
# ---------------------------------------------------------------------
result_a = st.session_state.get("comparison_area_a_result")
result_b = st.session_state.get("comparison_area_b_result")

area_a_stale = bool(result_a and is_result_stale(area_a_input, comparison_crawl_mode, result_a))
area_b_stale = bool(result_b and is_result_stale(area_b_input, comparison_crawl_mode, result_b))

st.divider()
st.subheader("Step 2 — Compare Scraped Results")

if area_a_stale or area_b_stale:
    st.warning("One or both area inputs or scrape-depth settings changed after scraping. Re-scrape the changed area before comparing.")
    st.stop()

if not result_a or not result_b:
    st.info("Scrape both Area A and Area B first. Comparison appears after both sides have data.")
    st.stop()

try:
    if result_a["source_url"] == result_b["source_url"]:
        st.warning("Area A and Area B point to the same SPEEDHOME URL. Use two different areas for comparison.")
        st.stop()
except Exception:
    pass


df_a = result_a["df"]
df_b = result_b["df"]

area_a_name = result_a["area_name"]
area_b_name = result_b["area_name"]

stats_a = area_stats(df_a)
stats_b = area_stats(df_b)

confidence_a = get_overall_confidence(df_a)
confidence_b = get_overall_confidence(df_b)

comparison_rows = []

for metric in [
    "Listings",
    "Unit Segments",
    "Median Monthly Rent (RM)",
    "Average Monthly Rent (RM)",
    "Lowest Monthly Rent (RM)",
    "Highest Monthly Rent (RM)",
    "Average Size (sqft)",
    "Average RM / sqft",
    "Best RM / sqft",
    "Average Data Completeness (%)",
]:
    a_value = stats_a.get(metric)
    b_value = stats_b.get(metric)

    if a_value is not None and b_value is not None and pd.notna(a_value) and pd.notna(b_value):
        difference = b_value - a_value
    else:
        difference = None

    comparison_rows.append(
        {
            "Metric": metric,
            area_a_name: a_value,
            area_b_name: b_value,
            "Difference (B - A)": difference,
        }
    )

for metric, a_value, b_value in [
    ("SPEEDHOME Reported Total", reported_total_display(result_a.get("reported_total")), reported_total_display(result_b.get("reported_total"))),
    (
        "Collected / Reported Coverage",
        coverage_display_value(result_a.get("coverage_ratio"), len(df_a), result_a.get("reported_total")),
        coverage_display_value(result_b.get("coverage_ratio"), len(df_b), result_b.get("reported_total")),
    ),
    ("Raw Rendered / Reported Ratio (%)", result_a.get("raw_rendered_reported_ratio"), result_b.get("raw_rendered_reported_ratio")),
    ("Reported vs Rendered Status", result_a.get("reported_vs_rendered_status"), result_b.get("reported_vs_rendered_status")),
    ("Coverage Confidence", result_a.get("coverage_confidence"), result_b.get("coverage_confidence")),
    ("Crawl Mode", result_a.get("crawl_mode"), result_b.get("crawl_mode")),
    ("Sample Size Confidence", confidence_a["Confidence"], confidence_b["Confidence"]),
    ("Sample Confidence Note", confidence_a["Confidence Note"], confidence_b["Confidence Note"]),
    ("Coverage Note", result_a.get("coverage_note"), result_b.get("coverage_note")),
]:
    comparison_rows.append(
        {
            "Metric": metric,
            area_a_name: a_value,
            area_b_name: b_value,
            "Difference (B - A)": "-",
        }
    )

comparison_df = pd.DataFrame(comparison_rows)
comparison_display_df = comparison_df.astype(str)

st.success(f"Comparing {area_a_name} vs {area_b_name} using all collected valid direct listing cards.")

low_coverage_areas = [
    result["area_name"]
    for result in [result_a, result_b]
    if result.get("coverage_ratio") is not None and result.get("coverage_ratio") < 40
]

if low_coverage_areas:
    st.warning(
        "Low coverage detected for: "
        + ", ".join(low_coverage_areas)
        + ". Treat those areas as public rendered-page samples, not full SPEEDHOME inventory."
    )

st.subheader("Quick Verdict")

median_a = stats_a["Median Monthly Rent (RM)"]
median_b = stats_b["Median Monthly Rent (RM)"]
best_sqft_a = stats_a["Best RM / sqft"]
best_sqft_b = stats_b["Best RM / sqft"]

verdict_items = []

if median_a is not None and median_b is not None and pd.notna(median_a) and pd.notna(median_b):
    cheaper_area = area_a_name if median_a < median_b else area_b_name
    cheaper_value = min(median_a, median_b)
    verdict_items.append(("Lower Median Rent", cheaper_area, money(cheaper_value)))
else:
    verdict_items.append(("Lower Median Rent", "N/A", ""))

if best_sqft_a is not None and best_sqft_b is not None and pd.notna(best_sqft_a) and pd.notna(best_sqft_b):
    better_value_area = area_a_name if best_sqft_a < best_sqft_b else area_b_name
    better_value = min(best_sqft_a, best_sqft_b)
    verdict_items.append(("Better RM / sqft", better_value_area, f"RM {better_value:.2f}"))
else:
    verdict_items.append(("Better RM / sqft", "N/A", ""))

listings_area = area_a_name if stats_a["Listings"] > stats_b["Listings"] else area_b_name
listings_value = max(stats_a["Listings"], stats_b["Listings"])
verdict_items.append(("More Collected Cards", listings_area, f"{int(listings_value)} cards"))

coverage_a = result_a.get("coverage_ratio")
coverage_b = result_b.get("coverage_ratio")

if coverage_a is not None and coverage_b is not None:
    coverage_winner = area_a_name if coverage_a > coverage_b else area_b_name
    verdict_items.append(
        (
            "Higher Coverage",
            coverage_winner,
            f"{area_a_name}: {pct(coverage_a)} | {area_b_name}: {pct(coverage_b)}",
        )
    )
else:
    verdict_items.append(
        (
            "Higher Coverage",
            "Not comparable",
            "Coverage is not comparable when the reported target count is not exposed or rendered cards exceed the reported target count.",
        )
    )

render_metric_card_grid(verdict_items, min_width_px=190, cards_per_row=3)


st.subheader("Side-by-Side Comparison Table")
st.dataframe(comparison_display_df, width="stretch", hide_index=True)


st.subheader("Sample Size Confidence")
sample_confidence_df = create_comparison_sample_confidence(
    area_a_name=area_a_name,
    df_a=df_a,
    area_b_name=area_b_name,
    df_b=df_b,
)

overall_sample_confidence = sample_confidence_df[sample_confidence_df["Scope"] == "Overall"]
st.dataframe(overall_sample_confidence, width="stretch", hide_index=True)

with st.expander("View sample confidence by unit segment"):
    st.dataframe(sample_confidence_df, width="stretch", hide_index=True)


st.subheader("Coverage Diagnostics")
area_diagnostics_df = create_area_diagnostics([("Area A", result_a), ("Area B", result_b)])
st.dataframe(area_diagnostics_df, width="stretch", hide_index=True)


st.subheader("Comparison Charts")
chart_data = pd.DataFrame(
    [
        {
            "Area": area_a_name,
            "Median Monthly Rent (RM)": stats_a["Median Monthly Rent (RM)"],
            "Average Monthly Rent (RM)": stats_a["Average Monthly Rent (RM)"],
            "Best RM / sqft": stats_a["Best RM / sqft"],
            "Collected Cards": stats_a["Listings"],
            "Coverage (%)": result_a.get("coverage_ratio"),
            "Raw Rendered / Reported Ratio (%)": result_a.get("raw_rendered_reported_ratio"),
        },
        {
            "Area": area_b_name,
            "Median Monthly Rent (RM)": stats_b["Median Monthly Rent (RM)"],
            "Average Monthly Rent (RM)": stats_b["Average Monthly Rent (RM)"],
            "Best RM / sqft": stats_b["Best RM / sqft"],
            "Collected Cards": stats_b["Listings"],
            "Coverage (%)": result_b.get("coverage_ratio"),
            "Raw Rendered / Reported Ratio (%)": result_b.get("raw_rendered_reported_ratio"),
        },
    ]
)

chart_col_1, chart_col_2 = st.columns(2)

with chart_col_1:
    fig_rent = px.bar(
        chart_data,
        x="Area",
        y="Median Monthly Rent (RM)",
        text="Median Monthly Rent (RM)",
        title="Median Monthly Rent",
    )
    st.plotly_chart(fig_rent, width="stretch")

with chart_col_2:
    fig_value = px.bar(
        chart_data,
        x="Area",
        y="Best RM / sqft",
        text="Best RM / sqft",
        title="Best RM / sqft",
    )
    st.plotly_chart(fig_value, width="stretch")

chart_col_3, chart_col_4 = st.columns(2)

with chart_col_3:
    fig_cards = px.bar(
        chart_data,
        x="Area",
        y="Collected Cards",
        text="Collected Cards",
        title="Collected Direct Listing Cards",
    )
    st.plotly_chart(fig_cards, width="stretch")

with chart_col_4:
    fig_coverage = px.bar(
        chart_data,
        x="Area",
        y="Coverage (%)",
        text="Coverage (%)",
        title="Comparable Coverage vs SPEEDHOME Reported Target",
    )
    st.plotly_chart(fig_coverage, width="stretch")


st.subheader("Segment Summary")
summary_a = create_price_summary(df_a)
summary_a["Area"] = area_a_name

summary_b = create_price_summary(df_b)
summary_b["Area"] = area_b_name

segment_summary = pd.concat([summary_a, summary_b], ignore_index=True)
display_segment_summary = prettify_segment_summary(segment_summary)

st.dataframe(display_segment_summary, width="stretch", hide_index=True)


st.subheader("Listings Side by Side")
st.caption(
    "The Open and Listing ID columns are placed first. Both tables show all valid direct listing cards collected for each source page."
)

list_col_a, list_col_b = st.columns(2)

with list_col_a:
    st.markdown(f"#### {area_a_name} Listings")
    display_a = prettify_listing_df(df_a)
    st.dataframe(display_a, width="stretch", hide_index=True, column_config=listing_table_column_config())

with list_col_b:
    st.markdown(f"#### {area_b_name} Listings")
    display_b = prettify_listing_df(df_b)
    st.dataframe(display_b, width="stretch", hide_index=True, column_config=listing_table_column_config())


st.subheader("Download Comparison Data")
combined_listing_df = pd.concat([display_a, display_b], ignore_index=True)

csv_data = comparison_display_df.to_csv(index=False).encode("utf-8")
excel_data = to_excel_bytes(
    comparison_df=comparison_display_df,
    segment_summary_df=display_segment_summary,
    sample_confidence_df=sample_confidence_df,
    area_diagnostics_df=area_diagnostics_df,
    combined_listing_df=combined_listing_df,
)

download_col_1, download_col_2 = st.columns(2)

with download_col_1:
    st.download_button(
        "Download Comparison CSV",
        data=csv_data,
        file_name=make_comparison_filename("csv"),
        mime="text/csv",
        width="stretch",
    )

with download_col_2:
    st.download_button(
        "Download Comparison Excel",
        data=excel_data,
        file_name=make_comparison_filename("xlsx"),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
