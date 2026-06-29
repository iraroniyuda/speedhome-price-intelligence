from io import BytesIO
from pathlib import Path
from datetime import datetime
import json

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    from ui_style import inject_global_css
except Exception:
    def inject_global_css():
        return None


st.set_page_config(
    page_title="ROI Calculator - SPEEDHOME Intelligence",
    layout="wide",
)

inject_global_css()

st.title("ROI Calculator")
st.caption("Directional rental investment calculator based on SPEEDHOME market rent intelligence.")

st.divider()


HISTORY_FILE = Path("data") / "scrape_history.json"


# ---------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------
def money(value):
    if value is None:
        return "N/A"

    try:
        if pd.isna(value):
            return "N/A"
    except Exception:
        pass

    try:
        return f"RM {float(value):,.0f}"
    except Exception:
        return "N/A"


def percent(value):
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


def money_table(value, decimals: int = 2):
    """Human-friendly money formatting for tables and exports.

    Metric cards intentionally use whole RM values through money(). Tables keep
    two decimals when needed so values sourced from fair-rent calculations remain
    transparent without showing long floating-point numbers.
    """
    if value is None:
        return "N/A"

    try:
        if pd.isna(value):
            return "N/A"
    except Exception:
        pass

    try:
        return f"RM {float(value):,.{decimals}f}"
    except Exception:
        return str(value)


def number_table(value, decimals: int = 2):
    if value is None:
        return "N/A"

    try:
        if pd.isna(value):
            return "N/A"
    except Exception:
        pass

    try:
        return f"{float(value):,.{decimals}f}"
    except Exception:
        return str(value)


def display_value_by_label(label, value):
    """Format mixed metric/assumption tables without long decimals.

    The ROI summary table contains RM values, percentage values, DSCR, plain
    counts, and text in a single Value column. Streamlit cannot infer the correct
    number format per row, so row-aware formatting keeps the output reviewer-
    friendly.
    """
    label_text = str(label or "").strip()
    label_lower = label_text.lower()

    if value is None:
        return "N/A"

    try:
        if pd.isna(value):
            return "N/A"
    except Exception:
        pass

    # Keep notes and source labels readable.
    if isinstance(value, str):
        return value

    if "dscr" == label_lower or label_lower.endswith("dscr"):
        try:
            return f"{float(value):.2f}x"
        except Exception:
            return str(value)

    if "(%)" in label_lower or "yield" in label_lower or "return" in label_lower or "rate" in label_lower:
        return percent(value)

    if "years" in label_lower or label_lower == "projection period":
        try:
            return f"{int(round(float(value))):,}"
        except Exception:
            return str(value)

    if (
        "(rm)" in label_lower
        or "rent" in label_lower
        or "price" in label_lower
        or "payment" in label_lower
        or "cost" in label_lower
        or "cash" in label_lower
        or "income" in label_lower
        or "amount" in label_lower
    ):
        return money_table(value, decimals=2)

    return number_table(value, decimals=2)


def format_value_table_for_display(df: pd.DataFrame, label_column: str) -> pd.DataFrame:
    """Return a copy with the Value column formatted per row."""
    if df is None or df.empty or "Value" not in df.columns or label_column not in df.columns:
        return df.copy() if df is not None else pd.DataFrame()

    display_df = df.copy()
    display_df["Value"] = [
        display_value_by_label(label, value)
        for label, value in zip(display_df[label_column], display_df["Value"])
    ]
    return display_df


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if pd.isna(value):
            return default

        return float(value)
    except Exception:
        return default


def safe_excel_text(value):
    """
    Convert any value safely into text for Excel column width calculation.
    This prevents TypeError when values are int, float, None, NaN, Timestamp, etc.
    """
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return str(value)


def safe_dataframe_for_excel(df):
    """
    Ensure dataframe is safe for Excel export without destroying numeric display too much.
    Only replaces problematic missing values.
    """
    if df is None:
        return pd.DataFrame()

    if df.empty:
        return df.copy()

    safe_df = df.copy()
    safe_df = safe_df.where(pd.notna(safe_df), "")

    return safe_df


# ---------------------------------------------------------------------
# Finance helpers
# ---------------------------------------------------------------------
def monthly_loan_payment(loan_amount, annual_interest_rate, tenure_years):
    loan_amount = safe_float(loan_amount)
    annual_interest_rate = safe_float(annual_interest_rate)
    tenure_years = safe_float(tenure_years)

    if loan_amount <= 0 or tenure_years <= 0:
        return 0.0

    number_of_months = int(tenure_years * 12)

    if number_of_months <= 0:
        return 0.0

    monthly_rate = annual_interest_rate / 100 / 12

    if monthly_rate <= 0:
        return loan_amount / number_of_months

    payment = loan_amount * monthly_rate * ((1 + monthly_rate) ** number_of_months)
    payment = payment / (((1 + monthly_rate) ** number_of_months) - 1)

    return float(payment)


def make_roi_projection(
    monthly_rent,
    purchase_price,
    occupancy_rate,
    rent_growth_rate,
    cost_growth_rate,
    monthly_operating_cost,
    monthly_installment,
    holding_years,
):
    rows = []
    cumulative_cashflow = 0.0

    holding_years = int(holding_years or 1)

    for year in range(1, holding_years + 1):
        rent_multiplier = (1 + rent_growth_rate / 100) ** (year - 1)
        cost_multiplier = (1 + cost_growth_rate / 100) ** (year - 1)

        projected_monthly_rent = monthly_rent * rent_multiplier
        effective_monthly_rent = projected_monthly_rent * (occupancy_rate / 100)
        annual_gross_rent = effective_monthly_rent * 12

        annual_operating_cost = monthly_operating_cost * cost_multiplier * 12
        annual_loan_payment = monthly_installment * 12

        net_operating_income = annual_gross_rent - annual_operating_cost
        annual_cashflow = net_operating_income - annual_loan_payment
        cumulative_cashflow += annual_cashflow

        gross_yield = (annual_gross_rent / purchase_price * 100) if purchase_price > 0 else 0
        net_yield = (net_operating_income / purchase_price * 100) if purchase_price > 0 else 0

        rows.append(
            {
                "Year": year,
                "Projected Monthly Rent (RM)": projected_monthly_rent,
                "Effective Monthly Rent (RM)": effective_monthly_rent,
                "Annual Gross Rent (RM)": annual_gross_rent,
                "Annual Operating Cost (RM)": annual_operating_cost,
                "Annual Loan Payment (RM)": annual_loan_payment,
                "Net Operating Income (RM)": net_operating_income,
                "Annual Cashflow (RM)": annual_cashflow,
                "Cumulative Cashflow (RM)": cumulative_cashflow,
                "Gross Yield (%)": gross_yield,
                "Net Yield (%)": net_yield,
            }
        )

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------
def load_history_records():
    if not HISTORY_FILE.exists():
        return []

    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ["datasets", "history", "records", "items"]:
            if isinstance(data.get(key), list):
                return data[key]

        records = []

        for value in data.values():
            if isinstance(value, dict):
                records.append(value)

        return records

    return []


def dataframe_from_history_record(record):
    if not isinstance(record, dict):
        return pd.DataFrame()

    possible_keys = [
        "records",
        "rows",
        "listings",
        "data",
        "df",
        "items",
    ]

    for key in possible_keys:
        value = record.get(key)

        if isinstance(value, list):
            try:
                df = pd.DataFrame(value)

                if not df.empty:
                    return df
            except Exception:
                pass

    return pd.DataFrame()


def history_record_label(record, index):
    if not isinstance(record, dict):
        return f"Dataset {index + 1}"

    metadata = record.get("metadata", {}) if isinstance(record.get("metadata", {}), dict) else {}

    target_area = (
        record.get("target_area")
        or record.get("area_name")
        or record.get("source_area")
        or record.get("area")
        or f"Dataset {index + 1}"
    )

    scraped_at = (
        record.get("scraped_at")
        or record.get("created_at")
        or record.get("timestamp")
        or metadata.get("scraped_at")
        or metadata.get("scrape_started_at")
        or ""
    )

    filtered_count = (
        record.get("filtered_count")
        or record.get("listing_count")
        or record.get("raw_count")
        or "-"
    )

    label_parts = [f"{target_area}", f"{filtered_count} listing(s)"]

    # Show only the date to keep the dropdown compact. If the history record has
    # no timestamp, do not append a meaningless trailing '| -'.
    if scraped_at:
        label_parts.append(str(scraped_at)[:10])

    return " | ".join(label_parts)


def get_market_rent_defaults(df):
    if df.empty or "monthly_price_rm" not in df.columns:
        return {
            "median_rent": 2500.0,
            "average_rent": 2500.0,
            "fair_rent": 2500.0,
            "listing_count": 0,
        }

    monthly = pd.to_numeric(df["monthly_price_rm"], errors="coerce").dropna()

    if monthly.empty:
        return {
            "median_rent": 2500.0,
            "average_rent": 2500.0,
            "fair_rent": 2500.0,
            "listing_count": len(df),
        }

    if len(monthly) >= 4:
        q1 = monthly.quantile(0.25)
        q3 = monthly.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr

        trimmed = monthly[(monthly >= lower) & (monthly <= upper)]

        if trimmed.empty:
            trimmed = monthly

        fair_rent = (trimmed.median() + trimmed.mean()) / 2
    else:
        fair_rent = monthly.median()

    return {
        "median_rent": float(monthly.median()),
        "average_rent": float(monthly.mean()),
        "fair_rent": float(fair_rent),
        "listing_count": len(monthly),
    }


def sample_confidence_label(listing_count):
    listing_count = int(listing_count or 0)

    if listing_count <= 0:
        return "No sample"

    if listing_count < 5:
        return "Low"

    if listing_count < 15:
        return "Medium"

    return "High"


# ---------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------
def to_excel_bytes(
    assumption_df,
    result_df,
    projection_df,
    selected_listing_df,
):
    output = BytesIO()

    assumption_df = safe_dataframe_for_excel(assumption_df)
    result_df = safe_dataframe_for_excel(result_df)
    projection_df = safe_dataframe_for_excel(projection_df)
    selected_listing_df = safe_dataframe_for_excel(selected_listing_df)

    assumption_export_df = format_value_table_for_display(assumption_df, "Assumption")
    result_export_df = format_value_table_for_display(result_df, "Metric")

    methodology = pd.DataFrame(
        [
            {
                "Topic": "Purpose",
                "Explanation": "This calculator estimates directional rental investment performance from public rental market data and manual property assumptions.",
            },
            {
                "Topic": "Gross Yield",
                "Explanation": "Annual effective rent divided by purchase price.",
            },
            {
                "Topic": "Net Yield",
                "Explanation": "Annual rent after operating costs divided by purchase price. Loan payment is excluded from net yield.",
            },
            {
                "Topic": "Cash-on-Cash Return",
                "Explanation": "Annual cashflow after loan payment divided by estimated initial cash required.",
            },
            {
                "Topic": "DSCR",
                "Explanation": "Debt Service Coverage Ratio = Net Operating Income divided by annual loan payment.",
            },
            {
                "Topic": "Break-even Rent",
                "Explanation": "Monthly rent required to cover monthly installment and operating costs after occupancy adjustment.",
            },
            {
                "Topic": "Interpretation",
                "Explanation": "This is a directional model, not financial advice or a formal valuation.",
            },
        ]
    )

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        assumption_export_df.to_excel(writer, index=False, sheet_name="Assumptions")
        result_export_df.to_excel(writer, index=False, sheet_name="ROI Summary")
        projection_df.to_excel(writer, index=False, sheet_name="Projection")

        if selected_listing_df is not None and not selected_listing_df.empty:
            selected_listing_df.to_excel(writer, index=False, sheet_name="Source Listings")

        methodology.to_excel(writer, index=False, sheet_name="Methodology")

        workbook = writer.book

        header_format = workbook.add_format(
            {
                "bold": True,
                "bg_color": "#D9EAF7",
                "border": 1,
            }
        )

        money_format = workbook.add_format(
            {
                "num_format": 'RM #,##0',
            }
        )

        percent_format = workbook.add_format(
            {
                "num_format": "0.00%",
            }
        )

        decimal_format = workbook.add_format(
            {
                "num_format": "0.00",
            }
        )

        all_sheets = {
            "Assumptions": assumption_export_df,
            "ROI Summary": result_export_df,
            "Projection": projection_df,
            "Source Listings": selected_listing_df if selected_listing_df is not None else pd.DataFrame(),
            "Methodology": methodology,
        }

        for sheet_name, worksheet in writer.sheets.items():
            worksheet.freeze_panes(1, 0)

            df_for_sheet = all_sheets.get(sheet_name, pd.DataFrame())

            if df_for_sheet is None or df_for_sheet.empty:
                continue

            for col_index, column_name in enumerate(df_for_sheet.columns):
                worksheet.write(0, col_index, column_name, header_format)

            for col_index, column_name in enumerate(df_for_sheet.columns):
                column_values = df_for_sheet[column_name].head(100).tolist()

                max_value_width = max(
                    [len(safe_excel_text(value)) for value in column_values],
                    default=0,
                )

                width = max(
                    12,
                    min(
                        60,
                        max(
                            len(safe_excel_text(column_name)) + 4,
                            max_value_width + 2,
                        ),
                    ),
                )

                column_name_lower = str(column_name).lower()

                if "(rm)" in column_name_lower or "price" in column_name_lower or "rent" in column_name_lower or "cashflow" in column_name_lower or "cost" in column_name_lower or "payment" in column_name_lower or "income" in column_name_lower:
                    worksheet.set_column(col_index, col_index, width, money_format)
                elif "(%)" in column_name_lower or "yield" in column_name_lower or "return" in column_name_lower:
                    worksheet.set_column(col_index, col_index, width, decimal_format)
                else:
                    worksheet.set_column(col_index, col_index, width)

    output.seek(0)
    return output.getvalue()


# ---------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------
def format_projection_for_display(df):
    display_df = df.copy()

    money_columns = [
        "Projected Monthly Rent (RM)",
        "Effective Monthly Rent (RM)",
        "Annual Gross Rent (RM)",
        "Annual Operating Cost (RM)",
        "Annual Loan Payment (RM)",
        "Net Operating Income (RM)",
        "Annual Cashflow (RM)",
        "Cumulative Cashflow (RM)",
    ]

    percent_columns = [
        "Gross Yield (%)",
        "Net Yield (%)",
    ]

    for column in money_columns:
        if column in display_df.columns:
            display_df[column] = pd.to_numeric(display_df[column], errors="coerce").round(0)

    for column in percent_columns:
        if column in display_df.columns:
            display_df[column] = pd.to_numeric(display_df[column], errors="coerce").round(2)

    return display_df


def projection_column_config():
    """Consistent table formatting for the projection grid."""
    return {
        "Year": st.column_config.NumberColumn("Year", format="%d"),
        "Projected Monthly Rent (RM)": st.column_config.NumberColumn("Projected Monthly Rent (RM)", format="RM %.0f"),
        "Effective Monthly Rent (RM)": st.column_config.NumberColumn("Effective Monthly Rent (RM)", format="RM %.0f"),
        "Annual Gross Rent (RM)": st.column_config.NumberColumn("Annual Gross Rent (RM)", format="RM %.0f"),
        "Annual Operating Cost (RM)": st.column_config.NumberColumn("Annual Operating Cost (RM)", format="RM %.0f"),
        "Annual Loan Payment (RM)": st.column_config.NumberColumn("Annual Loan Payment (RM)", format="RM %.0f"),
        "Net Operating Income (RM)": st.column_config.NumberColumn("Net Operating Income (RM)", format="RM %.0f"),
        "Annual Cashflow (RM)": st.column_config.NumberColumn("Annual Cashflow (RM)", format="RM %.0f"),
        "Cumulative Cashflow (RM)": st.column_config.NumberColumn("Cumulative Cashflow (RM)", format="RM %.0f"),
        "Gross Yield (%)": st.column_config.NumberColumn("Gross Yield (%)", format="%.2f%%"),
        "Net Yield (%)": st.column_config.NumberColumn("Net Yield (%)", format="%.2f%%"),
    }


def selected_source_listing_table(df):
    if df.empty:
        return pd.DataFrame()

    display_columns = [
        "title",
        "property_area",
        "bedroom_type",
        "monthly_price_rm",
        "estimated_yearly_price_rm",
        "size_sqft",
        "price_per_sqft_rm",
        "furnishing",
        "listing_url",
    ]

    available_columns = [
        column
        for column in display_columns
        if column in df.columns
    ]

    if not available_columns:
        return pd.DataFrame()

    display_df = df[available_columns].copy()

    display_df = display_df.rename(
        columns={
            "title": "Listing Title",
            "property_area": "Property / Area",
            "bedroom_type": "Unit Type",
            "monthly_price_rm": "Monthly Price (RM)",
            "estimated_yearly_price_rm": "Estimated Yearly Price (RM)",
            "size_sqft": "Size (sqft)",
            "price_per_sqft_rm": "RM / sqft",
            "furnishing": "Furnishing",
            "listing_url": "SPEEDHOME Link",
        }
    )

    return display_df


# ---------------------------------------------------------------------
# Page content
# ---------------------------------------------------------------------
history_records = load_history_records()

st.info(
    "This page is a decision-support calculator. It uses market rent as input, then estimates yield, cashflow, DSCR, and break-even rent. "
    "It is directional market intelligence, not formal financial advice."
)

st.subheader("1 — Rent Source")

source_mode = st.radio(
    "Choose rent input source",
    [
        "Use manual rent input",
        "Use latest scraped market data if available",
    ],
    horizontal=True,
)

selected_history_df = pd.DataFrame()
selected_history_label = "Manual input"

market_defaults = {
    "median_rent": 2500.0,
    "average_rent": 2500.0,
    "fair_rent": 2500.0,
    "listing_count": 0,
}

if source_mode == "Use latest scraped market data if available":
    if not history_records:
        st.warning("No scrape history found yet. Run the main dashboard or comparison page first, then return here.")
    else:
        labels = [
            history_record_label(record, index)
            for index, record in enumerate(history_records)
        ]

        selected_label = st.selectbox(
            "Select scraped dataset",
            labels,
        )

        selected_index = labels.index(selected_label)
        selected_record = history_records[selected_index]

        selected_history_label = selected_label
        selected_history_df = dataframe_from_history_record(selected_record)
        market_defaults = get_market_rent_defaults(selected_history_df)

        source_col_1, source_col_2, source_col_3, source_col_4 = st.columns(4)

        with source_col_1:
            st.metric("Detected Listings", market_defaults["listing_count"])

        with source_col_2:
            st.metric("Sample Confidence", sample_confidence_label(market_defaults["listing_count"]))

        with source_col_3:
            st.metric("Median Rent", money(market_defaults["median_rent"]))

        with source_col_4:
            st.metric("Fair Rent", money(market_defaults["fair_rent"]))

        with st.expander("View source listings used for rent reference"):
            source_listing_display = selected_source_listing_table(selected_history_df)

            if source_listing_display.empty:
                st.info("No source listing table available in the selected history dataset.")
            else:
                st.dataframe(
                    source_listing_display,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "SPEEDHOME Link": st.column_config.LinkColumn("SPEEDHOME Link"),
                        "Monthly Price (RM)": st.column_config.NumberColumn(
                            "Monthly Price (RM)",
                            format="RM %d",
                        ),
                        "Estimated Yearly Price (RM)": st.column_config.NumberColumn(
                            "Estimated Yearly Price (RM)",
                            format="RM %d",
                        ),
                        "RM / sqft": st.column_config.NumberColumn(
                            "RM / sqft",
                            format="%.2f",
                        ),
                    },
                )


st.subheader("2 — Investment Assumptions")

default_monthly_rent = market_defaults["fair_rent"]

assumption_col_1, assumption_col_2, assumption_col_3 = st.columns(3)

with assumption_col_1:
    monthly_rent = st.number_input(
        "Expected Monthly Rent (RM)",
        min_value=0.0,
        value=float(default_monthly_rent),
        step=100.0,
    )

    purchase_price = st.number_input(
        "Property Purchase Price (RM)",
        min_value=0.0,
        value=650000.0,
        step=10000.0,
    )

    occupancy_rate = st.slider(
        "Occupancy Rate (%)",
        min_value=0,
        max_value=100,
        value=95,
        step=1,
    )

with assumption_col_2:
    down_payment_percent = st.number_input(
        "Down Payment (%)",
        min_value=0.0,
        max_value=100.0,
        value=10.0,
        step=1.0,
    )

    annual_interest_rate = st.number_input(
        "Loan Interest Rate (% p.a.)",
        min_value=0.0,
        max_value=20.0,
        value=4.2,
        step=0.1,
    )

    loan_tenure_years = st.number_input(
        "Loan Tenure (Years)",
        min_value=1,
        max_value=40,
        value=30,
        step=1,
    )

with assumption_col_3:
    monthly_maintenance = st.number_input(
        "Monthly Maintenance / Service Charge (RM)",
        min_value=0.0,
        value=350.0,
        step=50.0,
    )

    other_monthly_cost = st.number_input(
        "Other Monthly Cost (RM)",
        min_value=0.0,
        value=150.0,
        step=50.0,
        help="Insurance, assessment, quit rent, minor repairs reserve, management fees, etc.",
    )

    closing_cost_percent = st.number_input(
        "One-time Closing Cost (%)",
        min_value=0.0,
        max_value=30.0,
        value=4.0,
        step=0.5,
        help="Legal fees, stamp duty estimate, valuation, loan agreement, and other upfront costs.",
    )


extra_col_1, extra_col_2, extra_col_3 = st.columns(3)

with extra_col_1:
    furnishing_or_renovation_cost = st.number_input(
        "Furnishing / Renovation Cost (RM)",
        min_value=0.0,
        value=15000.0,
        step=1000.0,
    )

with extra_col_2:
    rent_growth_rate = st.number_input(
        "Annual Rent Growth (%)",
        min_value=-20.0,
        max_value=30.0,
        value=2.0,
        step=0.5,
    )

with extra_col_3:
    cost_growth_rate = st.number_input(
        "Annual Cost Growth (%)",
        min_value=0.0,
        max_value=30.0,
        value=2.0,
        step=0.5,
    )


holding_years = st.slider(
    "Projection Period (Years)",
    min_value=1,
    max_value=15,
    value=5,
    step=1,
)


st.subheader("3 — ROI Summary")

down_payment_amount = purchase_price * down_payment_percent / 100
loan_amount = max(purchase_price - down_payment_amount, 0)

monthly_installment = monthly_loan_payment(
    loan_amount=loan_amount,
    annual_interest_rate=annual_interest_rate,
    tenure_years=loan_tenure_years,
)

effective_monthly_rent = monthly_rent * occupancy_rate / 100
annual_gross_rent = effective_monthly_rent * 12

monthly_operating_cost = monthly_maintenance + other_monthly_cost
annual_operating_cost = monthly_operating_cost * 12

net_operating_income = annual_gross_rent - annual_operating_cost
annual_loan_payment = monthly_installment * 12

annual_cashflow = net_operating_income - annual_loan_payment
monthly_cashflow = annual_cashflow / 12

closing_cost_amount = purchase_price * closing_cost_percent / 100
initial_cash_required = down_payment_amount + closing_cost_amount + furnishing_or_renovation_cost

gross_yield = annual_gross_rent / purchase_price * 100 if purchase_price > 0 else 0
net_yield = net_operating_income / purchase_price * 100 if purchase_price > 0 else 0
cash_on_cash_return = annual_cashflow / initial_cash_required * 100 if initial_cash_required > 0 else 0

dscr = net_operating_income / annual_loan_payment if annual_loan_payment > 0 else None

break_even_rent = (
    (monthly_installment + monthly_operating_cost) / (occupancy_rate / 100)
    if occupancy_rate > 0
    else None
)

metric_col_1, metric_col_2, metric_col_3, metric_col_4 = st.columns(4)

with metric_col_1:
    st.metric("Gross Rental Yield", percent(gross_yield))
    st.metric("Net Rental Yield", percent(net_yield))

with metric_col_2:
    st.metric("Monthly Cashflow", money(monthly_cashflow))
    st.metric("Annual Cashflow", money(annual_cashflow))

with metric_col_3:
    st.metric("Cash-on-Cash Return", percent(cash_on_cash_return))
    st.metric("DSCR", f"{dscr:.2f}x" if dscr is not None else "N/A")

with metric_col_4:
    st.metric("Monthly Installment", money(monthly_installment))
    st.metric("Break-even Rent", money(break_even_rent))


st.subheader("4 — Verdict")

verdict_notes = []

if monthly_cashflow >= 0:
    st.success("This scenario is cashflow-positive after estimated operating costs and loan payment.")
    verdict_notes.append("Cashflow is positive under the current assumptions.")
else:
    st.warning("This scenario is cashflow-negative after estimated operating costs and loan payment.")
    verdict_notes.append("Cashflow is negative under the current assumptions.")

if dscr is not None:
    if dscr >= 1.25:
        st.success("DSCR looks healthy. Net operating income gives a reasonable buffer over debt payment.")
        verdict_notes.append("DSCR is at or above 1.25x.")
    elif dscr >= 1.0:
        st.warning("DSCR is barely above 1.0x. The property covers debt payment, but buffer is thin.")
        verdict_notes.append("DSCR is between 1.0x and 1.25x.")
    else:
        st.error("DSCR is below 1.0x. Net operating income does not fully cover debt payment.")
        verdict_notes.append("DSCR is below 1.0x.")

if break_even_rent is not None:
    if monthly_rent >= break_even_rent:
        verdict_notes.append("Expected rent is above break-even rent.")
    else:
        verdict_notes.append("Expected rent is below break-even rent.")


assumption_df = pd.DataFrame(
    [
        {"Assumption": "Rent Source", "Value": selected_history_label},
        {"Assumption": "Expected Monthly Rent (RM)", "Value": monthly_rent},
        {"Assumption": "Effective Monthly Rent after Occupancy (RM)", "Value": effective_monthly_rent},
        {"Assumption": "Purchase Price (RM)", "Value": purchase_price},
        {"Assumption": "Down Payment (%)", "Value": down_payment_percent},
        {"Assumption": "Down Payment Amount (RM)", "Value": down_payment_amount},
        {"Assumption": "Loan Amount (RM)", "Value": loan_amount},
        {"Assumption": "Loan Interest Rate (%)", "Value": annual_interest_rate},
        {"Assumption": "Loan Tenure (Years)", "Value": loan_tenure_years},
        {"Assumption": "Monthly Maintenance (RM)", "Value": monthly_maintenance},
        {"Assumption": "Other Monthly Cost (RM)", "Value": other_monthly_cost},
        {"Assumption": "Closing Cost (%)", "Value": closing_cost_percent},
        {"Assumption": "Closing Cost Amount (RM)", "Value": closing_cost_amount},
        {"Assumption": "Furnishing / Renovation Cost (RM)", "Value": furnishing_or_renovation_cost},
        {"Assumption": "Initial Cash Required (RM)", "Value": initial_cash_required},
        {"Assumption": "Annual Rent Growth (%)", "Value": rent_growth_rate},
        {"Assumption": "Annual Cost Growth (%)", "Value": cost_growth_rate},
        {"Assumption": "Projection Period (Years)", "Value": holding_years},
    ]
)

result_df = pd.DataFrame(
    [
        {"Metric": "Annual Gross Rent (RM)", "Value": annual_gross_rent},
        {"Metric": "Annual Operating Cost (RM)", "Value": annual_operating_cost},
        {"Metric": "Net Operating Income (RM)", "Value": net_operating_income},
        {"Metric": "Annual Loan Payment (RM)", "Value": annual_loan_payment},
        {"Metric": "Monthly Installment (RM)", "Value": monthly_installment},
        {"Metric": "Monthly Cashflow (RM)", "Value": monthly_cashflow},
        {"Metric": "Annual Cashflow (RM)", "Value": annual_cashflow},
        {"Metric": "Gross Rental Yield (%)", "Value": gross_yield},
        {"Metric": "Net Rental Yield (%)", "Value": net_yield},
        {"Metric": "Cash-on-Cash Return (%)", "Value": cash_on_cash_return},
        {"Metric": "DSCR", "Value": dscr},
        {"Metric": "Break-even Rent (RM)", "Value": break_even_rent},
        {"Metric": "Verdict Notes", "Value": " | ".join(verdict_notes)},
    ]
)

result_display_df = format_value_table_for_display(result_df, "Metric")

st.dataframe(
    result_display_df,
    width="stretch",
    hide_index=True,
)


st.subheader("5 — Projection")

projection_df = make_roi_projection(
    monthly_rent=monthly_rent,
    purchase_price=purchase_price,
    occupancy_rate=occupancy_rate,
    rent_growth_rate=rent_growth_rate,
    cost_growth_rate=cost_growth_rate,
    monthly_operating_cost=monthly_operating_cost,
    monthly_installment=monthly_installment,
    holding_years=holding_years,
)

projection_display_df = format_projection_for_display(projection_df)

st.dataframe(
    projection_display_df,
    width="stretch",
    hide_index=True,
    column_config=projection_column_config(),
)

chart_col_1, chart_col_2 = st.columns(2)

with chart_col_1:
    cashflow_chart = px.line(
        projection_df,
        x="Year",
        y="Annual Cashflow (RM)",
        markers=True,
        title="Annual Cashflow Projection",
    )

    st.plotly_chart(cashflow_chart, width="stretch")

with chart_col_2:
    cumulative_chart = px.line(
        projection_df,
        x="Year",
        y="Cumulative Cashflow (RM)",
        markers=True,
        title="Cumulative Cashflow Projection",
    )

    st.plotly_chart(cumulative_chart, width="stretch")


st.subheader("6 — Download ROI Analysis")

selected_listing_df = selected_source_listing_table(selected_history_df)

try:
    excel_data = to_excel_bytes(
        assumption_df=assumption_df,
        result_df=result_df,
        projection_df=projection_display_df,
        selected_listing_df=selected_listing_df,
    )

    download_filename = f"SPEEDHOME_ROI_Calculator_{datetime.now().strftime('%Y%m%d')}.xlsx"

    st.download_button(
        "Download ROI Excel",
        data=excel_data,
        file_name=download_filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )

except Exception as exc:
    st.error("Could not generate ROI Excel file.")
    st.caption(str(exc))