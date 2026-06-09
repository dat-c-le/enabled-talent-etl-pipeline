"""
ACS transform step.

Reads raw CSVs + variable metadata JSONs produced by extract/acs.py and outputs
cleaned CSVs with human-readable column names.

Column naming rules
-------------------
Census variable labels are structured like:
  "Estimate!!Total:!!With a disability:!!Employed"

This module converts them to the 'Parent | Sub category' format:
  "With a disability | Employed"

Steps:
  1. Split label on '!!'
  2. Strip 'Estimate' (always first token).
  3. Strip a leading bare 'Total' token only when other tokens follow it.
  4. Deduplicate consecutive identical tokens (occurs in some S-table labels).
  5. Join remaining tokens with ' | '.

Geography
---------
Each output file contains both state-level summary rows and county-level rows.
State rows appear first; counties follow sorted by state_fips → county_fips.
Added columns: survey_type, geo_id (UCGID format), level, state (name),
state_fips, county (name), county_fips, fips.

Percent → count conversion (three cases)
-----------------------------------------
1. C03 E-suffix columns (S1810/S1811): dropped when the equivalent C02 count
   column already exists — they are redundant.
2. PE-suffix columns (B18120/B18121): converted to counts via
       count = (PE_value / 100) * parent_total
   and renamed with "[derived count]" appended.
3. E-suffix mixed percent columns (S1811 C01/C02/C03): Census stores some
   breakdowns (e.g. class-of-worker shares) as raw percentages using E-suffix.
   Detected by two criteria — label chain is a sub-category of another E variable
   AND all values are ≤ 100 — then converted in-place using the same formula.
   Column names are unchanged after conversion.

Margin-of-error columns (M suffix) are always dropped.

Missing value codes
-------------------
Census uses large negative integers (-666666666 etc.) to represent
suppressed or unavailable data. All of these are replaced with NaN.

Output: output/cleaned/acs_{TABLE}_{YEAR}_cleaned.csv
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from tqdm import tqdm

import config

logger = logging.getLogger(__name__)

# All Census suppression / not-available sentinel values.
_CENSUS_MISSING = {
    -666666666, -888888888, -999999999,
    -222222222, -333333333, -555555555,
}


# ── Label parsing ─────────────────────────────────────────────────────────────

def label_to_column_name(label: str) -> str:
    """
    Convert a Census variable label to 'Parent | Sub category' format.

    Examples
    --------
    "Estimate!!Total:"
        → "Total"
    "Estimate!!Total:!!SEX!!Male"
        → "SEX | Male"
    "Estimate!!Total:!!With a hearing difficulty:!!In the labor force:!!Employed"
        → "With a hearing difficulty | In the labor force | Employed"
    "Estimate!!Number with a disability!!Number with a disability"
        → "Number with a disability"   (consecutive duplicate removed)
    """
    parts = [p.strip().rstrip(":") for p in label.split("!!")]
    parts = [p for p in parts if p and p.lower() != "estimate"]

    # Remove leading bare 'Total' when additional context follows.
    if len(parts) > 1 and parts[0].lower() == "total":
        parts = parts[1:]

    # Remove consecutive duplicate tokens (common in subject-table labels).
    deduped: List[str] = []
    for p in parts:
        if not deduped or p.lower() != deduped[-1].lower():
            deduped.append(p)
    parts = deduped

    return " | ".join(parts) if parts else "Total"


def _build_column_map(
    variable_meta: Dict,
    pe_parents: Dict[str, Optional[str]],
    estimate_vars_in_df: List[str],
) -> Dict[str, str]:
    """
    Return {raw_census_var_id: human_readable_column_name}.

    Guarantees uniqueness: if two variables parse to the same name,
    the variable ID is appended in parentheses for all but the first.
    """
    raw_map: Dict[str, str] = {}

    for var_id in estimate_vars_in_df:
        meta = variable_meta.get(var_id, {})
        label = meta.get("label", var_id)
        raw_map[var_id] = label_to_column_name(label)

    for pe_var, parent in pe_parents.items():
        if pe_var not in variable_meta:
            continue
        meta = variable_meta.get(pe_var, {})
        label = meta.get("label", pe_var)
        base_name = label_to_column_name(label)
        # Replace "Percent" with "Number [derived]" to indicate the conversion.
        derived_name = re.sub(r"\bPercent\b", "Number [derived]", base_name, flags=re.IGNORECASE)
        if derived_name == base_name:
            derived_name = base_name + " [derived count]"
        raw_map[pe_var] = derived_name

    # Deduplicate column names by appending var ID when collision occurs.
    seen: Dict[str, str] = {}  # col_name → first var that used it
    final_map: Dict[str, str] = {}
    for var_id, col_name in raw_map.items():
        if col_name not in seen:
            seen[col_name] = var_id
            final_map[var_id] = col_name
        else:
            # Collision: append short var ID to disambiguate.
            final_map[var_id] = f"{col_name} ({var_id})"

    return final_map


# ── Value cleaning ─────────────────────────────────────────────────────────────

def _to_numeric(val) -> float:
    """Parse a Census API string value to float; return NaN for missing codes."""
    try:
        n = float(val)
        if n in _CENSUS_MISSING or n < -100_000_000:
            return np.nan
        return n
    except (TypeError, ValueError):
        return np.nan


def _apply_numeric(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    for col in cols:
        if col in df.columns:
            df[col] = df[col].apply(_to_numeric)
    return df


# ── PE → count conversion ─────────────────────────────────────────────────────

def _derive_counts(
    df: pd.DataFrame,
    pe_parents: Dict[str, Optional[str]],
) -> pd.DataFrame:
    """
    For each PE variable, create a derived count column in-place.
    Formula: count = (PE_value / 100) * parent_total.
    If parent is None or parent column is absent, the result is NaN.
    """
    for pe_var, parent_var in pe_parents.items():
        if pe_var not in df.columns:
            continue
        if parent_var and parent_var in df.columns:
            pe_numeric     = df[pe_var].apply(_to_numeric)
            parent_numeric = df[parent_var].apply(_to_numeric)
            df[pe_var] = np.where(
                pe_numeric.isna() | parent_numeric.isna() | (parent_numeric == 0),
                np.nan,
                (pe_numeric / 100.0) * parent_numeric,
            )
        else:
            df[pe_var] = np.nan  # no parent found → leave blank
    return df


# ── E-suffix percent → count detection and conversion ─────────────────────────

def _find_e_percent_parents(
    est_cols: List[str],
    variable_meta: Dict,
    df: pd.DataFrame,
) -> Dict[str, str]:
    """
    Identify E-suffix variables that store percentage distributions rather than counts.

    Two conditions are required:
    1. Structural: the variable's label chain is a proper sub-category of another
       E-suffix variable's label chain (that other variable is the denominator).
    2. Value range: all non-null values are <= 100 (confirms the column is a percent).

    Returns {var_id: parent_var_id}.
    """
    # Build raw label chains (split on !!, strip Estimate, deduplicate consecutive duplicates)
    chains: Dict[str, Tuple] = {}
    for v in est_cols:
        label = variable_meta.get(v, {}).get("label", "")
        raw_parts = [p.strip().rstrip(":") for p in label.split("!!")]
        raw_parts = [p for p in raw_parts if p and p.lower() not in ("", "estimate")]
        deduped: List[str] = []
        for p in raw_parts:
            if not deduped or p.lower() != deduped[-1].lower():
                deduped.append(p)
        chains[v] = tuple(deduped)

    # Reverse map: label chain → variable ID (used for prefix lookup)
    chain_to_var: Dict[Tuple, str] = {chain: v for v, chain in chains.items()}

    pct_parents: Dict[str, str] = {}
    for v, chain in chains.items():
        if len(chain) <= 1:
            continue  # Root variable — no parent possible

        # Condition 2: all non-null values must be in the 0–100 percentage range
        if v not in df.columns:
            continue
        col_vals = df[v].dropna()
        if len(col_vals) > 0 and col_vals.max() > 100.5:
            continue  # Values exceed 100 → this is an absolute count column

        # Condition 1: find the longest prefix that maps to a different variable
        best_parent: Optional[str] = None
        best_len = 0
        for k in range(1, len(chain)):
            prefix = chain[:k]
            parent_var = chain_to_var.get(prefix)
            if parent_var and parent_var != v and k > best_len:
                best_len = k
                best_parent = parent_var

        if best_parent is not None:
            pct_parents[v] = best_parent

    return pct_parents


def _convert_e_percents(
    df: pd.DataFrame,
    e_pct_parents: Dict[str, str],
) -> pd.DataFrame:
    """
    Convert E-suffix percentage columns to derived count values in-place.
    Formula: count = (percent / 100) * parent_count
    """
    for pct_var, parent_var in e_pct_parents.items():
        if pct_var not in df.columns:
            continue
        if parent_var and parent_var in df.columns:
            pct_vals    = df[pct_var]   # already numeric from _apply_numeric
            parent_vals = df[parent_var]
            df[pct_var] = np.where(
                pct_vals.isna() | parent_vals.isna() | (parent_vals == 0),
                np.nan,
                (pct_vals / 100.0) * parent_vals,
            )
        else:
            df[pct_var] = np.nan  # no parent found → all-null → dropped by dropna
    return df


# ── Geographic columns ────────────────────────────────────────────────────────

def _add_geo_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add geographic columns. Handles rows at both state level (_level='state')
    and county level (_level='county').

    Consumes raw Census columns: NAME, state (FIPS), county (FIPS), ucgid, _level.
    Produces: survey_type, geo_id, level, state (name), state_fips,
              county (name), county_fips, fips.
    """
    # Pop the level indicator added during concat (default county for old data)
    level = df.pop("_level") if "_level" in df.columns else pd.Series("county", index=df.index, dtype=str)
    is_state_row = level == "state"

    # Read FIPS codes before dropping the raw columns
    state_fips = (df["state"].fillna("").astype(str).str.zfill(2)
                  if "state" in df.columns
                  else pd.Series("", index=df.index, dtype=str))
    county_raw = (df["county"].fillna("").astype(str)
                  if "county" in df.columns
                  else pd.Series("", index=df.index, dtype=str))

    # county_fips: blank for state rows, zero-padded for county rows
    county_fips = county_raw.str.zfill(3).where(
        (county_raw != "") & (county_raw != "nan") & (~is_state_row), ""
    )

    # Parse NAME into human-readable state and county names
    state_name  = pd.Series("", index=df.index, dtype=str)
    county_name = pd.Series("", index=df.index, dtype=str)
    if "NAME" in df.columns:
        name_str = df["NAME"].fillna("").astype(str)
        split = name_str.str.rsplit(", ", n=1, expand=True)
        if split.shape[1] >= 2:
            # County rows: "Jefferson County, Alabama" → county="Jefferson County", state="Alabama"
            # State rows:  "Alabama"                  → no comma; state=name_str, county=""
            county_name = split.iloc[:, 0].where(~is_state_row, "")
            state_name  = split.iloc[:, 1].fillna(name_str).where(~is_state_row, name_str)
        else:
            state_name = name_str

    # Drop raw geographic columns; replace with cleaned versions below
    df.drop(columns=["NAME", "state", "county", "ucgid"], errors="ignore", inplace=True)

    df["survey_type"] = "1-Year"
    df["geo_id"]      = ("0400000US" + state_fips).where(
                            is_state_row,
                            "0500000US" + state_fips + county_fips,
                        )
    df["level"]       = level
    df["state"]       = state_name
    df["state_fips"]  = state_fips
    df["county"]      = county_name
    df["county_fips"] = county_fips
    df["fips"]        = state_fips + county_fips

    return df


# ── Main transform ────────────────────────────────────────────────────────────

def transform_raw_file(
    raw_csv: Path,
    meta_json: Path,
    output_csv: Path,
    state_csv: Optional[Path] = None,
) -> bool:
    """
    Transform one raw ACS CSV file into a cleaned CSV.
    If state_csv is provided, state-level rows are prepended before county rows.
    Returns True on success.
    """
    try:
        county_df = pd.read_csv(raw_csv, dtype=str, low_memory=False)
        county_df["_level"] = "county"
    except Exception as exc:
        logger.error(f"Cannot read {raw_csv}: {exc}")
        return False

    if state_csv is not None:
        try:
            state_df = pd.read_csv(state_csv, dtype=str, low_memory=False)
            state_df["_level"] = "state"
            df = pd.concat([state_df, county_df], ignore_index=True)
        except Exception as exc:
            logger.warning(f"Could not read state CSV {state_csv.name}: {exc} — county-only")
            df = county_df
    else:
        df = county_df

    try:
        meta = json.loads(meta_json.read_text())
    except Exception as exc:
        logger.error(f"Cannot read {meta_json}: {exc}")
        return False

    year         = meta["year"]
    variable_meta = meta["variables"]   # {var_id: {"label": ..., "concept": ...}}
    pe_parents    = meta.get("pe_parents", {})

    # Geographic identifier columns returned by the Census API — never treat as data.
    _GEO_IDS = {"NAME", "state", "county", "ucgid"}

    # Identify which estimate and PE columns are actually in the DataFrame.
    est_cols = [c for c in df.columns if c in variable_meta and not c.endswith("PE") and c not in _GEO_IDS]
    pe_cols  = [c for c in df.columns if c in pe_parents and c not in _GEO_IDS]

    # S1810/S1811 store percent values in C03 E-suffix variables (e.g. S1810_C03_059E)
    # rather than PE-suffix. When the corresponding C02 count variable already exists,
    # these C03 percent-labeled E columns are redundant and must be dropped.
    all_est_set = set(est_cols)
    est_cols_clean = []
    for v in est_cols:
        label = variable_meta.get(v, {}).get("label", "")
        m = re.match(r"^(S\d+)_C(\d+)_(\d+)E$", v)
        if m and "percent" in label.lower():
            table, _grp, idx = m.groups()
            if f"{table}_C02_{idx}E" in all_est_set:
                continue  # C02 already has the count; skip this redundant percent column
        est_cols_clean.append(v)
    if len(est_cols_clean) < len(est_cols):
        logger.info(f"  Filtered {len(est_cols) - len(est_cols_clean)} redundant C03 percent E-suffix columns")
    est_cols = est_cols_clean

    # Convert PE columns to counts (in-place, before renaming).
    df = _derive_counts(df, {v: pe_parents[v] for v in pe_cols})

    # Convert all data columns to numeric.
    _apply_numeric(df, est_cols + pe_cols)

    # Detect E-suffix variables that store percentage distributions rather than counts.
    # S1811 C01 mixes absolute counts (e.g. employed total = 2,297,941) with percent
    # breakdowns within those counts (e.g. private for-profit workers = 70.3%).
    # Both use E-suffix; the only reliable distinguishers are label hierarchy (the
    # percent column's label is a sub-category of the count column's label) plus
    # value range (all values ≤ 100 confirms it is a percentage).
    # Conversion: count = (percent / 100) × parent_count. Column names unchanged.
    e_pct_parents = _find_e_percent_parents(est_cols, variable_meta, df)
    if e_pct_parents:
        logger.info(f"  Converting {len(e_pct_parents)} E-suffix percent columns to counts")
        df = _convert_e_percents(df, e_pct_parents)

    # Build column rename map.
    col_map = _build_column_map(variable_meta, pe_parents, est_cols)

    # Add year and geographic columns before renaming.
    df["year"] = year
    df = _add_geo_columns(df)

    # Select and rename: keep geo columns + data columns.
    geo_keep = [
        "year", "survey_type", "geo_id", "level",
        "state", "state_fips", "county", "county_fips", "fips",
    ]
    geo_keep = [c for c in geo_keep if c in df.columns]

    data_cols = est_cols + pe_cols
    keep = geo_keep + [c for c in data_cols if c in df.columns]
    df = df[keep].rename(columns=col_map)

    # Drop columns where every value is null:
    # - PE columns with no identifiable parent (conversion produced all NaN)
    # - Census variables suppressed for all counties in this year
    before = df.shape[1]
    df = df.dropna(axis=1, how="all")
    dropped = before - df.shape[1]
    if dropped:
        logger.info(f"  Dropped {dropped} all-null columns")

    # Sort: state rows first (for state-level summary), then counties within each state
    if "level" in df.columns and "state_fips" in df.columns:
        df["_sort"] = (df["level"] == "county").astype(int)
        df = df.sort_values(["_sort", "state_fips", "county_fips"]).drop(columns=["_sort"])
        df = df.reset_index(drop=True)

    # Fill NaN in geographic string columns so state rows don't have NULL county fields
    for _geo_col in ["state", "county", "county_fips"]:
        if _geo_col in df.columns:
            df[_geo_col] = df[_geo_col].fillna("").astype(str)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    logger.info(f"Cleaned: {output_csv.name}  ({len(df)} rows, {len(df.columns)} columns)")
    return True


def run_acs_transform(
    raw_dir: Path = config.ACS_RAW_DIR,
    cleaned_dir: Path = config.CLEANED_DIR,
) -> None:
    cleaned_dir.mkdir(parents=True, exist_ok=True)

    # Only process county raw CSVs (state raw files are combined during transform)
    raw_csvs = sorted(c for c in raw_dir.glob("acs_*_raw.csv") if "_state_raw" not in c.name)
    if not raw_csvs:
        logger.warning(f"No raw ACS CSVs found in {raw_dir}. Run extraction first.")
        return

    for raw_csv in tqdm(raw_csvs, desc="ACS transform"):
        stem      = raw_csv.stem.replace("_raw", "")      # acs_S1810_2022
        meta_json  = raw_dir / f"{stem}_variables.json"
        output_csv = cleaned_dir / f"{stem}_cleaned.csv"
        state_csv  = raw_dir / f"{stem}_state_raw.csv"

        if output_csv.exists():
            logger.info(f"Skip (cached): {output_csv.name}")
            continue

        if not meta_json.exists():
            logger.warning(f"Missing metadata for {raw_csv.name} — skipping.")
            continue

        transform_raw_file(
            raw_csv, meta_json, output_csv,
            state_csv=state_csv if state_csv.exists() else None,
        )
