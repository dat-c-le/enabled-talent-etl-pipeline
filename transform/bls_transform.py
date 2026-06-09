"""
BLS transform step.

Reads raw BLS CSVs and outputs cleaned, consistently typed CSVs.

QCEW
----
Input:  output/raw/bls/bls_qcew_raw.csv
Output: output/cleaned/bls_qcew_cleaned.csv

Key columns retained:
  state_fips, area_title (state name), industry_code, industry_title, year,
  annual_avg_emplvl  — average monthly employment for the year
  annual_avg_estabs  — average number of establishments
  total_annual_wages — total wages paid in the year ($)
  avg_annual_pay     — average annual pay per worker ($)

Records with a disclosure_code of 'N' are suppressed by BLS for confidentiality.
These rows are kept but their employment/wage values are set to NaN.

CPS disability
--------------
Input:  output/raw/bls/bls_cps_disability_raw.csv
Output: output/cleaned/bls_cps_disability_cleaned.csv

Pivoted so each row = (series_description, year) and
each column = one series_id value.
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

import config

logger = logging.getLogger(__name__)


# ── QCEW ─────────────────────────────────────────────────────────────────────

_QCEW_NUMERIC = [
    "annual_avg_emplvl",
    "annual_avg_estabs",
    "total_annual_wages",
    "avg_annual_pay",
]

_QCEW_RENAME = {
    "area_title":         "state_name",
    "annual_avg_emplvl":  "avg_monthly_employment",
    "annual_avg_estabs":  "avg_establishments",
    "total_annual_wages": "total_annual_wages_usd",
    "avg_annual_pay":     "avg_annual_pay_usd",
}


def transform_qcew(
    raw_path: Path,
    output_path: Path,
) -> bool:
    if not raw_path.exists():
        logger.warning(f"QCEW raw file not found: {raw_path}")
        return False

    df = pd.read_csv(raw_path, dtype=str, low_memory=False)

    # Suppress disclosed rows — BLS masks values with disclosure_code = 'N'.
    suppressed_mask = df.get("disclosure_code", pd.Series(dtype=str)) == "N"

    for col in _QCEW_NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df.loc[suppressed_mask, col] = np.nan

    # Cast year to integer.
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")

    # Keep only relevant columns.
    keep = [
        "state_fips", "area_title", "industry_code", "industry_title", "year",
        "annual_avg_emplvl", "annual_avg_estabs",
        "total_annual_wages", "avg_annual_pay",
        "disclosure_code",
    ]
    keep = [c for c in keep if c in df.columns]
    df = df[keep].rename(columns=_QCEW_RENAME)

    # Drop rows with no state or industry information.
    df = df.dropna(subset=["state_fips", "industry_code"])
    df = df.sort_values(["state_fips", "industry_code", "year"]).reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    logger.info(f"QCEW cleaned: {output_path.name}  ({len(df)} rows)")
    return True


# ── CPS disability ────────────────────────────────────────────────────────────

def transform_cps(
    raw_path: Path,
    output_path: Path,
) -> bool:
    if not raw_path.exists():
        logger.warning(f"CPS raw file not found: {raw_path}")
        return False

    df = pd.read_csv(raw_path, dtype=str, low_memory=False)
    if df.empty:
        logger.warning("CPS raw file is empty.")
        return False

    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["year"]  = pd.to_numeric(df["year"],  errors="coerce").astype("Int64")

    # Pivot: rows = year, columns = series_description.
    # Use series_description rather than series_id for readability.
    pivot = (
        df.pivot_table(
            index="year",
            columns="series_description",
            values="value",
            aggfunc="first",
        )
        .reset_index()
    )
    pivot.columns.name = None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pivot.to_csv(output_path, index=False)
    logger.info(f"CPS cleaned: {output_path.name}  ({len(pivot)} rows, {len(pivot.columns)} columns)")
    return True


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_bls_transform(
    raw_dir: Path    = config.BLS_RAW_DIR,
    cleaned_dir: Path = config.CLEANED_DIR,
) -> None:
    cleaned_dir.mkdir(parents=True, exist_ok=True)

    jobs = [
        (raw_dir / "bls_qcew_raw.csv",            cleaned_dir / "bls_qcew_cleaned.csv",            transform_qcew),
        (raw_dir / "bls_cps_disability_raw.csv",  cleaned_dir / "bls_cps_disability_cleaned.csv",  transform_cps),
    ]

    for raw_path, out_path, fn in tqdm(jobs, desc="BLS transform"):
        if out_path.exists():
            logger.info(f"Skip (cached): {out_path.name}")
            continue
        try:
            fn(raw_path, out_path)
        except Exception as exc:
            logger.error(f"Transform failed for {raw_path.name}: {exc}", exc_info=True)
