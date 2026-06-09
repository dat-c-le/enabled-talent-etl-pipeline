"""
Validation report generator.

Reads every cleaned CSV in output/cleaned/ and produces:
  output/reports/validation_report.csv   — one row per file/column with quality metrics
  output/reports/validation_summary.csv  — one row per file with pass/fail flags

Checks performed per column
---------------------------
  null_count        — number of NaN / empty values
  null_pct          — percentage of rows that are null
  unique_count      — number of distinct non-null values
  min / max         — for numeric columns
  negative_count    — count of values < 0 (unexpected for employment/count data)

File-level checks
-----------------
  row_count         — total rows
  duplicate_rows    — fully duplicated rows
  missing_fips      — rows where the 'fips' column is null (ACS files)
  year_range        — min and max year values found in the file
"""

import logging
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
from tqdm import tqdm

import config

logger = logging.getLogger(__name__)


def _column_stats(df: pd.DataFrame, col: str) -> dict:
    series = df[col]
    null_count = int(series.isna().sum())
    null_pct   = round(null_count / max(len(series), 1) * 100, 2)

    stats = {
        "column":       col,
        "dtype":        str(series.dtype),
        "null_count":   null_count,
        "null_pct":     null_pct,
        "unique_count": int(series.nunique(dropna=True)),
    }

    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() > 0:
        stats["min"]            = float(numeric.min())
        stats["max"]            = float(numeric.max())
        stats["negative_count"] = int((numeric < 0).sum())
        stats["mean"]           = round(float(numeric.mean()), 4)
    else:
        stats["min"]            = None
        stats["max"]            = None
        stats["negative_count"] = None
        stats["mean"]           = None

    return stats


def validate_file(csv_path: Path) -> dict:
    """
    Run all quality checks on a single cleaned CSV.
    Returns a dict with file-level metrics and a list of column-level dicts.
    """
    try:
        df = pd.read_csv(csv_path, low_memory=False)
    except Exception as exc:
        logger.error(f"Cannot read {csv_path}: {exc}")
        return {"file": csv_path.name, "error": str(exc), "column_stats": []}

    row_count       = len(df)
    duplicate_rows  = int(df.duplicated().sum())
    missing_fips    = int(df["fips"].isna().sum()) if "fips" in df.columns else None

    year_min = year_max = None
    if "year" in df.columns:
        years = pd.to_numeric(df["year"], errors="coerce").dropna()
        if not years.empty:
            year_min = int(years.min())
            year_max = int(years.max())

    col_stats = [_column_stats(df, col) for col in df.columns]

    total_nulls     = sum(s["null_count"] for s in col_stats)
    high_null_cols  = [s["column"] for s in col_stats if s["null_pct"] > 50]
    negative_cols   = [s["column"] for s in col_stats if (s["negative_count"] or 0) > 0]

    return {
        "file":            csv_path.name,
        "row_count":       row_count,
        "column_count":    len(df.columns),
        "duplicate_rows":  duplicate_rows,
        "missing_fips":    missing_fips,
        "year_min":        year_min,
        "year_max":        year_max,
        "total_nulls":     total_nulls,
        "high_null_cols":  "; ".join(high_null_cols) if high_null_cols else "",
        "negative_cols":   "; ".join(negative_cols) if negative_cols else "",
        "column_stats":    col_stats,
    }


def run_validation(
    cleaned_dir: Path  = config.CLEANED_DIR,
    reports_dir: Path  = config.REPORTS_DIR,
) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)

    cleaned_csvs = sorted(cleaned_dir.glob("*.csv"))
    if not cleaned_csvs:
        logger.warning(f"No cleaned CSVs found in {cleaned_dir}. Run transform first.")
        return

    all_column_rows: List[dict] = []
    all_summary_rows: List[dict] = []

    for csv_path in tqdm(cleaned_csvs, desc="Validation"):
        result = validate_file(csv_path)

        # Summary row (one per file).
        summary = {k: v for k, v in result.items() if k != "column_stats"}
        all_summary_rows.append(summary)

        # Column-level rows.
        for cs in result.get("column_stats", []):
            all_column_rows.append({"file": result["file"], **cs})

    # Write column-level report.
    col_report_path = reports_dir / "validation_column_detail.csv"
    pd.DataFrame(all_column_rows).to_csv(col_report_path, index=False)

    # Write file-level summary.
    summary_path = reports_dir / "validation_summary.csv"
    pd.DataFrame(all_summary_rows).to_csv(summary_path, index=False)

    logger.info(f"Validation report written to {reports_dir}")
    _print_summary(all_summary_rows)


def _print_summary(summary_rows: List[dict]) -> None:
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    for row in summary_rows:
        flags = []
        if row.get("duplicate_rows", 0):
            flags.append(f"{row['duplicate_rows']} duplicate rows")
        if row.get("high_null_cols"):
            flags.append(f"high-null columns: {row['high_null_cols']}")
        if row.get("negative_cols"):
            flags.append(f"negative values in: {row['negative_cols']}")
        status = "WARN  " if flags else "OK    "
        print(f"  {status} {row['file']}")
        if flags:
            for f in flags:
                print(f"           ↳ {f}")
    print("=" * 70)
    print(f"Full detail: output/reports/validation_column_detail.csv")
    print(f"Summary:     output/reports/validation_summary.csv\n")
