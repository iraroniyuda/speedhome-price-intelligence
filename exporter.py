from io import BytesIO

import pandas as pd


def _autosize_columns(writer, sheet_name: str, df: pd.DataFrame) -> None:
    worksheet = writer.sheets[sheet_name]
    worksheet.freeze_panes(1, 0)

    for index, column in enumerate(df.columns):
        values = df[column].astype(str).head(100).tolist() if not df.empty else []
        max_value_width = max([len(str(value)) for value in values], default=0)
        width = max(12, min(50, max(len(str(column)) + 4, max_value_width + 2)))
        worksheet.set_column(index, index, width)


def to_excel_bytes(
    unit_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    rental_coverage_df: pd.DataFrame = None,
    data_quality_df: pd.DataFrame = None,
    best_value_df: pd.DataFrame = None,
    outlier_report_df: pd.DataFrame = None,
) -> bytes:
    output = BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Price Summary")
        unit_df.to_excel(writer, index=False, sheet_name="Unit Listings")

        sheets = {
            "Price Summary": summary_df,
            "Unit Listings": unit_df,
        }

        if best_value_df is not None:
            best_value_df.to_excel(writer, index=False, sheet_name="Best Value")
            sheets["Best Value"] = best_value_df

        if outlier_report_df is not None:
            outlier_report_df.to_excel(writer, index=False, sheet_name="Outliers")
            sheets["Outliers"] = outlier_report_df

        if rental_coverage_df is not None:
            rental_coverage_df.to_excel(writer, index=False, sheet_name="Rental Coverage")
            sheets["Rental Coverage"] = rental_coverage_df

        if data_quality_df is not None:
            data_quality_df.to_excel(writer, index=False, sheet_name="Data Quality")
            sheets["Data Quality"] = data_quality_df

        methodology = pd.DataFrame(
            [
                {
                    "Topic": "Fair Price",
                    "Explanation": "Fair price is estimated using an outlier-resistant approach based on median and trimmed mean.",
                },
                {
                    "Topic": "Best Value",
                    "Explanation": "Best value opportunities are derived from cheapest rent, lowest RM/sqft, largest unit under median rent, representative median listing, and data completeness.",
                },
                {
                    "Topic": "Outlier Detection",
                    "Explanation": "Outlier detection uses the IQR method. Prices below Q1 - 1.5xIQR are potentially underpriced; prices above Q3 + 1.5xIQR are potentially overpriced.",
                },
                {
                    "Topic": "Estimated Yearly Price",
                    "Explanation": "When explicit yearly rent is not detected, yearly rent is estimated as monthly rent multiplied by 12.",
                },
                {
                    "Topic": "Rental Coverage",
                    "Explanation": "Daily, monthly, and yearly rental availability are reported separately to avoid silent missing values.",
                },
                {
                    "Topic": "Furnishing Detection",
                    "Explanation": "Furnishing is reported only when the public rendered listing card or parsed listing data exposes Fully Furnished, Partially Furnished, or Unfurnished text. Missing card-level labels are shown as not detected instead of being guessed.",
                },
                {
                    "Topic": "Target Area Filter",
                    "Explanation": "The target area filter keeps valid direct listing cards from the selected public SPEEDHOME result page and labels strong target-evidence cards separately from broader source-page cards.",
                },
                {
                    "Topic": "Reported Total vs Rendered Cards",
                    "Explanation": "SPEEDHOME's headline target count may differ from the number of direct listing cards rendered on public pages. The app reports both counts and does not treat rendered cards above the reported count as >100% coverage.",
                },
                {
                    "Topic": "Scrape Mode",
                    "Explanation": "The app uses public rendered listing cards and checks public pagination when available. This keeps extraction verifiable through SPEEDHOME detail links.",
                },
            ]
        )

        methodology.to_excel(writer, index=False, sheet_name="Methodology")
        sheets["Methodology"] = methodology

        for sheet_name, df in sheets.items():
            _autosize_columns(writer, sheet_name, df)

    output.seek(0)
    return output.getvalue()
