# Disability Employment ETL Pipeline

**MSBA Capstone — Enabled Talent** &nbsp;|&nbsp; Python · Census ACS API · BLS API · Google BigQuery · Looker Studio

> An end-to-end data pipeline that collects U.S. disability employment statistics from two federal data sources, cleans and standardizes them, loads them into BigQuery, and surfaces insights through a Looker Studio dashboard — built as a reusable, maintainable system for a real nonprofit client.

---

## Highlights

- Automated extraction of **818 BLS CPS labor series** and **4 Census ACS tables** spanning **52 states × 14 years**, producing **~85,000 rows** across 5 BigQuery production tables
- Diagnosed and fixed a silent API bug that had wiped **13 years of state-level data** (2010–2023) for two tables without any visible error — recovered by replacing a column-name blacklist with a Census variable-ID regex whitelist
- Ran correlation analysis on **1,700+ state-year observations**, finding that less-than-high-school attainment is the strongest negative predictor of disability employment rate (Pearson r = −0.79, p < 0.001)
- Delivered full client handover: validation tooling, a one-click pipeline launcher, and technical/non-technical documentation for the client to refresh the data independently

---

## Dashboard Preview

> Replace the images below with screenshots from your Looker Studio dashboard.

**National Trends**
![National employment trend dashboard](assets/dashboard_national.png)

**State-Level Map**
![State-level disability employment map](assets/dashboard_state_map.png)

**Correlation Analysis**
![Education vs employment rate scatter plot](assets/dashboard_correlation.png)

---

## Tech Stack

| Layer | Tools |
|---|---|
| Extraction | Python, Census ACS API, BLS API |
| Transformation | pandas, custom dimension decoders |
| Storage | Google BigQuery (`WRITE_TRUNCATE` refresh pattern) |
| Visualization | Looker Studio (connected directly to BigQuery) |
| Analysis | scipy (Pearson correlation), matplotlib (scatter plots) |
| Automation | `run_pipeline.bat` launcher, `validate/report.py` |

---

## Data Sources

| Source | BigQuery Table | Geography | Years |
|---|---|---|---|
| Census ACS 1-Year (S1810) | `acs_s1810` | State + County | 2010–2024 (no 2020) |
| Census ACS 1-Year (S1811) | `acs_s1811` | State + County | 2010–2024 (no 2020) |
| Census ACS 1-Year (B18120) | `acs_b18120` | State + County | 2010–2024 (no 2020) |
| Census ACS 1-Year (B18121) | `acs_b18121` | State + County | 2010–2024 (no 2020) |
| BLS CPS Disability Series | `bls_cps_disability` | National | 2008–2024 |

---

## Project Structure

```
├── extract/
│   ├── acs.py              # Census API extraction (ACS tables)
│   └── bls.py              # BLS CPS disability series extraction
├── transform/
│   ├── acs_transform.py    # ACS cleaning, percent→count, geo columns
│   └── bls_transform.py    # BLS cleaning, dimension decoding
├── load/
│   └── bigquery_loader.py  # BigQuery upload with column sanitization
├── analysis/
│   └── correlation.py      # Pearson correlation analysis + scatter plots
├── validate/
│   └── report.py           # Row counts, null checks, FIPS coverage
├── output/
│   ├── cleaned/            # Cleaned CSVs (ready for BigQuery)
│   ├── combined/           # ACS tables merged across all years
│   └── analysis/           # Correlation CSVs and plots
├── scripts/                # Backfill and utility scripts
├── bls_series.json         # 818 CPS series configuration
├── config.py               # Paths, years, table list, API config
├── main.py                 # Pipeline entry point (CLI)
├── run_pipeline.bat        # Windows one-click launcher
└── requirements.txt
```

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/dat-c-le/enabled-talent-etl-pipeline.git
cd enabled-talent-etl-pipeline
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in your keys:

```
CENSUS_API_KEY=your_key        # https://api.census.gov/data/key_signup.html
BLS_API_KEY=your_key           # https://data.bls.gov/registrationEngine/
GCP_PROJECT_ID=your_project_id
BQ_DATASET=disability_employment
GCP_CREDENTIALS_PATH=service_account.json
```

### 3. Run the pipeline

**Windows — double-click `run_pipeline.bat`**, or from the terminal:

```bash
python main.py                              # full pipeline
python main.py --step extract --source acs  # ACS only
python main.py --step extract --source bls  # BLS only
python main.py --step load                  # reload BigQuery only
```

---

## Key Design Decisions

**Why BigQuery instead of local files?** The client's analytics workflow is cloud-based. Storing cleaned data in BigQuery lets Looker Studio connect directly without any export step, and makes the refresh cycle one command.

**Why long/tidy format for CPS?** The BLS publishes 818 disability series with overlapping dimensions (sex, age, race, disability status, labor force status). A wide table would be 800+ columns. Long format lets Looker Studio filter by dimension rather than requiring a new column for every series.

**Why remove QCEW?** QCEW is general employment by industry with no disability breakdown — redundant with ACS and CPS, which already cover disability employment by industry and occupation. Removed to keep the dataset focused on the client's actual questions.
