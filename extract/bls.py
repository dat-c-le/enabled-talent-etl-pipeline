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

def _qcew_area_code(state_fips: str) -> str:
    """State-level QCEW area code: 2-digit FIPS padded to 5 chars with '000'."""
    return state_fips.zfill(2) + "000"


def fetch_qcew_record(
    year: int, state_fips: str, industry_code: str
) -> Optional[Dict]:
    """
    Fetch one annual QCEW record for a given state × industry.
    Returns the first dataset record dict, or None if unavailable.

    QCEW REST endpoint:
      /cew/data/api/{year}/a/area/{area}/industry/{industry}/ownership/0/
    Ownership 0 = Total, all ownerships.
    """
    area = _qcew_area_code(state_fips)
    url  = f"{QCEW_BASE}/{year}/a/area/{area}/industry/{industry_code}/ownership/0/"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        records = data.get("data", {}).get("dataset", [])
        return records[0] if records else None
    except Exception as exc:
        logger.debug(f"QCEW fetch failed ({year}/{state_fips}/{industry_code}): {exc}")
        return None


def run_qcew_extraction(raw_dir: Path = config.BLS_RAW_DIR) -> None:
    """
    Iterate over all years × states × industry codes and collect QCEW records.
    Saves to output/raw/bls/bls_qcew_raw.csv.

    Key fields retained from QCEW annual response:
      area_fips, industry_code, industry_title, year,
      annual_avg_emplvl   — average monthly employment for the year
      annual_avg_estabs   — average number of establishments
      total_annual_wages  — total wages paid in the year
      avg_annual_pay      — average annual pay per worker
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / "bls_qcew_raw.csv"

    if out_path.exists():
        logger.info(f"Skip (cached): {out_path.name}")
        return

    rows: List[Dict] = []
    jobs = [
        (year, fips, ind)
        for year   in config.ACS_YEARS
        for fips   in config.STATE_FIPS
        for ind    in config.QCEW_INDUSTRY_CODES
    ]

    for year, fips, industry in tqdm(jobs, desc="QCEW extraction"):
        rec = fetch_qcew_record(year, fips, industry)
        if rec:
            rows.append({
                "state_fips":         fips,
                "area_fips":          rec.get("area_fips", ""),
                "area_title":         rec.get("area_title", ""),
                "industry_code":      rec.get("industry_code", industry),
                "industry_title":     rec.get("industry_title", ""),
                "year":               year,
                "annual_avg_emplvl":  rec.get("annual_avg_emplvl", ""),
                "annual_avg_estabs":  rec.get("annual_avg_estabs_count", ""),
                "total_annual_wages": rec.get("tot_annual_wages", ""),
                "avg_annual_pay":     rec.get("annual_avg_wkly_wage", ""),
                "disclosure_code":    rec.get("disclosure_code", ""),
            })
        time.sleep(_REQUEST_DELAY)

    if not rows:
        logger.warning("No QCEW records retrieved. Check state FIPS and industry codes.")
        return

    pd.DataFrame(rows).to_csv(out_path, index=False)
    logger.info(f"QCEW saved: {out_path}  ({len(rows)} records)")


# ── BLS CPS disability series ─────────────────────────────────────────────────

def _load_bls_series_config() -> Dict[str, str]:
    """
    Read series IDs from bls_series.json.
    Returns {series_id: description}.
    """
    path = config.BLS_SERIES_FILE
    if not path.exists():
        logger.warning(f"{path} not found — skipping CPS extraction.")
        return {}
    try:
        cfg = json.loads(path.read_text())
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
    year_batches = _bls_year_batches(config.ACS_YEARS)

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
                    # Include only annual averages (period "M13") and yearly values.
                    period = obs.get("period", "")
                    if period not in ("M13", "Q05", "A01"):
                        continue
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
