# Disability Employment ETL Pipeline

**MSBA Capstone — Enabled Talent**

An end-to-end data pipeline that extracts, transforms, and loads U.S. disability employment statistics into BigQuery for analysis and visualization in Looker Studio.

---

## What It Does

1. **Extract** — Pulls ACS 1-year estimates from the U.S. Census Bureau API, CPS disability series from the BLS API, and QCEW employment data from BLS flat files
2. **Transform** — Cleans column names, converts percent values to actual counts, decodes dimension codes into readable labels, and standardizes formats across all sources
3. **Load** — Uploads all cleaned data into Google BigQuery for querying and visualization

---

## Data Sources

| Source | BigQuery Table | Geography | Years | Description |
|---|---|---|---|---|
| Census ACS 1-Year | `acs_s1810` | State + County | 2010–2024 (no 2020) | Disability characteristics — population, type, demographics |
| Census ACS 1-Year | `acs_s1811` | State + County | 2010–2024 (no 2020) | Employment & earnings for people with disabilities |
| Census ACS 1-Year | `acs_b18120` | State + County | 2010–2024 (no 2020) | Employment status by disability type |
| Census ACS 1-Year | `acs_b18121` | State + County | 2010–2024 (no 2020) | Work experience and earnings by disability status |
| BLS CPS | `bls_cps_disability` | National only | 2008–2024 | Monthly + annual disability labor force series |

**ACS geography:** All 50 states + DC + Puerto Rico. County-level for counties with 65,000+ population. Filter `level = 'state'` for state-only analysis.

**CPS note:** National totals only — no state breakdown. Disability-specific series are not seasonally adjusted by BLS (sample too small); general population totals include seasonally adjusted series.

---

## BigQuery Dataset

- **Project:** `msba-capstone-498915`
- **Dataset:** `disability_employment`
- **Tables:** `acs_s1810`, `acs_s1811`, `acs_b18120`, `acs_b18121`, `bls_cps_disability`

---

## Project Structure

```
├── extract/
│   ├── acs.py              # Census API extraction (ACS tables)
│   └── bls.py              # BLS extraction (QCEW flat files + CPS series)
├── transform/
│   ├── acs_transform.py    # ACS cleaning, percent→count, geo columns
│   └── bls_transform.py    # BLS cleaning, dimension decoding, percent→count
├── load/
│   └── bigquery_loader.py  # BigQuery upload with column sanitization
├── output/
│   ├── cleaned/            # Cleaned CSVs (ready for BigQuery)
│   └── combined/           # ACS tables merged across all years
├── scripts/                # Utility and one-off scripts
├── bls_series.json         # CPS series configuration (815 series)
├── config.py               # Table list, years, paths, API config
├── main.py                 # Pipeline entry point
├── .env.example            # Environment variable template
└── requirements.txt        # Python dependencies
```

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/dat-c-le/enabled-talent-etl-pipeline.git
cd enabled-talent-etl-pipeline
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your keys:

```
CENSUS_API_KEY=your_key_here        # https://api.census.gov/data/key_signup.html
BLS_API_KEY=your_key_here           # https://data.bls.gov/registrationEngine/
GCP_PROJECT_ID=your_gcp_project_id
BQ_DATASET=disability_employment
GCP_CREDENTIALS_PATH=service_account.json
```

### 4. Set up BigQuery credentials

Place your GCP service account JSON file at the path specified in `GCP_CREDENTIALS_PATH` (default: `service_account.json`). See [Google Cloud docs](https://cloud.google.com/iam/docs/creating-managing-service-accounts) for how to create one.

---

## Running the Pipeline

```bash
# Full pipeline (extract → transform → load)
python main.py

# Individual steps
python main.py --step extract
python main.py --step transform
python main.py --step load

# Source-specific
python main.py --step extract --source acs
python main.py --step extract --source bls
python main.py --step transform --source acs
python main.py --step transform --source bls
```

The cleaned CSVs in `output/cleaned/` are already committed to this repo — you can skip straight to `--step load` to reload BigQuery without re-extracting.

---

## CPS Disability Dataset (`bls_cps_disability`)

The CPS dataset is in **tidy/long format** — one row per series × year × period.

### Key columns

| Column | Description |
|---|---|
| `series_id` | BLS series ID (`LNU0` = not seasonally adjusted, `LNS1` = seasonally adjusted) |
| `periodicity` | `M` monthly, `A` annual, `Q` quarterly |
| `seasonal_adjustment` | Seasonally adjusted / Not seasonally adjusted |
| `disability_status` | With disability / No disability / All persons |
| `sex` | Both sexes / Men / Women |
| `age_group` | 16 years and over, 16 to 64 years, 65 years and over, and sub-ranges |
| `race_ethnicity` | All races/ethnicities / White / Black or African American / Asian / Hispanic or Latino |
| `labor_force_status` | Employed / Unemployed / Unemployment rate / Labor force participation rate / Not in labor force / etc. |
| `occupation` | Occupation group (Table 3 series, With/No disability, annual only) |
| `industry` | Industry sector (Table 4 series, With disability, annual only) |
| `class_of_worker` | Class of worker (Table 4 series, With disability, annual only) |
| `nilf_subcategory` | NILF detail or education level (Table 5 series, With disability, annual only) |
| `year` | Calendar year |
| `period` | BLS period code: `M01`–`M12`, `Q01`–`Q04`, `A01` |
| `date` | ISO date of the first day of the period |
| `value` | Measured value — counts in thousands or percent (see `labor_force_status`) |

**Occupation, industry, and class-of-worker values** are counts in thousands, converted from BLS percent distributions using the annual-average total employed as denominator.

---

## Key Metrics by Use Case

### National disability employment trends (monthly)
Use `bls_cps_disability` — filter `disability_status = 'With disability'` and `labor_force_status` to the metric of interest. Use `period` to select monthly (`M01`–`M12`) or annual (`A01`) data.

### State-level disability employment
Use `acs_s1811` — filter `level = 'state'`. Key columns:
- `with_a_disability_employed_population_age_16_and_over` — employment count
- `with_a_disability_unemployment_rate` — unemployment rate

### Disability type breakdown
Use `acs_b18120` — employment status broken down by disability type (hearing, vision, cognitive, ambulatory, self-care, independent living).

