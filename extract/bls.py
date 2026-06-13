"""
BLS data extractor — two sources:

1. QCEW (Quarterly Census of Employment and Wages)
   State-level employment by NAICS industry, annual average, all ownerships.
   No API key required. Data available from 1975 onward.
   Output: output/raw/bls/bls_qcew_raw.csv

2. CPS disability series (national level only)
   Series IDs are read from bls_series.json (user-configurable).
   Requires a BLS API v2 key for >25 requests/day.
   Output: output/raw/bls/bls_cps_disability_raw.csv
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests
from tqdm import tqdm

import config

logger = logging.getLogger(__name__)

QCEW_BASE = "https://data.bls.gov/cew/data/api"
BLS_API_V2 = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

_REQUEST_DELAY = 0.5   # seconds between QCEW calls
_BLS_BATCH     = 50    # BLS v2 max series per request
_BLS_YEAR_SPAN = 20    # BLS v2 max year range per request


# ── QCEW ─────────────────────────────────────────────────────────────────────

# FIPS → state name for area_title column (50 states + DC + PR).
_STATE_NAMES: Dict[str, str] = {
    "01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas",
    "06": "California", "08": "Colorado", "09": "Connecticut", "10": "Delaware",
    "11": "District of Columbia", "12": "Florida", "13": "Georgia",
    "15": "Hawaii", "16": "Idaho", "17": "Illinois", "18": "Indiana",
    "19": "Iowa", "20": "Kansas", "21": "Kentucky", "22": "Louisiana",
    "23": "Maine", "24": "Maryland", "25": "Massachusetts", "26": "Michigan",
    "27": "Minnesota", "28": "Mississippi", "29": "Missouri", "30": "Montana",
    "31": "Nebraska", "32": "Nevada", "33": "New Hampshire", "34": "New Jersey",
    "35": "New Mexico", "36": "New York", "37": "North Carolina", "38": "North Dakota",
    "39": "Ohio", "40": "Oklahoma", "41": "Oregon", "42": "Pennsylvania",
    "44": "Rhode Island", "45": "South Carolina", "46": "South Dakota",
    "47": "Tennessee", "48": "Texas", "49": "Utah", "50": "Vermont",
    "51": "Virginia", "53": "Washington", "54": "West Virginia",
    "55": "Wisconsin", "56": "Wyoming", "72": "Puerto Rico",
}

# Industry titles by code (NAICS 2-digit + QCEW aggregates).
_INDUSTRY_TITLES: Dict[str, str] = {
    "10":    "Total, all industries",
    "11":    "Agriculture, Forestry, Fishing and Hunting",
    "21":    "Mining, Quarrying, and Oil and Gas Extraction",
    "22":    "Utilities",
    "23":    "Construction",
    "31-33": "Manufacturing",
    "42":    "Wholesale Trade",
    "44-45": "Retail Trade",
    "48-49": "Transportation and Warehousing",
    "51":    "Information",
    "52":    "Finance and Insurance",
    "53":    "Real Estate and Rental and Leasing",
    "54":    "Professional, Scientific, and Technical Services",
    "55":    "Management of Companies and Enterprises",
    "56":    "Administrative and Support and Waste Management",
    "61":    "Educational Services",
    "62":    "Health Care and Social Assistance",
    "71":    "Arts, Entertainment, and Recreation",
    "72":    "Accommodation and Food Services",
    "81":    "Other Services (except Public Administration)",
    "92":    "Public Administration",
    "99":    "Unclassified establishments",
}

# Config uses concatenated codes; CSV uses dashes. Map config → CSV format.
_CODE_TO_CSV: Dict[str, str] = {
    "3133": "31-33",
    "4445": "44-45",
    "4849": "48-49",
}


def fetch_qcew_state_year(year: int, state_fips: str) -> Optional[pd.DataFrame]:
    """
    Download the full annual QCEW CSV for one state/year.

    Endpoint: https://data.bls.gov/cew/data/api/{year}/a/area/{area_fips}.csv
    area_fips = 2-digit state FIPS + "000" (e.g. "01000" for Alabama).
    Returns a DataFrame of all rows, or None on failure.
    """
    import io
    area = state_fips.zfill(2) + "000"
    url  = f"{QCEW_BASE}/{year}/a/area/{area}.csv"
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return pd.read_csv(io.StringIO(resp.text), dtype=str)
    except Exception as exc:
        logger.debug(f"QCEW fetch failed ({year}/{state_fips}): {exc}")
        return None


def run_qcew_extraction(raw_dir: Path = config.BLS_RAW_DIR) -> None:
    """
    Download annual QCEW CSVs (one per state × year), aggregate all ownerships
    per industry, and save combined CSV.

    BLS endpoint: /cew/data/api/{year}/a/area/{area_fips}.csv
    - 728 requests total (52 states × 14 years).
    - Industry "10" total: taken directly from own_code=0 (provided by BLS).
    - All other 2-digit industries: sum own_codes 1+2+3+5 across agglvl_code=54 rows.
    - disclosure_code set to "N" if any component is suppressed.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / "bls_qcew_raw.csv"

    if out_path.exists():
        logger.info(f"Skip (cached): {out_path.name}")
        return

    # Normalize config industry codes to CSV format.
    csv_codes = {_CODE_TO_CSV.get(c, c) for c in config.QCEW_INDUSTRY_CODES}
    numeric_cols = ["annual_avg_emplvl", "annual_avg_estabs", "total_annual_wages", "avg_annual_pay"]
    jobs = [(year, fips) for year in config.ACS_YEARS for fips in config.STATE_FIPS]

    all_frames: List[pd.DataFrame] = []
    for year, fips in tqdm(jobs, desc="QCEW extraction"):
        df = fetch_qcew_state_year(year, fips)
        if df is None or df.empty:
            time.sleep(_REQUEST_DELAY)
            continue

        df["own_code"]      = df["own_code"].str.strip()
        df["industry_code"] = df["industry_code"].str.strip()
        df["agglvl_code"]   = df["agglvl_code"].str.strip()

        records: List[Dict] = []

        # "10" — Total all industries: own_code=0 row directly available.
        if "10" in csv_codes:
            row = df[(df["industry_code"] == "10") & (df["own_code"] == "0")]
            if not row.empty:
                r = row.iloc[0]
                records.append({
                    "industry_code":      "10",
                    "industry_title":     _INDUSTRY_TITLES["10"],
                    "annual_avg_emplvl":  r["annual_avg_emplvl"],
                    "annual_avg_estabs":  r["annual_avg_estabs"],
                    "total_annual_wages": r["total_annual_wages"],
                    "avg_annual_pay":     r["avg_annual_pay"],
                    "disclosure_code":    r.get("disclosure_code", ""),
                })

        # 2-digit NAICS sectors: agglvl_code=54, sum all ownerships (1+2+3+5).
        sector_targets = csv_codes - {"10"}
        sectors = df[(df["agglvl_code"] == "54") & (df["industry_code"].isin(sector_targets))].copy()
        for col in numeric_cols:
            sectors[col] = pd.to_numeric(sectors[col], errors="coerce")

        for ind_code, grp in sectors.groupby("industry_code"):
            has_suppression = grp["disclosure_code"].eq("N").any()
            totals = grp[numeric_cols].sum(min_count=1)
            records.append({
                "industry_code":      ind_code,
                "industry_title":     _INDUSTRY_TITLES.get(ind_code, ""),
                "annual_avg_emplvl":  totals["annual_avg_emplvl"],
                "annual_avg_estabs":  totals["annual_avg_estabs"],
                "total_annual_wages": totals["total_annual_wages"],
                "avg_annual_pay":     grp["avg_annual_pay"].apply(pd.to_numeric, errors="coerce").mean(),
                "disclosure_code":    "N" if has_suppression else "",
            })

        if records:
            frame = pd.DataFrame(records)
            frame["state_fips"] = fips
            frame["area_fips"]  = fips.zfill(2) + "000"
            frame["area_title"] = _STATE_NAMES.get(fips, "")
            frame["year"]       = year
            all_frames.append(frame[[
                "state_fips", "area_fips", "area_title", "industry_code", "industry_title",
                "year", "annual_avg_emplvl", "annual_avg_estabs",
                "total_annual_wages", "avg_annual_pay", "disclosure_code",
            ]])
        time.sleep(_REQUEST_DELAY)

    if not all_frames:
        logger.warning("No QCEW records retrieved. Check state FIPS and industry codes.")
        return

    result = pd.concat(all_frames, ignore_index=True)
    result.to_csv(out_path, index=False)
    logger.info(f"QCEW saved: {out_path}  ({len(result):,} records)")


# ── BLS CPS disability series ─────────────────────────────────────────────────

def _load_bls_series_config() -> Dict[str, str]:
    """
    Read series IDs from bls_series.json.
    Returns {series_id: title}.
    Supports both the new flat format {"series": {id: {title, ...}}}
    and the legacy nested format {"section": {"series": {id: title}}}.
    """
    path = config.BLS_SERIES_FILE
    if not path.exists():
        logger.warning(f"{path} not found — skipping CPS extraction.")
        return {}
    try:
        cfg = json.loads(path.read_text())
        # New flat format: top-level "series" key maps id → metadata dict.
        if "series" in cfg and isinstance(cfg["series"], dict):
            first = next(iter(cfg["series"].values()), None)
            if isinstance(first, dict):
                return {sid: meta["title"] for sid, meta in cfg["series"].items()}
            return cfg["series"]
        # Legacy nested format: sections with "series" sub-dicts of id → title strings.
        combined: Dict[str, str] = {}
        for section_key, section in cfg.items():
            if section_key.startswith("_"):
                continue
            if isinstance(section, dict) and "series" in section:
                combined.update(section["series"])
        return combined
    except Exception as exc:
        logger.error(f"Could not parse {path}: {exc}")
        return {}


def _bls_year_batches(years: List[int]) -> List[tuple]:
    """Split a list of years into (start, end) pairs that fit BLS API limits."""
    years_sorted = sorted(years)
    batches = []
    i = 0
    while i < len(years_sorted):
        start = years_sorted[i]
        end   = min(start + _BLS_YEAR_SPAN - 1, years_sorted[-1])
        batches.append((str(start), str(end)))
        i += sum(1 for y in years_sorted if start <= y <= end)
    return batches


def fetch_bls_series(
    series_ids: List[str],
    start_year: str,
    end_year: str,
) -> Optional[Dict]:
    """
    POST to BLS API v2 for a batch of series over a year range.
    Returns the parsed JSON response or None on failure.
    """
    payload: Dict = {
        "seriesid":  series_ids,
        "startyear": start_year,
        "endyear":   end_year,
    }
    if config.BLS_API_KEY:
        payload["registrationkey"] = config.BLS_API_KEY

    try:
        resp = requests.post(BLS_API_V2, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error(f"BLS API error ({start_year}–{end_year}): {exc}")
        return None


def run_cps_extraction(raw_dir: Path = config.BLS_RAW_DIR) -> None:
    """
    Fetch national CPS disability series from BLS API v2.
    Saves to output/raw/bls/bls_cps_disability_raw.csv.

    Output columns: series_id, series_description, year, period, value
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / "bls_cps_disability_raw.csv"

    if out_path.exists():
        logger.info(f"Skip (cached): {out_path.name}")
        return

    series_map = _load_bls_series_config()
    if not series_map:
        logger.warning("No BLS series configured. Edit bls_series.json to add series IDs.")
        return

    all_series_ids = list(series_map.keys())
    rows: List[Dict] = []
    year_batches = _bls_year_batches(config.CPS_YEARS)

    series_batches = [
        all_series_ids[i : i + _BLS_BATCH]
        for i in range(0, len(all_series_ids), _BLS_BATCH)
    ]

    for s_batch in tqdm(series_batches, desc="BLS CPS series batches"):
        for start_yr, end_yr in year_batches:
            result = fetch_bls_series(s_batch, start_yr, end_yr)
            if not result or result.get("status") != "REQUEST_SUCCEEDED":
                logger.warning(f"BLS request did not succeed: {result.get('message', '')}")
                continue

            for series_obj in result.get("Results", {}).get("series", []):
                sid = series_obj.get("seriesID", "")
                description = series_map.get(sid, "")
                for obs in series_obj.get("data", []):
                    # Keep annual averages (M13/Q05/A01) when present; otherwise
                    # keep all monthly observations (M01-M12) for series that
                    # do not publish a pre-computed annual average.
                    period = obs.get("period", "")
                    rows.append({
                        "series_id":          sid,
                        "series_description": description,
                        "year":               obs.get("year", ""),
                        "period":             period,
                        "value":              obs.get("value", ""),
                        "footnotes":          "; ".join(
                            f.get("text", "") for f in obs.get("footnotes", []) if f.get("text")
                        ),
                    })
            time.sleep(_REQUEST_DELAY)

    if not rows:
        logger.warning("No CPS disability data retrieved. Verify series IDs in bls_series.json.")
        return

    pd.DataFrame(rows).to_csv(out_path, index=False)
    logger.info(f"CPS disability saved: {out_path}  ({len(rows)} records)")
