# Disability Employment ETL Pipeline

**MSBA Capstone — Enabled Talent**

An end-to-end data pipeline that extracts, transforms, and loads U.S. disability employment statistics into BigQuery for analysis and visualization in Looker Studio.

---

## What It Does

1. **Extract** — Pulls ACS 1-year estimates from the U.S. Census Bureau API and employment data from the BLS API (2010–2023, skipping 2020)
2. **Transform** — Cleans column names, converts percent values to actual counts, and combines state + county rows into a single file per table/year
3. **Load** — Uploads all cleaned data into Google BigQuery for querying and visualization

---

## Data Sources

| Source | Tables | Description |
|---|---|---|
| Census ACS 1-Year | S1810 | Disability characteristics (population, type, demographics) |
| Census ACS 1-Year | S1811 | Employment & earnings for people with disabilities |
| Census ACS 1-Year | B18120 | Employment status by disability type |
| Census ACS 1-Year | B18121 | Work experience and earnings by disability status |
| BLS QCEW | — | State-level employment by industry (NAICS sectors) |
| BLS CPS | — | National disability employment series |

**Geography:** All 50 states + DC + Puerto Rico. County-level for ACS 1-year (counties with 65,000+ population).

---

## BigQuery Dataset

- **Project:** `msba-capstone-498915`
- **Dataset:** `disability_employment`
- **Tables:** `acs_s1810`, `acs_s1811`, `acs_b18120`, `acs_b18121`

Each table has a `level` column (`state` or `county`) and a `year` column so all years are stored in one table. Filter to `level = 'state'` for state-level analysis.

---

## Project Structure

```
├── extract/
│   ├── acs.py          # Census API extraction (ACS tables)
│   └── bls.py          # BLS API extraction (QCEW + CPS)
├── transform/
│   ├── acs_transform.py  # ACS cleaning, percent→count conversion, geo columns
│   └── bls_transform.py  # BLS cleaning
├── load/
│   └── bigquery_loader.py  # BigQuery upload
├── validate/
│   └── report.py       # Data quality checks
├── output/
│   └── cleaned/        # Cleaned CSVs (one per table × year)
├── config.py           # Table list, years, paths, API config
├── main.py             # Pipeline entry point
├── .env.example        # Environment variable template
└── requirements.txt    # Python dependencies
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

```bash
cp .env.example .env
```

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

# ACS only
python main.py --step extract --source acs
python main.py --step transform --source acs
```

The cleaned CSVs in `output/cleaned/` are already included in this repo — you can skip straight to `--step load` if you just want to reload BigQuery.

---

## Key Metrics Available

| Metric | Table | Column |
|---|---|---|
| Disability population (16+) | `acs_s1811` | `with_a_disability_population_age_16_and_over` |
| Employed with disability | `acs_s1811` | `with_a_disability_employed_population_age_16_and_over` |
| Total disability population | `acs_s1810` | `number_with_a_disability_number_with_a_disability` |
| Employment by disability type | `acs_b18120` | `in_the_labor_force_employed_with_a_disability` |

**Tip:** Use `with_a_disability_employed_population_age_16_and_over` for employment counts across all years (2010–2023). Other employment columns changed label names mid-series.
