import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CENSUS_API_KEY = os.getenv("CENSUS_API_KEY", "")
BLS_API_KEY    = os.getenv("BLS_API_KEY", "")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "msba-capstone-498915")
BQ_DATASET     = os.getenv("BQ_DATASET", "disability_employment")
GCP_CREDENTIALS_PATH = os.getenv("GCP_CREDENTIALS_PATH", "service_account.json")

# ── ACS ───────────────────────────────────────────────────────────────────────
# ACS 1-year estimates; 2020 was not released due to COVID-19 data collection disruption.
ACS_YEARS = [y for y in range(2010, 2025) if y != 2020]

# "subject" tables use a different Census API endpoint than "detailed" tables.
ACS_TABLES = {
    "S1810": "subject",   # Disability Characteristics
    "S1811": "subject",   # Selected Economic Characteristics for People with a Disability
    "B18120": "detailed", # Employment Status by Disability Status and Type
    "B18121": "detailed", # Work Experience by Disability Status and Type
}

# All 50 states + DC (11) + Puerto Rico (72).
# Territories AS(60), GU(66), MP(69), VI(78) are excluded: ACS 1-year county-level
# estimates are not published for them.
STATE_FIPS = [
    "01","02","04","05","06","08","09","10","11","12","13",
    "15","16","17","18","19","20","21","22","23","24","25",
    "26","27","28","29","30","31","32","33","34","35","36",
    "37","38","39","40","41","42","44","45","46","47","48",
    "49","50","51","53","54","55","56","72",
]

# ── BLS / QCEW ────────────────────────────────────────────────────────────────
# QCEW industry codes (2-digit NAICS sectors used by BLS QCEW).
# "10" = Total, all industries (QCEW aggregate, not a NAICS sector).
# "99" = Unclassified establishments.
# Multi-sector groups (3133, 4445, 4849) are QCEW aggregate codes for
# NAICS 31-33, 44-45, and 48-49 respectively.
QCEW_INDUSTRY_CODES = [
    "10",    # Total, all industries
    "11",    # Agriculture, Forestry, Fishing and Hunting
    "21",    # Mining, Quarrying, and Oil and Gas Extraction
    "22",    # Utilities
    "23",    # Construction
    "3133",  # Manufacturing (NAICS 31, 32, 33)
    "42",    # Wholesale Trade
    "4445",  # Retail Trade (NAICS 44, 45)
    "4849",  # Transportation and Warehousing (NAICS 48, 49)
    "51",    # Information
    "52",    # Finance and Insurance
    "53",    # Real Estate and Rental and Leasing
    "54",    # Professional, Scientific, and Technical Services
    "55",    # Management of Companies and Enterprises
    "56",    # Administrative and Support and Waste Management
    "61",    # Educational Services
    "62",    # Health Care and Social Assistance
    "71",    # Arts, Entertainment, and Recreation
    "72",    # Accommodation and Food Services
    "81",    # Other Services (except Public Administration)
    "92",    # Public Administration
    "99",    # Unclassified establishments
]

# ── Output directories ────────────────────────────────────────────────────────
OUTPUT_DIR      = Path("output")
ACS_RAW_DIR     = OUTPUT_DIR / "raw" / "acs"
BLS_RAW_DIR     = OUTPUT_DIR / "raw" / "bls"
CLEANED_DIR     = OUTPUT_DIR / "cleaned"
REPORTS_DIR     = OUTPUT_DIR / "reports"
BLS_SERIES_FILE = Path("bls_series.json")
