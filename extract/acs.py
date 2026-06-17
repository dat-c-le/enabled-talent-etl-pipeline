"""
ACS 1-year estimates extractor.

Fetches county-level data for tables S1810, S1811, B18120, B18121
from the Census Bureau API, years 2010–2023 (skipping 2020).

Outputs per table/year:
  output/raw/acs/acs_{TABLE}_{YEAR}_raw.csv
  output/raw/acs/acs_{TABLE}_{YEAR}_variables.json
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from tqdm import tqdm

import config

logger = logging.getLogger(__name__)

CENSUS_BASE = "https://api.census.gov/data"
# Census API allows max 50 items in the 'get' parameter.
# NAME counts as 1, leaving 49 slots; we use 48 to stay safe.
_MAX_VARS_PER_REQUEST = 48
# Seconds to wait between requests to avoid rate-limit errors.
_REQUEST_DELAY = 0.25


# ── Helpers ───────────────────────────────────────────────────────────────────

def _endpoint(year: int, table_type: str) -> str:
    base = f"{CENSUS_BASE}/{year}/acs/acs1"
    return f"{base}/subject" if table_type == "subject" else base


def _get(url: str, params: dict, retries: int = 3) -> Optional[requests.Response]:
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 404:
                logger.debug(f"404 — not available: {url}")
                return None
            logger.warning(f"HTTP {resp.status_code} on attempt {attempt+1}: {url}")
            time.sleep(2 ** attempt)
        except requests.RequestException as exc:
            logger.warning(f"Request error attempt {attempt+1}: {exc}")
            time.sleep(2 ** attempt)
    return None


# ── Variable metadata ─────────────────────────────────────────────────────────

def fetch_group_variables(year: int, table_id: str, table_type: str) -> Dict:
    """
    Fetch the variable definition JSON for a Census table.
    Returns dict of {var_id: {"label": ..., "concept": ..., ...}}.
    Returns {} if the table is not available for that year.
    """
    url = f"{_endpoint(year, table_type)}/groups/{table_id}.json"
    params = {"key": config.CENSUS_API_KEY} if config.CENSUS_API_KEY else {}
    resp = _get(url, params)
    if resp is None:
        return {}
    try:
        return resp.json().get("variables", {})
    except Exception as exc:
        logger.error(f"Could not parse variable JSON for {table_id}/{year}: {exc}")
        return {}


def classify_variables(
    variables: Dict,
) -> Tuple[List[str], Dict[str, Optional[str]], List[str]]:
    """
    Sort variables into three buckets:

    estimate_vars  — E suffix (keep as-is)
    pe_parent_map  — PE suffix → parent E var id (or None if no parent found)
    skip_vars      — M / PM suffix (margin of error, discarded)

    Annotation flags (EA, MA) and internal Census keys are also excluded.
    """
    estimate_vars: List[str] = []
    pe_parent_map: Dict[str, Optional[str]] = {}
    skip_vars: List[str] = []
    internal = {"for", "in", "ucgid"}
    available = set(variables.keys())

    for var_id, meta in variables.items():
        if var_id in internal:
            continue
        if var_id.endswith("EA") or var_id.endswith("MA"):
            continue  # annotation flags

        suffix = _var_suffix(var_id)
        if suffix == "M":
            skip_vars.append(var_id)
        elif suffix == "PE":
            pe_parent_map[var_id] = _find_pe_parent(var_id, available)
        elif suffix == "E":
            estimate_vars.append(var_id)

    return sorted(estimate_vars), pe_parent_map, skip_vars


def _var_suffix(var_id: str) -> str:
    # All Census data variable IDs contain an underscore (e.g. B18120_001E).
    # Geographic columns like "NAME" have no underscore and must be excluded.
    if "_" not in var_id:
        return ""
    if var_id.endswith("PE"):
        return "PE"
    if var_id.endswith("PM"):
        return "M"
    if var_id.endswith("M"):
        return "M"
    if var_id.endswith("E"):
        return "E"
    return ""


def _find_pe_parent(pe_var: str, available: set) -> Optional[str]:
    """
    Identify the total (denominator) E variable for a PE variable.

    Subject tables  (S####_C##_###PE):
        C03_NNN_PE → C01_NNN_E   (total-population denominator)
    Detailed tables (B#####_###PE):
        NNN_PE → NNN_E
    Returns None if no parent is found.
    """
    # Subject table pattern
    m = re.match(r"^(S\d+)_C(\d+)_(\d+)PE$", pe_var)
    if m:
        table, _grp, idx = m.groups()
        candidate = f"{table}_C01_{idx}E"
        return candidate if candidate in available else None

    # Detailed table pattern
    m = re.match(r"^([A-Z]\d+)_(\d+)PE$", pe_var)
    if m:
        table, idx = m.groups()
        candidate = f"{table}_{idx}E"
        return candidate if candidate in available else None

    return None


def _has_count_column(pe_var: str, estimate_set: set) -> bool:
    """
    For S-table C03 percent variables, check whether the corresponding
    C02 count variable already exists. If it does, the PE conversion
    would be redundant.
    """
    m = re.match(r"^(S\d+)_C(\d+)_(\d+)PE$", pe_var)
    if m:
        table, _grp, idx = m.groups()
        c02_var = f"{table}_C02_{idx}E"
        return c02_var in estimate_set
    return False


# ── Data fetching ─────────────────────────────────────────────────────────────

def _fetch_geo_data(
    year: int,
    table_id: str,
    table_type: str,
    var_ids: List[str],
    geo_for: str,
    geo_in: Optional[str] = None,
    merge_keys: List[str] = None,
) -> Optional[pd.DataFrame]:
    """
    Generic ACS data fetch. Handles batching and HTML-error detection.
    geo_for  : e.g. 'county:*' or 'state:*'
    geo_in   : e.g. 'state:*' (omit for state-level queries)
    merge_keys: FIPS columns to merge batches on (e.g. ['state','county'])
    """
    if merge_keys is None:
        merge_keys = ["state"]
    base = _endpoint(year, table_type)
    all_batches: List[pd.DataFrame] = []

    for i in range(0, len(var_ids), _MAX_VARS_PER_REQUEST):
        batch = var_ids[i : i + _MAX_VARS_PER_REQUEST]
        params: dict = {
            "get": "NAME," + ",".join(batch),
            "for": geo_for,
            "key": config.CENSUS_API_KEY,
        }
        if geo_in:
            params["in"] = geo_in

        resp = _get(base, params)
        if resp is None:
            return None
        if resp.text.strip().startswith("<"):
            logger.error(
                f"Census API returned HTML for {table_id}/{year} (geo={geo_for}). "
                "Check CENSUS_API_KEY in .env."
            )
            return None
        try:
            raw = resp.json()
        except Exception as exc:
            logger.error(f"JSON decode error for {table_id}/{year}: {exc}")
            return None

        df = pd.DataFrame(raw[1:], columns=raw[0])
        all_batches.append(df)
        time.sleep(_REQUEST_DELAY)

    if not all_batches:
        return None

    result = all_batches[0]
    for df in all_batches[1:]:
        merge_on  = [c for c in merge_keys if c in df.columns and c in result.columns]
        extra_cols = [c for c in df.columns if c not in merge_on and c != "NAME"]
        result = result.merge(df[merge_on + extra_cols], on=merge_on, how="outer")

    return result


def fetch_county_data(
    year: int,
    table_id: str,
    table_type: str,
    var_ids: List[str],
) -> Optional[pd.DataFrame]:
    """Fetch ACS county-level data (for=county:*&in=state:*)."""
    return _fetch_geo_data(
        year, table_id, table_type, var_ids,
        geo_for="county:*", geo_in="state:*",
        merge_keys=["state", "county"],
    )


def fetch_state_data(
    year: int,
    table_id: str,
    table_type: str,
    var_ids: List[str],
) -> Optional[pd.DataFrame]:
    """Fetch ACS state-level data (for=state:*)."""
    return _fetch_geo_data(
        year, table_id, table_type, var_ids,
        geo_for="state:*", geo_in=None,
        merge_keys=["state"],
    )


# ── Main extraction entry point ───────────────────────────────────────────────

def extract_table_year(
    year: int,
    table_id: str,
    table_type: str,
    raw_dir: Path,
) -> bool:
    """
    Extract one table × year combination at both county and state geography.
    Saves:
      {raw_dir}/acs_{table_id}_{year}_raw.csv        (county-level)
      {raw_dir}/acs_{table_id}_{year}_state_raw.csv  (state-level)
      {raw_dir}/acs_{table_id}_{year}_variables.json
    Returns True on success, False if the table/year is unavailable.
    """
    csv_path       = raw_dir / f"acs_{table_id}_{year}_raw.csv"
    state_csv_path = raw_dir / f"acs_{table_id}_{year}_state_raw.csv"
    meta_path      = raw_dir / f"acs_{table_id}_{year}_variables.json"

    # Fast path: county cached, only state is missing → reuse variable list from CSV headers
    if csv_path.exists() and meta_path.exists() and not state_csv_path.exists():
        logger.info(f"Extracting state data (county cached): {table_id}/{year}")
        try:
            # Only keep columns matching a real Census variable ID (e.g. B18120_001E,
            # S1810_C01_001E). Excludes geo columns and stale merge artifacts like
            # "NAME.1" left over from older cached CSVs.
            var_pattern = re.compile(r"^[A-Z]\d+(_C\d+)?_\d+(E|M|PE|PM)$")
            all_vars = [c for c in pd.read_csv(csv_path, nrows=0).columns if var_pattern.match(c)]
        except Exception as exc:
            logger.error(f"Cannot read county CSV headers for {table_id}/{year}: {exc}")
            return False
        state_df = fetch_state_data(year, table_id, table_type, all_vars)
        if state_df is not None and not state_df.empty:
            state_df.to_csv(state_csv_path, index=False)
            logger.info(f"Saved {state_csv_path.name}  ({len(state_df)} rows)")
        return True

    # Full cache hit
    if csv_path.exists() and meta_path.exists() and state_csv_path.exists():
        logger.info(f"Skip (cached): {csv_path.name}")
        return True

    # Full extraction needed
    logger.info(f"Extracting ACS {table_id} / {year}")
    variables = fetch_group_variables(year, table_id, table_type)
    if not variables:
        logger.warning(f"No variable metadata returned for {table_id}/{year} — skipping.")
        return False

    estimate_vars, pe_parent_map, _ = classify_variables(variables)
    estimate_set = set(estimate_vars)

    # Exclude PE vars whose count column already exists (C02 for S-table C03 vars).
    pe_to_convert: Dict[str, Optional[str]] = {
        v: parent
        for v, parent in pe_parent_map.items()
        if not _has_count_column(v, estimate_set)
    }

    all_vars = estimate_vars + list(pe_to_convert.keys())
    if not all_vars:
        logger.warning(f"No usable variables for {table_id}/{year} — skipping.")
        return False

    # County-level extraction
    df = fetch_county_data(year, table_id, table_type, all_vars)
    if df is None or df.empty:
        logger.warning(f"No county data returned for {table_id}/{year}.")
        return False
    df.to_csv(csv_path, index=False)

    # Save variable metadata (labels + PE parent mapping) for the transform step.
    meta = {
        "table_id":   table_id,
        "year":       year,
        "table_type": table_type,
        "variables":  {
            v: {"label": variables[v].get("label", v), "concept": variables[v].get("concept", "")}
            for v in variables
            if v in estimate_set or v in pe_to_convert
        },
        "pe_parents": {v: parent for v, parent in pe_to_convert.items()},
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    logger.info(f"Saved {csv_path.name}  ({len(df)} rows, {len(all_vars)} variables)")

    # State-level extraction (same variables, different geography)
    state_df = fetch_state_data(year, table_id, table_type, all_vars)
    if state_df is not None and not state_df.empty:
        state_df.to_csv(state_csv_path, index=False)
        logger.info(f"Saved {state_csv_path.name}  ({len(state_df)} rows)")

    return True


def run_acs_extraction(raw_dir: Path = config.ACS_RAW_DIR) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    jobs = [
        (year, tid, ttype)
        for tid, ttype in config.ACS_TABLES.items()
        for year in config.ACS_YEARS
    ]
    for year, table_id, table_type in tqdm(jobs, desc="ACS extraction"):
        try:
            extract_table_year(year, table_id, table_type, raw_dir)
        except Exception as exc:
            logger.error(f"Unhandled error for {table_id}/{year}: {exc}", exc_info=True)
