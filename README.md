# SPEEDHOME Property Price Intelligence

A Streamlit-based rental market intelligence dashboard for collecting, cleaning, analyzing, comparing, and exporting public SPEEDHOME rental listing data.

This project was built as a technical assessment project for the CEO Office role. The goal is not only to scrape rental listings, but to turn public rental data into a small decision-support product with transparent assumptions, diagnostics, comparison tools, and ROI simulation.

---

## 1. Project Overview

**SPEEDHOME Property Price Intelligence** helps users analyze public SPEEDHOME rental pages by collecting visible rental listing cards, standardizing listing fields, summarizing rent by unit segment, comparing two areas side by side, and estimating rental investment performance.

The app focuses on:

- public SPEEDHOME rental pages only;
- safe and transparent scraping behavior;
- clear market summaries by unit type;
- sample-size confidence and data quality visibility;
- exportable CSV/Excel outputs;
- business-oriented interpretation rather than raw scraping output only.

---

## 2. Live Demo

Add the deployed Streamlit link here after deployment:

```text
<your-deployed-streamlit-url>
```

For Streamlit Cloud, set the main file path to:

```text
Home.py
```

---

## 3. Screenshots

Screenshots are stored under:

```text
docs/screenshots/
```

The screenshot sections below use collapsible panels so the README stays readable.

<details>
<summary><strong>Search Sidebar</strong></summary>

<br>

![Search Sidebar](docs/screenshots/00_search_sidebar.png)

</details>

<details>
<summary><strong>Main Dashboard</strong></summary>

<br>

![Home Dashboard A](docs/screenshots/01_home_dashboard_a.png)

![Home Dashboard B](docs/screenshots/01_home_dashboard_b.png)

![Home Dashboard C](docs/screenshots/01_home_dashboard_c.png)

![Home Dashboard D](docs/screenshots/01_home_dashboard_d.png)

![Home Dashboard E](docs/screenshots/01_home_dashboard_e.png)

![Home Dashboard F](docs/screenshots/01_home_dashboard_f.png)

</details>

<details>
<summary><strong>Comparison Mode</strong></summary>

<br>

![Comparison Mode A](docs/screenshots/03_comparison_mode_a.png)

![Comparison Mode B](docs/screenshots/03_comparison_mode_b.png)

![Comparison Mode C](docs/screenshots/03_comparison_mode_c.png)

![Comparison Mode D](docs/screenshots/03_comparison_mode_d.png)

</details>

<details>
<summary><strong>ROI Calculator</strong></summary>

<br>

![ROI Calculator A](docs/screenshots/04_roi_calculator_a.png)

![ROI Calculator B](docs/screenshots/04_roi_calculator_b.png)

![ROI Calculator C](docs/screenshots/04_roi_calculator_c.png)

</details>

<details>
<summary><strong>Methodology</strong></summary>

<br>

![Methodology A](docs/screenshots/05_methodology_a.png)

![Methodology B](docs/screenshots/05_methodology_b.png)

![Methodology C](docs/screenshots/05_methodology_c.png)

</details>

---

## 4. Key Features

### Main Dashboard

- Accepts a SPEEDHOME rental URL, area name, or apartment name.
- Provides strict autocomplete suggestions for known areas/apartments.
- Supports custom direct SPEEDHOME `/rent/...` URLs with explicit confirmation.
- Rejects random text, non-SPEEDHOME URLs, and non-rent pages such as `/details/...`.
- Collects valid public direct listing cards from rendered SPEEDHOME rental pages.
- Uses local cache fallback to reduce repeated live requests and support demo reliability.
- Shows scrape diagnostics, parser notes, cache usage, robots policy status, and timing information.
- Presents price summary, sample-size confidence, data quality, rental type coverage, best-value opportunities, outlier intelligence, and automatic insights.
- Provides interactive listing filters and sorting for the displayed unit listings.
- Supports CSV and Excel export.

### Price Intelligence

The app summarizes rental listings by unit segment, including:

- unit count;
- average monthly rent;
- median monthly rent;
- mode monthly rent;
- fair price estimate;
- average size;
- average RM per sqft;
- data completeness score;
- sample-size confidence;
- confidence note.

### Comparison Mode

The comparison page lets users scrape and compare two SPEEDHOME areas separately.

It includes:

- Area A and Area B independent scraping;
- collected card count;
- SPEEDHOME reported target count when exposed;
- reported-vs-rendered status;
- median rent comparison;
- average rent comparison;
- lowest and highest rent comparison;
- average size comparison;
- best RM per sqft comparison;
- sample-size confidence comparison;
- coverage diagnostics;
- side-by-side charts;
- segment summary by unit type;
- side-by-side listing tables;
- Excel and CSV export.

### ROI Calculator

The ROI calculator estimates directional rental investment performance from either manual rent input or scraped market rent history.

It includes:

- expected monthly rent;
- purchase price;
- down payment;
- loan amount;
- loan interest rate;
- loan tenure;
- monthly installment;
- maintenance and other operating cost;
- occupancy rate;
- gross rental yield;
- net rental yield;
- cash-on-cash return;
- monthly cashflow;
- annual cashflow;
- DSCR;
- break-even rent;
- multi-year projection;
- Excel export.

### Methodology Page

The methodology page documents:

- data source;
- input handling;
- scraping strategy;
- cache strategy;
- extraction logic;
- room rental and bathroom label handling;
- rental type logic;
- data completeness scoring;
- fair price methodology;
- sample-size confidence;
- best value logic;
- outlier detection;
- comparison logic;
- ROI formulas;
- limitations and mitigation.

---

## 5. Room Rental and Bathroom Label Handling

SPEEDHOME room-rental cards may display labels such as:

```text
MASTER | PRIVATE | 0
MEDIUM | SHARED | 0
SMALL | SHARED | 0
```

The app treats:

- `MASTER`, `MEDIUM`, and `SMALL` as unit type labels;
- `PRIVATE` and `SHARED` as bathroom arrangement labels;
- `0`, `1`, `2`, etc. as car park count when applicable.

For example:

```text
MEDIUM | SHARED | 0
```

is interpreted as:

```text
Unit Type: Medium
Bathroom Type: Shared Bathroom
Car Parks: 0
```

The app does not add a generic `Room` suffix to these room-rental labels, because doing so can make the listing table less clear.

---

## 6. Rental Type Coverage

The app distinguishes between:

- daily rental price;
- monthly rental price;
- explicit yearly rental price;
- estimated yearly rental price.

If explicit yearly rent is not detected, yearly rent is estimated using:

```text
Estimated Yearly Rent = Monthly Rent x 12
```

The rental coverage report makes the distinction explicit so estimated values are not mistaken for values directly shown by SPEEDHOME.

---

## 7. Furnishing Detection

SPEEDHOME exposes furnishing as a website filter, but public result cards do not always print the furnishing label on every card.

The app only labels a listing as:

- `Fully Furnished`;
- `Partially Furnished`;
- `Unfurnished`;
- `Furnished`;

when the text is detected from the public rendered card or accepted detail page data.

If furnishing is not exposed, the app shows:

```text
Not detected on result card
```

This avoids falsely classifying a unit as unfurnished only because the public result card did not print furnishing information.

---

## 8. Reported Total vs Rendered Listing Cards

SPEEDHOME pages can expose different count signals:

- **SPEEDHOME reported target count**: headline or metadata count shown by SPEEDHOME for the searched area.
- **Rendered direct listing cards collected**: verified `/details/...` listing cards rendered on the public page and parsed by this app.

These numbers may differ.

For large result pages, collected direct cards may be lower than the reported count because SPEEDHOME may limit or paginate visible public results. For some pages, rendered cards may exceed the reported count because the public page may include nearby or broader source-page cards.

The app avoids showing misleading coverage such as `188%`. Instead, it reports:

- `Reported vs Rendered Status`;
- `Explicit Target Evidence`;
- `Source Page Cards`;
- `Coverage Confidence`;
- diagnostics notes.

When SPEEDHOME does not expose a clear reported target count, the app displays reviewer-friendly wording such as:

```text
SPEEDHOME Reported: Not exposed
Coverage: No target count
```

---

## 9. Scrape Mode

The app uses one safe default scrape mode:

```text
Collect valid direct listing cards from public rendered SPEEDHOME pages and check public pagination when available.
```

The scraper:

1. validates the target input;
2. normalizes it into a SPEEDHOME `/rent/...` URL;
3. checks supported public rent path policy;
4. applies reasonable request delay;
5. tries normal HTTP request first;
6. falls back to Playwright browser rendering when needed;
7. parses rendered HTML and available JSON payloads;
8. accepts direct `/details/...` listing cards only;
9. stores diagnostics and timing information;
10. saves successful datasets to local cache/history.

---

## 10. Cache and Demo Fallback

The app stores successful scrape results in:

```text
data/speedhome_cache.json
data/scrape_history.json
```

This reduces repeated live requests and helps the app remain demonstrable if the deployed environment is blocked by the source site or if live scraping is slow.

Current sample cached datasets prepared for demo/testing include:

| Area | Listings |
|---|---:|
| Mont Kiara | 40 |
| Wangsa Maju | 40 |
| Bangsar | 31 |
| Damansara | 40 |
| Dutamas | 33 |
| Mont Kiara Pines | 30 |

Cached data may become stale, so users can clear cache and rerun scraping when fresh data is required.

---

## 11. Tech Stack

- Python
- Streamlit
- Pandas
- NumPy
- Requests
- BeautifulSoup
- Playwright
- Plotly
- OpenPyXL
- XlsxWriter
- streamlit-searchbox

---

## 12. Project Structure

```text
speedhome-price-intelligence/
├── Home.py
├── scraper.py
├── analyzer.py
├── exporter.py
├── history_store.py
├── ui_style.py
├── requirements.txt
├── README.md
├── .gitignore
├── .streamlit/
│   └── config.toml
├── pages/
│   ├── 1_Comparison_Mode.py
│   ├── 2_ROI_Calculator.py
│   └── 3_Methodology.py
├── data/
│   ├── speedhome_cache.json
│   └── scrape_history.json
└── docs/
    └── screenshots/
        ├── 00_search_sidebar.png
        ├── 01_home_dashboard_a.png
        ├── 01_home_dashboard_b.png
        ├── 01_home_dashboard_c.png
        ├── 01_home_dashboard_d.png
        ├── 01_home_dashboard_e.png
        ├── 01_home_dashboard_f.png
        ├── 03_comparison_mode_a.png
        ├── 03_comparison_mode_b.png
        ├── 03_comparison_mode_c.png
        ├── 03_comparison_mode_d.png
        ├── 04_roi_calculator_a.png
        ├── 04_roi_calculator_b.png
        ├── 04_roi_calculator_c.png
        ├── 05_methodology_a.png
        ├── 05_methodology_b.png
        └── 05_methodology_c.png
```

---

## 13. Installation

Clone the repository:

```bash
git clone <your-repository-url>
cd speedhome-price-intelligence
```

Create a virtual environment:

```bash
python -m venv venv
```

Activate the virtual environment.

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Install the Playwright Chromium browser dependency:

```bash
python -m playwright install chromium
```

---

## 14. Running the App Locally

Run the Streamlit app:

```bash
streamlit run Home.py
```

Then open:

```text
http://localhost:8501
```

A successful run should show a local URL similar to:

```text
Local URL: http://localhost:8501
```

---

## 15. Deployment Notes

For Streamlit Cloud:

1. Push the repository to GitHub.
2. Create a new Streamlit app.
3. Set the main file path to:

```text
Home.py
```

4. Ensure `requirements.txt` is included.
5. If Playwright browser installation is required by the hosting environment, add the appropriate deployment setup command or package configuration supported by the host.
6. Keep `data/speedhome_cache.json` and `data/scrape_history.json` committed if cache fallback is needed for demo reliability.

After deployment, test the deployed link in:

- normal browser session;
- incognito/private window;
- mobile browser.

---

## 16. How to Use

### Main Dashboard

1. Open the app.
2. Enter a SPEEDHOME rental URL, area name, or apartment name.
3. Select the desired suggestion or confirm a valid custom SPEEDHOME `/rent/...` URL.
4. Keep or disable optional detail enrichment depending on speed vs completeness needs.
5. Click **Analyze SPEEDHOME Data**.
6. Review price summary, data quality, sample-size confidence, best value opportunities, outlier report, rental type coverage, listing table, and insights.
7. Download CSV or Excel if needed.

### Comparison Mode

1. Open **Comparison Mode** from the sidebar.
2. Select Area A.
3. Click **Scrape Area A**.
4. Select Area B.
5. Click **Scrape Area B**.
6. Review quick verdict, side-by-side metrics, diagnostics, charts, segment summaries, and listing tables.
7. Download comparison CSV or Excel if needed.

### ROI Calculator

1. Open **ROI Calculator**.
2. Choose manual rent input or use scraped market history.
3. Enter investment assumptions.
4. Review yield, cashflow, DSCR, break-even rent, and projection.
5. Download ROI Excel if needed.

### Methodology

Open **Methodology** to review the logic behind scraping, cleaning, analysis, confidence scoring, outlier detection, comparison logic, ROI calculation, and limitations.

---

## 17. Data Methodology

### Data Source

The app collects data from public SPEEDHOME rental pages.

### Input Normalization

User input can be:

- a full SPEEDHOME `/rent/...` URL;
- a `/rent/...` path;
- a supported area name;
- a supported apartment name.

Example:

```text
Mont Kiara -> https://speedhome.com/rent/mont-kiara
Ara Damansara -> https://speedhome.com/rent/ara-damansara
```

Random text is rejected before scraping.

### Data Cleaning

The app normalizes each listing into a consistent structure:

- title;
- property area;
- source area;
- bedroom count;
- unit type;
- room type label;
- bathroom count;
- bathroom type label;
- car park count;
- monthly rent;
- daily rent;
- explicit yearly rent;
- estimated yearly rent;
- size in sqft;
- furnishing;
- detected rental type;
- listing URL;
- raw text.

Numeric fields are converted with Pandas numeric coercion.

---

## 18. Fair Price Methodology

Fair price is designed to be more robust than a simple average.

For small samples:

```text
Fair Price = Median
```

For larger samples:

```text
1. Calculate Q1 and Q3
2. Calculate IQR = Q3 - Q1
3. Remove values outside Q1 - 1.5 x IQR and Q3 + 1.5 x IQR
4. Fair Price = (Trimmed Median + Trimmed Mean) / 2
```

This reduces distortion from unusually cheap or expensive listings.

---

## 19. Sample Size Confidence

The app uses sample-size confidence to avoid over-interpreting small datasets.

| Listing Count | Confidence | Interpretation |
|---:|---|---|
| 0 | No sample | No market confidence can be calculated |
| 1-4 | Low | Treat as rough directional signal only |
| 5-14 | Medium | Useful for directional comparison, but sensitive to outliers |
| 15+ | High | Stronger public-listing sample |

---

## 20. Data Completeness Score

Each listing receives a data completeness score based on detected important fields:

- title;
- property area;
- unit type;
- monthly rent;
- estimated yearly rent;
- size;
- listing URL;
- bedrooms;
- bathrooms;
- car parks;
- furnishing.

The score helps users identify stronger and weaker rows for review.

---

## 21. ROI Methodology

The ROI calculator uses directional investment formulas.

### Effective Monthly Rent

```text
Effective Monthly Rent = Expected Monthly Rent x Occupancy Rate
```

### Annual Gross Rent

```text
Annual Gross Rent = Effective Monthly Rent x 12
```

### Net Operating Income

```text
Net Operating Income = Annual Gross Rent - Annual Operating Cost
```

### Gross Rental Yield

```text
Gross Rental Yield = Annual Gross Rent / Purchase Price x 100
```

### Net Rental Yield

```text
Net Rental Yield = Net Operating Income / Purchase Price x 100
```

### Annual Cashflow

```text
Annual Cashflow = Net Operating Income - Annual Loan Payment
```

### Cash-on-Cash Return

```text
Cash-on-Cash Return = Annual Cashflow / Initial Cash Required x 100
```

### DSCR

```text
DSCR = Net Operating Income / Annual Loan Payment
```

### Break-even Rent

```text
Break-even Rent = (Monthly Installment + Monthly Operating Cost) / Occupancy Rate
```

The ROI result is directional market intelligence, not formal financial advice.

---

## 22. Export Features

The app supports export for:

- unit listings;
- price summary;
- rental type coverage;
- data quality report;
- best value opportunities;
- outlier report;
- comparison analysis;
- ROI analysis;
- methodology documentation.

Supported formats:

- CSV;
- Excel.

---

## 23. Responsible Scraping Notes

This project is designed for public rental page analysis and technical assessment purposes.

Responsible scraping measures include:

- supported public rent path validation;
- robots policy check;
- reasonable request delay;
- public page only;
- cache usage to reduce repeated live requests;
- fallback handling when fetching is blocked or unavailable;
- clear diagnostics when data cannot be parsed.

---

## 24. Limitations

- The tool depends on public SPEEDHOME page structure.
- Dynamic JavaScript rendering can change over time.
- Some listings may have incomplete fields.
- Some areas may have small sample sizes.
- SPEEDHOME may not expose a reported target count for every page.
- Rendered listing cards may not always match the headline reported count exactly.
- Cached data may become stale.
- ROI output depends heavily on user-defined assumptions.
- Results are directional market intelligence, not official valuation or financial advice.

---

## 25. Local Testing Checklist

Before submission, the app was tested through the following checks:

- fresh virtual environment install;
- dependency installation from `requirements.txt`;
- Playwright Chromium installation;
- local Streamlit run from `Home.py`;
- main dashboard analysis;
- cache fallback datasets;
- CSV and Excel export;
- comparison mode;
- ROI calculator;
- methodology page;
- methodology Excel export.

Recommended final deployment checks:

- deployed app opens successfully;
- incognito/private browser works;
- mobile browser works;
- cached datasets are available;
- direct live scrape behavior is acceptable;
- export buttons work after deployment.

---

## 26. Future Improvements

Potential improvements:

- Improve mobile-specific layout for wide data tables.
- Add scheduled cache refresh.
- Add map-based area clustering.
- Add more robust public endpoint detection if SPEEDHOME exposes stable public endpoints.
- Add screenshot capture for analysis evidence.
- Add more detailed property type classification.
- Add historical trend tracking.
- Add automated parser/analyzer tests.
- Add deployment-specific Playwright setup documentation.

---

## 27. Author

Built by **Ira Roni Yuda**.

This project demonstrates practical data-product thinking across scraping, data cleaning, analytics, UX, export, diagnostics, and business-oriented decision support.

## Live Demo

Public app link:

https://speedhome-price-intelligence-ira-roni.streamlit.app

