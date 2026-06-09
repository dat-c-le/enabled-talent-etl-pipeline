"""
BigQuery loader.

Loads cleaned CSVs from output/cleaned/ into BigQuery.

BigQuery column name rules
--------------------------
  - Must start with a letter or underscore.
  - May contain only letters, digits, and underscores.
  - Max 300 characters.

The ' | ' separator used in ACS column names is replaced with '__'
so the hierarchy is preserved in a BigQuery-safe form.
Example: "With a hearing difficulty | Employed" → "with_a_hearing_difficulty__employed"

Table naming convention
-----------------------
  Cleaned filename               → BigQuery table name
  acs_S1810_2022_cleaned.csv    → acs_s1810  (all years merged into one table)
  bls_qcew_cleaned.csv          → bls_qcew
  bls_cps_disability_cleaned.csv → bls_cps_disability

All years for the same ACS table are loaded into a single BigQuery table.
The 'year' column distinguishes rows.
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account

import config

logger = logging.getLogger(__name__)


# ── Column name sanitization ───────────────────────────────────────────────────

def sanitize_column(name: str) -> str:
    """
    Convert a human-readable column name to a BigQuery-safe identifier.
    Preserves hierarchy using double underscore for ' | ' and ' >> '.
    """
    col = name
    col = col.replace(" | ", "__").replace(" >> ", "__")
    col = re.sub(r"[^a-zA-Z0-9_]", "_", col)
    col = re.sub(r"_+", "_", col)
    col = col.strip("_").lower()
    if col and col[0].isdigit():
        col = "n_" + col
    return col[:300] if col else "unknown"


def sanitize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename all DataFrame columns to BigQuery-safe names."""
    mapping = {c: sanitize_column(c) for c in df.columns}
    # Handle duplicate sanitized names by appending a counter.
    seen: Dict[str, int] = {}
    final: Dict[str, str] = {}
    for orig, sanitized in mapping.items():
        if sanitized in seen:
            seen[sanitized] += 1
            final[orig] = f"{sanitized}_{seen[sanitized]}"
        else:
            seen[sanitized] = 0
            final[orig] = sanitized
    return df.rename(columns=final)


# ── BigQuery client ────────────────────────────────────────────────────────────

def get_bq_client(
    project_id: str = config.GCP_PROJECT_ID,
    credentials_path: str = config.GCP_CREDENTIALS_PATH,
) -> bigquery.Client:
    """
    Return an authenticated BigQuery client.
    Uses a service account key file if GCP_CREDENTIALS_PATH is set and exists;
    otherwise falls back to Application Default Credentials
    (e.g. from 'gcloud auth application-default login').
    """
    if credentials_path and os.path.isfile(credentials_path):
        creds = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return bigquery.Client(project=project_id, credentials=creds)
    return bigquery.Client(project=project_id)


# ── Dataset setup ─────────────────────────────────────────────────────────────

def create_dataset_if_not_exists(
    client: bigquery.Client,
    dataset_id: str = config.BQ_DATASET,
    location: str = "US",
) -> None:
    full_id = f"{client.project}.{dataset_id}"
    dataset  = bigquery.Dataset(full_id)
    dataset.location = location
    client.create_dataset(dataset, exists_ok=True)
    logger.info(f"Dataset ready: {full_id}")


# ── Table name resolution ─────────────────────────────────────────────────────

def csv_to_table_name(csv_path: Path) -> str:
    """
    Map a cleaned CSV filename to a BigQuery table name.
    All years for an ACS table are merged into one BQ table.

    acs_S1810_2022_cleaned.csv  → acs_s1810
    acs_B18120_2019_cleaned.csv → acs_b18120
    bls_qcew_cleaned.csv        → bls_qcew
    """
    stem = csv_path.stem  # e.g. "acs_S1810_2022_cleaned"
    name = stem.lower().replace("_cleaned", "")
    # Remove year suffix from ACS files (pattern: _YYYY or _YYYY_).
    name = re.sub(r"_\d{4}$", "", name)
    name = re.sub(r"[^a-z0-9_]", "_", name)
    return name


def _group_csvs_by_table(cleaned_dir: Path) -> Dict[str, List[Path]]:
    """Group cleaned CSV paths by their target BigQuery table name."""
    groups: Dict[str, List[Path]] = {}
    for p in sorted(cleaned_dir.glob("*.csv")):
        tbl = csv_to_table_name(p)
        groups.setdefault(tbl, []).append(p)
    return groups


# ── Load ──────────────────────────────────────────────────────────────────────

# Geographic/identifier columns that must be preserved as strings (e.g. FIPS codes with leading zeros).
_GEO_STR_COLS = {"state_fips", "county_fips", "fips", "geo_id", "state", "county",
                  "level", "survey_type", "area_code", "series_id"}


def _read_csv(path: Path) -> pd.DataFrame:
    """Read a cleaned CSV, keeping geo/identifier columns as strings."""
    headers = pd.read_csv(path, nrows=0).columns.tolist()
    dtype_map = {c: str for c in headers if c in _GEO_STR_COLS}
    return pd.read_csv(path, dtype=dtype_map, keep_default_na=False,
                       na_values=["", "NA", "N/A", "NaN", "nan", "NULL", "null"],
                       low_memory=False)


def load_table(
    client: bigquery.Client,
    table_name: str,
    csv_paths: List[Path],
    dataset_id: str = config.BQ_DATASET,
) -> None:
    """
    Load one or more cleaned CSVs into a single BigQuery table.
    All CSVs are concatenated; schemas are union-merged (missing columns → NaN).
    Uses WRITE_TRUNCATE to replace existing data on each run.
    """
    frames: List[pd.DataFrame] = []
    for p in csv_paths:
        try:
            df = _read_csv(p)
            frames.append(df)
        except Exception as exc:
            logger.error(f"Cannot read {p}: {exc}")

    if not frames:
        logger.warning(f"No data loaded for table {table_name}.")
        return

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined = sanitize_columns(combined)

    table_ref  = f"{client.project}.{dataset_id}.{table_name}"
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        autodetect=True,
    )

    job = client.load_table_from_dataframe(combined, table_ref, job_config=job_config)
    job.result()  # Wait for completion.

    loaded_table = client.get_table(table_ref)
    logger.info(
        f"Loaded {loaded_table.num_rows} rows into {table_ref} "
        f"({loaded_table.num_bytes / 1_000_000:.1f} MB)"
    )


def run_load(
    cleaned_dir: Path = config.CLEANED_DIR,
    project_id: str   = config.GCP_PROJECT_ID,
    dataset_id: str   = config.BQ_DATASET,
) -> None:
    """
    Load all cleaned CSVs into BigQuery.
    Prints a preview of each table before loading and asks for confirmation.
    """
    cleaned_dir.mkdir(parents=True, exist_ok=True)
    groups = _group_csvs_by_table(cleaned_dir)

    if not groups:
        logger.warning(f"No cleaned CSVs found in {cleaned_dir}. Run transform first.")
        return

    print("\n" + "=" * 70)
    print("BIGQUERY LOAD PREVIEW")
    print(f"  Project : {project_id}")
    print(f"  Dataset : {dataset_id}")
    print("=" * 70)
    for tbl, paths in sorted(groups.items()):
        n_files = len(paths)
        try:
            sample = pd.read_csv(paths[0], nrows=0)
            n_cols = len(sample.columns)
        except Exception:
            n_cols = "?"
        print(f"  {tbl:<40}  {n_files} file(s),  ~{n_cols} columns")
    print("=" * 70)

    answer = input("\nProceed with loading all tables? [yes/no]: ").strip().lower()
    if answer not in ("yes", "y"):
        print("Load cancelled.")
        return

    try:
        client = get_bq_client(project_id)
        create_dataset_if_not_exists(client, dataset_id)
    except Exception as exc:
        logger.error(f"Could not connect to BigQuery: {exc}")
        print(
            "\nERROR: Could not authenticate with BigQuery.\n"
            "Run setup_bigquery.py first to configure credentials."
        )
        return

    for tbl, paths in sorted(groups.items()):
        print(f"\nLoading -> {dataset_id}.{tbl} ...")
        try:
            load_table(client, tbl, paths, dataset_id)
            print(f"  Done.")
        except Exception as exc:
            logger.error(f"Load failed for {tbl}: {exc}", exc_info=True)
            print(f"  ERROR: {exc}")

    print(
        f"\nAll tables loaded into BigQuery dataset '{dataset_id}'.\n"
        f"Connect to Looker Studio at: https://lookerstudio.google.com\n"
        f"  -> Create -> Data source -> BigQuery -> Project: {project_id} "
        f"-> Dataset: {dataset_id}\n"
    )
