"""
BLS transform step — CPS disability series.

Input:  output/raw/bls/bls_cps_disability_raw.csv
Output: output/cleaned/bls_cps_disability_cleaned.csv

Tidy/long format — one row per series × year × period.
Columns: series_id, series_title, periodicity, seasonal_adjustment,
         disability_status, sex, age_group, race_ethnicity,
         labor_force_status, occupation, industry, class_of_worker,
         nilf_subcategory, year, period, date, value, footnotes
"""

import json
import logging
from pathlib import Path

import pandas as pd
from tqdm import tqdm

import config

logger = logging.getLogger(__name__)


# ── CPS disability ────────────────────────────────────────────────────────────

_DISA_LABELS = {0: "All persons", 1: "No disability", 2: "With disability"}
_SEXS_LABELS = {0: "Both sexes", 1: "Men", 2: "Women"}
_LFST_LABELS = {
    0:  "Population",
    10: "Civilian labor force",
    13: "Labor force participation rate",
    20: "Employed",
    23: "Employment-population ratio",
    25: "Employed, full time",
    26: "Employed, part time",
    30: "Unemployed",
    40: "Unemployment rate",
    50: "Not in labor force",
}
_AGES_LABELS = {
    0:  "16 years and over",
    8:  "16 to 19 years",
    11: "16 to 64 years",
    20: "20 to 24 years",
    28: "25 years and over",
    31: "25 to 34 years",
    38: "35 to 44 years",
    42: "45 to 54 years",
    49: "55 to 64 years",
    65: "65 years and over",
}
# race_ethnicity: Hispanic/Latino overrides race (per BLS Table 1 grouping)
def _decode_race_ethnicity(race_code: int, orig_code: int) -> str:
    if orig_code == 1:
        return "Hispanic or Latino"
    return {0: "All races/ethnicities", 1: "White", 3: "Black or African American", 4: "Asian"}.get(race_code, "All races/ethnicities")


import re as _re

def _parse_occupation(title: str) -> str:
    m = _re.search(r"[Pp]ercent employed in (.+?)(?:,\s*(?:Men|Women))?$", title)
    return m.group(1).strip() if m else ""

def _parse_industry(title: str) -> str:
    """Extract industry name: 'Percent employed in [NAME], Men/Women' or without sex."""
    m = _re.search(r"[Pp]ercent employed in (.+?)(?:,\s*(?:Men|Women))?$", title)
    return m.group(1).strip() if m else ""

def _parse_class_of_worker(title: str) -> str:
    """Extract class of worker: 'Percent employed as [NAME]'."""
    m = _re.search(r"[Pp]ercent employed as (.+?)(?:,\s*(?:Men|Women))?$", title)
    return m.group(1).strip() if m else ""

def _parse_nilf_subcategory(title: str) -> str:
    """Extract NILF subcategory from series title keywords."""
    t = title.lower()
    if "discouraged workers" in t:
        return "Discouraged workers"
    if "reasons other than discouragement" in t or ("marginally attached" in t and "discouraged" not in t):
        if "reasons other" in t:
            return "Other marginally attached"
        return "Marginally attached"
    if "want a job" in t:
        return "Wants a job"
    if "less than a high school" in t:
        return "Less than high school diploma"
    if "high school graduates, no college" in t:
        return "High school graduate, no college"
    if "some college or associate" in t:
        return "Some college or associate degree"
    if "bachelor" in t:
        return "Bachelor's degree or higher"
    return ""

# M01-M12 → calendar month number for date construction.
_MONTH_NUM = {f"M{i:02d}": i for i in range(1, 13)}
# Q01-Q04 → first month of that quarter.
_QUARTER_MONTH = {"Q01": 1, "Q02": 4, "Q03": 7, "Q04": 10}


def _period_to_date(year: int, period: str) -> str:
    """Convert BLS year + period code to an ISO date string (first of the period)."""
    if period.startswith("M") and period in _MONTH_NUM:
        return f"{year}-{_MONTH_NUM[period]:02d}-01"
    if period.startswith("Q") and period in _QUARTER_MONTH:
        return f"{year}-{_QUARTER_MONTH[period]:02d}-01"
    # Annual (A01) or unknown → January 1.
    return f"{year}-01-01"


def _load_series_metadata() -> pd.DataFrame:
    """Load series dimension metadata from bls_series.json."""
    path = config.BLS_SERIES_FILE
    if not path.exists():
        return pd.DataFrame()
    try:
        cfg = json.loads(path.read_text())
        series_dict = cfg.get("series", {})
        if not series_dict:
            return pd.DataFrame()
        first = next(iter(series_dict.values()))
        if not isinstance(first, dict):
            return pd.DataFrame()
        records = [{"series_id": sid, **meta} for sid, meta in series_dict.items()]
        return pd.DataFrame(records)
    except Exception as exc:
        logger.warning(f"Could not load series metadata: {exc}")
        return pd.DataFrame()


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

    # Join series metadata for dimension decoding.
    meta = _load_series_metadata()
    if not meta.empty:
        meta_cols = ["series_id","title","periodicity","seasonal","disa_code","sexs_code",
                     "ages_code","race_code","orig_code","lfst_code",
                     "occupation_code","indy_code","class_code"]
        meta_cols = [c for c in meta_cols if c in meta.columns]
        df = df.merge(meta[meta_cols], on="series_id", how="left")
        df["series_title"]        = df["title"].fillna(df.get("series_description", ""))
        df["seasonal_adjustment"] = df["seasonal"].map({"S": "Seasonally adjusted", "U": "Not seasonally adjusted"})
        df["disability_status"]   = df["disa_code"].map(_DISA_LABELS)
        df["sex"]                 = df["sexs_code"].map(_SEXS_LABELS)
        df["age_group"]           = df["ages_code"].map(_AGES_LABELS)
        df["race_ethnicity"]      = [_decode_race_ethnicity(int(rc), int(oc))
                                     for rc, oc in zip(df["race_code"].fillna(0), df["orig_code"].fillna(0))]
        df["labor_force_status"]  = df["lfst_code"].map(_LFST_LABELS)

        # Occupation (Table 3): occupation_code != 0
        occ_mask = df.get("occupation_code", pd.Series(0, index=df.index)).fillna(0).astype(int) != 0
        df["occupation"] = df["series_title"].where(occ_mask).map(lambda t: _parse_occupation(str(t)) if pd.notna(t) else "")
        df["occupation"] = df["occupation"].fillna("")

        # Industry (Table 4): indy_code != 0
        ind_mask = df.get("indy_code", pd.Series(0, index=df.index)).fillna(0).astype(int) != 0
        df["industry"] = df["series_title"].where(ind_mask).map(lambda t: _parse_industry(str(t)) if pd.notna(t) else "")
        df["industry"] = df["industry"].fillna("")

        # Class of worker (Table 4): class_code != 0
        cls_mask = df.get("class_code", pd.Series(0, index=df.index)).fillna(0).astype(int) != 0
        df["class_of_worker"] = df["series_title"].where(cls_mask).map(lambda t: _parse_class_of_worker(str(t)) if pd.notna(t) else "")
        df["class_of_worker"] = df["class_of_worker"].fillna("")

        # NILF subcategory (Table 5): series with specific keywords in title
        nilf_kw = "want a job|marginally attached|discouraged|high school|some college|bachelor"
        nilf_mask = df["series_title"].str.contains(nilf_kw, case=False, na=False)
        df["nilf_subcategory"] = df["series_title"].where(nilf_mask).map(lambda t: _parse_nilf_subcategory(str(t)) if pd.notna(t) else "")
        df["nilf_subcategory"] = df["nilf_subcategory"].fillna("")
    else:
        df["series_title"] = df.get("series_description", df["series_id"])
        for col in ("seasonal_adjustment","disability_status","sex","age_group","race_ethnicity",
                    "labor_force_status","occupation","industry","class_of_worker","nilf_subcategory","periodicity"):
            df[col] = ""

    # Convert occupation/industry percent-distribution series → counts in thousands.
    # These series title contains "Percent employed in" and have annual values 0–100.
    # Conversion: count_thousands = percent / 100 × total_employed_thousands
    # Denominator is total employed for same (disability_status, sex, year).
    _PERCENT_DENOM = {
        # (disa_code, sexs_code) → total employed series_id
        (2, 0): "LNU02074597",  # With disability, Both sexes
        (2, 1): "LNU02075630",  # With disability, Men
        (2, 2): "LNU02075704",  # With disability, Women
        (1, 0): "LNU02074593",  # No disability, Both sexes
        (1, 1): "LNU02075409",  # No disability, Men
        (1, 2): "LNU02075483",  # No disability, Women
    }
    pct_mask = df["series_title"].str.contains("Percent employed in|Percent employed as", na=False, regex=True)
    if pct_mask.any() and not meta.empty:
        # Build lookup: {(dc, sc, year): employed_thousands} using annual avg of monthly obs.
        # Denominator series are monthly-only (no A01), so average M01-M12 per year.
        monthly_mask = df["period"].str.match(r"^M\d\d$", na=False)
        monthly_df = df[monthly_mask][["series_id", "year", "value"]].copy()
        denom_lookup: dict = {}
        for (dc, sc), denom_sid in _PERCENT_DENOM.items():
            rows = monthly_df[monthly_df["series_id"] == denom_sid]
            annual_avg = rows.groupby("year")["value"].mean()
            for yr, avg_val in annual_avg.items():
                denom_lookup[(dc, sc, int(yr))] = avg_val

        def _convert_pct(row):
            if not pct_mask.loc[row.name]:
                return row["value"]
            key = (int(row.get("disa_code", -1)), int(row.get("sexs_code", -1)), int(row["year"]))
            denom = denom_lookup.get(key)
            if denom is None or pd.isna(denom) or pd.isna(row["value"]):
                return row["value"]
            return round(row["value"] / 100 * denom, 1)

        df["value"] = df.apply(_convert_pct, axis=1)
        n_converted = pct_mask.sum()
        logger.info(f"  Converted {n_converted:,} percent-distribution rows to counts (thousands)")

    # Build date column.
    df["date"] = [
        _period_to_date(int(y), str(p))
        for y, p in zip(df["year"].fillna(0).astype(int), df["period"].fillna("A01"))
    ]

    keep = [
        "series_id", "series_title", "periodicity", "seasonal_adjustment",
        "disability_status", "sex", "age_group", "race_ethnicity",
        "labor_force_status", "occupation", "industry", "class_of_worker", "nilf_subcategory",
        "year", "period", "date", "value", "footnotes",
    ]
    keep = [c for c in keep if c in df.columns]
    df = df[keep].sort_values(["series_id", "year", "period"]).reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    logger.info(f"CPS cleaned: {output_path.name}  ({len(df):,} rows, {len(df.columns)} columns)")
    return True


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_bls_transform(
    raw_dir: Path    = config.BLS_RAW_DIR,
    cleaned_dir: Path = config.CLEANED_DIR,
) -> None:
    cleaned_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "bls_cps_disability_raw.csv"
    out_path = cleaned_dir / "bls_cps_disability_cleaned.csv"
    if out_path.exists():
        logger.info(f"Skip (cached): {out_path.name}")
        return
    try:
        transform_cps(raw_path, out_path)
    except Exception as exc:
        logger.error(f"Transform failed for {raw_path.name}: {exc}", exc_info=True)
