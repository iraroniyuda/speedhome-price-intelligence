from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

try:
    from ui_style import inject_global_css
except Exception:
    def inject_global_css():
        return None


st.set_page_config(
    page_title="Methodology - SPEEDHOME Intelligence",
    layout="wide",
)

inject_global_css()

st.title("Methodology")
st.caption("How the SPEEDHOME Property Price Intelligence tool collects, cleans, interprets, and presents rental market data.")

st.divider()


def to_excel_bytes(methodology_sections, formulas_df, confidence_df, limitations_df):
    output = BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        methodology_sections.to_excel(writer, index=False, sheet_name="Methodology")
        formulas_df.to_excel(writer, index=False, sheet_name="Formulas")
        confidence_df.to_excel(writer, index=False, sheet_name="Confidence Rules")
        limitations_df.to_excel(writer, index=False, sheet_name="Limitations")

        for sheet_name, df in {
            "Methodology": methodology_sections,
            "Formulas": formulas_df,
            "Confidence Rules": confidence_df,
            "Limitations": limitations_df,
        }.items():
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes(1, 0)

            for index, column in enumerate(df.columns):
                values = df[column].astype(str).head(100).tolist() if not df.empty else []
                max_value_width = max([len(value) for value in values], default=0)
                width = max(14, min(70, max(len(str(column)) + 4, max_value_width + 2)))
                worksheet.set_column(index, index, width)

    output.seek(0)
    return output.getvalue()


methodology_sections = pd.DataFrame(
    [
        {
            "Section": "Purpose",
            "Methodology": "The application is designed as a rental market intelligence dashboard for public SPEEDHOME rental listings.",
            "Business Reason": "The goal is to help users compare rental areas, understand market rent, detect value opportunities, and support investment-style decision making.",
        },
        {
            "Section": "Data Source",
            "Methodology": "Data is collected from public SPEEDHOME rent pages based on user input.",
            "Business Reason": "Using public listing pages keeps the tool aligned with visible market supply and makes the analysis explainable.",
        },
        {
            "Section": "Input Handling",
            "Methodology": "The user can input a SPEEDHOME URL, area name, or apartment name. Area suggestions are helper options for text search and are not used as a whitelist for direct SPEEDHOME /rent URLs.",
            "Business Reason": "This keeps the tool flexible while still making the interface easier to use.",
        },
        {
            "Section": "Autocomplete Logic",
            "Methodology": "Known area/apartment suggestions use strict substring matching. Random text is rejected, while valid-shape SPEEDHOME /rent URLs outside the suggestions are shown as custom URL options that require explicit confirmation before scraping.",
            "Business Reason": "This avoids misleading fuzzy matches while still allowing custom searches.",
        },
        {
            "Section": "Direct URL Safety",
            "Methodology": "Direct URLs are validated by domain and /rent/<slug> path, not by the suggestion list. Custom /rent URLs outside the built-in suggestions require user confirmation. Obvious concatenated typo URLs are blocked before live scraping, empty results are not cached, and redirects away from the requested rent target are rejected.",
            "Business Reason": "This supports newly added SPEEDHOME rent pages while preventing random or typo input from wasting scrape time or showing stale data from another target.",
        },
        {
            "Section": "Robots and Delay",
            "Methodology": "The scraper checks the robots policy for supported paths and uses a delay before requests.",
            "Business Reason": "This makes the collection process more responsible and avoids aggressive scraping behavior.",
        },
        {
            "Section": "Fetch Strategy",
            "Methodology": "The app first tries a normal HTTP request. If the request fails, it falls back to Playwright browser rendering.",
            "Business Reason": "Some public pages require JavaScript rendering or reject normal requests, so Playwright improves reliability.",
        },
        {
            "Section": "Cache Strategy",
            "Methodology": "Successful parsed listings are cached locally. Empty results are not cached to avoid locking failed parsing results.",
            "Business Reason": "Caching improves speed and provides a fallback when live fetching is unstable.",
        },
        {
            "Section": "Scrape Timing",
            "Methodology": "Each scrape records live scrape duration, current run duration, and cache lookup duration where applicable. These values are displayed in data quality and comparison diagnostics.",
            "Business Reason": "Browser-rendered scraping can take longer on dynamic public pages, so timing diagnostics make performance transparent during review.",
        },
        {
            "Section": "Data Extraction",
            "Methodology": "The parser extracts listing title, area, bedroom count, bathroom count, car parks, monthly rent, daily rent, yearly rent, size, furnishing, and listing URL where available.",
            "Business Reason": "These fields are the minimum useful attributes for rental comparison and pricing analysis.",
        },
        {
            "Section": "Optional Detail Enrichment",
            "Methodology": "When enabled, the scraper opens accepted SPEEDHOME detail pages only for listing cards with missing furnishing or unit-detail fields. This can fill values such as Fully Furnished, Partially Furnished, or Unfurnished when those values are visible on the detail page but not on the result card.",
            "Business Reason": "This improves data completeness while keeping the base dataset grounded in the selected public result page. It is optional because it increases scrape time.",
        },
        {
            "Section": "Room Rental Labels",
            "Methodology": "For room-rental cards, MASTER, MEDIUM, and SMALL are treated as unit type labels and displayed as Master, Medium, and Small without a generic Room suffix. PRIVATE and SHARED are treated as bathroom arrangement labels, not bedroom or unit type labels.",
            "Business Reason": "This prevents room rentals such as MEDIUM | SHARED | 0 from being misclassified as Studio or 1 Bedroom units.",
        },
        {
            "Section": "Rental Type Handling",
            "Methodology": "Monthly rent is treated as the primary rental value. Daily and explicit yearly prices are only marked available when detected. If explicit yearly rent is not found, estimated yearly rent is calculated as monthly rent multiplied by 12.",
            "Business Reason": "This avoids pretending that estimated yearly rent is directly published by SPEEDHOME.",
        },
        {
            "Section": "Data Completeness Score",
            "Methodology": "Each listing receives a completeness score based on availability of important fields such as title, area, unit type, rent, size, listing URL, bedrooms, bathrooms, car parks, and furnishing.",
            "Business Reason": "This helps users understand which listings are more reliable for comparison.",
        },
        {
            "Section": "Furnishing Detection",
            "Methodology": "Furnishing status is only assigned when the rendered public listing card or parsed listing data explicitly exposes labels such as Fully Furnished, Partially Furnished, or Unfurnished. If the result card does not expose a furnishing label, the app shows Not detected on result card rather than guessing.",
            "Business Reason": "This avoids falsely classifying units as unfurnished just because the public result card did not print a furnishing status.",
        },
        {
            "Section": "Price Summary",
            "Methodology": "Listings are grouped by unit type. The app calculates unit count, average rent, median rent, mode rent, fair price, average size, average RM per sqft, and average data completeness.",
            "Business Reason": "Grouping by unit type gives a more meaningful view than mixing studios, 1-bedroom, and larger units together.",
        },
        {
            "Section": "Fair Price",
            "Methodology": "For small samples, fair price uses the median. For larger samples, the app uses IQR to remove extreme outliers, then combines the trimmed median and trimmed mean.",
            "Business Reason": "This reduces distortion from unusually cheap or expensive listings.",
        },
        {
            "Section": "Sample Size Confidence",
            "Methodology": "The app labels sample confidence based on listing count: Low, Medium, or High.",
            "Business Reason": "A price estimate from 3 listings should not be treated the same as a price estimate from 30 listings.",
        },
        {
            "Section": "Best Value Opportunities",
            "Methodology": "The app surfaces multiple value angles, including cheapest listings, best RM per sqft, largest units under median rent, representative fair-price listings, and highest data completeness.",
            "Business Reason": "Value is not always the cheapest price; it can also mean better size, better completeness, or better price-per-square-foot.",
        },
        {
            "Section": "Outlier Detection",
            "Methodology": "Outliers are detected using the IQR method. Listings below Q1 - 1.5xIQR are marked potentially underpriced. Listings above Q3 + 1.5xIQR are marked potentially overpriced.",
            "Business Reason": "This gives a quick way to identify listings that may need extra attention.",
        },
        {
            "Section": "Comparison Mode",
            "Methodology": "The comparison page scrapes Area A and Area B separately, then compares listing count, rent levels, RM per sqft, data completeness, sample confidence, charts, segment summary, and side-by-side listings.",
            "Business Reason": "Separate scraping prevents hidden assumptions and makes the comparison more auditable.",
        },
        {
            "Section": "ROI Calculator",
            "Methodology": "The ROI page estimates gross yield, net yield, cash-on-cash return, monthly cashflow, annual cashflow, DSCR, break-even rent, and multi-year cashflow projection.",
            "Business Reason": "This turns rental price intelligence into a more practical decision-support model.",
        },
        {
            "Section": "Export",
            "Methodology": "CSV and Excel exports are provided for listings, summary, comparison, ROI analysis, and methodology where relevant.",
            "Business Reason": "Export makes the result easier to review, share, and validate outside the app.",
        },
    ]
)


formulas_df = pd.DataFrame(
    [
        {
            "Metric": "Estimated Yearly Rent",
            "Formula": "Monthly Rent x 12",
            "Explanation": "Used only when explicit yearly rent is not detected.",
        },
        {
            "Metric": "RM per sqft",
            "Formula": "Monthly Rent / Size in sqft",
            "Explanation": "Used to compare value across differently sized units.",
        },
        {
            "Metric": "Fair Price",
            "Formula": "Small sample: Median. Larger sample: (Trimmed Median + Trimmed Mean) / 2",
            "Explanation": "Designed to reduce impact from outliers.",
        },
        {
            "Metric": "IQR Lower Bound",
            "Formula": "Q1 - 1.5 x IQR",
            "Explanation": "Listings below this threshold may be underpriced.",
        },
        {
            "Metric": "IQR Upper Bound",
            "Formula": "Q3 + 1.5 x IQR",
            "Explanation": "Listings above this threshold may be overpriced.",
        },
        {
            "Metric": "Gross Rental Yield",
            "Formula": "Annual Effective Rent / Purchase Price x 100",
            "Explanation": "Simple rental return before operating costs.",
        },
        {
            "Metric": "Net Rental Yield",
            "Formula": "Net Operating Income / Purchase Price x 100",
            "Explanation": "Rental return after operating costs but before financing impact.",
        },
        {
            "Metric": "Cash-on-Cash Return",
            "Formula": "Annual Cashflow / Initial Cash Required x 100",
            "Explanation": "Return based on cash invested upfront.",
        },
        {
            "Metric": "DSCR",
            "Formula": "Net Operating Income / Annual Loan Payment",
            "Explanation": "Shows whether rental income covers debt payment.",
        },
        {
            "Metric": "Break-even Rent",
            "Formula": "(Monthly Installment + Monthly Operating Cost) / Occupancy Rate",
            "Explanation": "Monthly rent needed to cover operating cost and debt payment.",
        },
    ]
)


confidence_df = pd.DataFrame(
    [
        {
            "Sample Size": "0 listings",
            "Confidence": "No sample",
            "Interpretation": "No market confidence can be calculated.",
        },
        {
            "Sample Size": "1-4 listings",
            "Confidence": "Low",
            "Interpretation": "Treat results as rough directional signals only.",
        },
        {
            "Sample Size": "5-14 listings",
            "Confidence": "Medium",
            "Interpretation": "Useful for directional comparison, but still sensitive to outliers.",
        },
        {
            "Sample Size": "15+ listings",
            "Confidence": "High",
            "Interpretation": "Stronger public-listing sample for market intelligence.",
        },
    ]
)


limitations_df = pd.DataFrame(
    [
        {
            "Limitation": "Public page dependency",
            "Explanation": "The tool depends on data visible from public SPEEDHOME pages. If the website structure changes, the parser may need adjustment.",
            "Mitigation": "Parser diagnostics and cache fallback help identify and reduce failure impact.",
        },
        {
            "Limitation": "Not official valuation",
            "Explanation": "The result is market intelligence based on public rental listings, not a certified property valuation.",
            "Mitigation": "The app labels results as directional and exposes methodology clearly.",
        },
        {
            "Limitation": "Sample size variation",
            "Explanation": "Some areas have fewer detected listings, making estimates less stable.",
            "Mitigation": "Sample Size Confidence and Confidence Note are shown in dashboard and comparison mode.",
        },
        {
            "Limitation": "Listing quality varies",
            "Explanation": "Some listings may have incomplete fields such as sqft, furnishing, car parks, or bathroom count.",
            "Mitigation": "Data Completeness Score helps users identify stronger rows.",
        },
        {
            "Limitation": "Dynamic website behavior",
            "Explanation": "Some data may load through JavaScript or API calls, which normal HTTP requests may not capture.",
            "Mitigation": "The app falls back to Playwright browser rendering when normal requests fail.",
        },
        {
            "Limitation": "ROI assumptions are user-defined",
            "Explanation": "Purchase price, financing, occupancy, maintenance, and cost assumptions can materially change ROI output.",
            "Mitigation": "The ROI page exposes all assumptions and exports them with the result.",
        },
        {
            "Limitation": "Cache may become stale",
            "Explanation": "Cached data may not reflect the latest public listings.",
            "Mitigation": "Users can clear cache and re-run scraping.",
        },
    ]
)


overview_col_1, overview_col_2, overview_col_3 = st.columns(3)

with overview_col_1:
    st.metric("Method Sections", len(methodology_sections))

with overview_col_2:
    st.metric("Formula References", len(formulas_df))

with overview_col_3:
    st.metric("Known Limitations", len(limitations_df))


st.subheader("1 — Methodology Overview")

st.dataframe(
    methodology_sections,
    width="stretch",
    hide_index=True,
)


st.subheader("2 — Key Formulas")

st.dataframe(
    formulas_df,
    width="stretch",
    hide_index=True,
)


st.subheader("3 — Sample Size Confidence Rules")

st.dataframe(
    confidence_df,
    width="stretch",
    hide_index=True,
)


st.subheader("4 — Limitations and Mitigation")

st.dataframe(
    limitations_df,
    width="stretch",
    hide_index=True,
)


st.subheader("5 — Plain-English Summary")

st.markdown(
    """
This project is not only a scraper. It is designed as a small rental market intelligence system.

The workflow is:

1. Accept an area, apartment name, or SPEEDHOME URL.
2. Normalize the input into a SPEEDHOME rental URL.
3. Check scraping permission and apply request delay.
4. Fetch the public page using requests first, then Playwright if needed.
5. Parse listings from rendered HTML and available JSON payloads.
6. Clean the data into a consistent table.
7. Calculate rent summary, fair price, value opportunities, outliers, and sample confidence.
8. Allow side-by-side area comparison.
9. Allow rental ROI simulation using market rent and user-defined investment assumptions.
10. Export results for further review.

The most important design decision is honesty around data quality. The app does not hide weak samples or missing fields. Instead, it exposes data completeness, sample confidence, scraper diagnostics, and methodology so the result can be reviewed critically.
"""
)


st.subheader("6 — Download Methodology")

excel_data = to_excel_bytes(
    methodology_sections=methodology_sections,
    formulas_df=formulas_df,
    confidence_df=confidence_df,
    limitations_df=limitations_df,
)

filename = f"SPEEDHOME_Methodology_{datetime.now().strftime('%Y%m%d')}.xlsx"

st.download_button(
    "Download Methodology Excel",
    data=excel_data,
    file_name=filename,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
)

st.divider()
st.subheader("Reported Total vs Rendered Listing Cards")
st.write(
    "SPEEDHOME may show a headline target count that is not identical to the number of direct listing cards rendered on the public page. "
    "For large result pages, rendered cards can be lower than the reported count because the public page may expose only a subset. "
    "For some pages, rendered cards can be higher than the headline count because nearby or broader source-page cards are displayed."
)
st.write(
    "The app therefore separates SPEEDHOME Reported Target Count, Rendered Direct Listing Cards, Strong Target Evidence Cards, and Source Page Cards. "
    "When rendered cards exceed the reported count, the app does not display this as greater-than-100% coverage; it labels the result as a reported-vs-rendered mismatch instead."
)

st.subheader("Default Scrape Mode")
st.write(
    "The app uses one safe default mode: collect valid direct listing cards from public rendered SPEEDHOME pages and check public pagination when available. "
    "This keeps the main workflow simple for assessment reviewers while preserving transparent diagnostics such as pages checked, reported count, collected count, and coverage interpretation."
)
