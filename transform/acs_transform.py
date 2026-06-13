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


def _convert_labeled_percents(df: pd.DataFrame) -> pd.DataFrame:
    """
    Second-pass percent-to-count conversion that runs AFTER the column rename and
    normalization steps, operating on human-readable column names.

    Needed for early S1811 years (2010-2016) where Census stored occupation/industry/
    sector/employment breakdowns as E-suffix percentages using a flat label hierarchy.
    The pre-rename detection in _find_e_percent_parents misses these because the raw
    label chains don't show a parent-child relationship before normalization.

    Logic: for each column whose non-null values are all ≤ 100, look for the longest
    column-name prefix that matches another column in the DataFrame.  That other column
    is the denominator.  Formula: count = (pct / 100) × parent_count.
    """
    # Build chain map: col_name → tuple of label parts
    chains: Dict[str, tuple] = {col: tuple(col.split(" | ")) for col in df.columns}
    chain_to_col: Dict[tuple, str] = {v: k for k, v in chains.items()}

    converted = 0
    for col, chain in chains.items():
        if len(chain) <= 1:
            continue
        vals = df[col].dropna()
        if len(vals) == 0 or vals.max() > 100.5:
            continue  # Already a count or empty

        # Find longest prefix that maps to a different column (the parent count).
        best_parent: Optional[str] = None
        best_len = 0
        for k in range(1, len(chain)):
            parent_col = chain_to_col.get(chain[:k])
            if parent_col and parent_col != col and k > best_len:
                best_len = k
                best_parent = parent_col

        if best_parent is None:
            continue
        parent_vals = df[best_parent].dropna()
        if len(parent_vals) == 0 or parent_vals.max() <= 100.5:
            continue  # Parent also looks like a percent — skip

        df[col] = np.where(
            df[col].isna() | df[best_parent].isna() | (df[best_parent] == 0),
            np.nan,
            (df[col] / 100.0) * df[best_parent],
        )
        converted += 1

    if converted:
        logger.info(f"  Post-normalize: converted {converted} percent columns to counts")
    return df


# ── Cross-year column normalization ───────────────────────────────────────────

# Census rewrites S-table label hierarchies between releases, producing different
# column names for the same concept across years.  These rules map older variants
# to the canonical (2018+) label so BigQuery sees one consistent column name
# for every year without needing COALESCE views.
#
# S1811 changes detected across 2010-2023:
#   2010-2017  "[Group] | CLASS OF WORKER | [Category]"
#   2018+      "[Group] | Employed Population Age 16 and Over | CLASS OF WORKER | [Category]"
#
#   2010-2016  "[Group] | EMPLOYMENT STATUS | [Status]"
#   2017+      "[Group] | Population Age 16 and Over | EMPLOYMENT STATUS | [Status]"

_S1811_GROUPS = (
    "With a Disability",
    "No Disability",
    "Total Civilian Noninstitutionalized Population",
)

# (old_infix, canonical_infix) — matched after "{group} | "
_S1811_INFIX_UPGRADES = (
    (
        "CLASS OF WORKER | ",
        "Employed Population Age 16 and Over | CLASS OF WORKER | ",
    ),
    (
        "EMPLOYMENT STATUS | ",
        "Population Age 16 and Over | EMPLOYMENT STATUS | ",
    ),
    (
        "OCCUPATION | ",
        "Employed Population Age 16 and Over | OCCUPATION | ",
    ),
    (
        "INDUSTRY | ",
        "Employed Population Age 16 and Over | INDUSTRY | ",
    ),
)

# 2018 S1810 uses "Subject" as a middle segment that no other year has.
_S1810_2018_SUBJECT = "Subject"
_S1810_TCNP = "Total civilian noninstitutionalized population"

# Top-level categories that appear in C01/C02 labels WITHOUT the TCNP wrapper in 2010-2017.
# Any label starting with one of these (for C01) or having one of these as the second
# segment after "With a disability | " (for C02) needs TCNP inserted.
_S1810_KNOWN_CATEGORIES = {
    "SEX",
    "AGE",
    "RACE AND HISPANIC OR LATINO ORIGIN",
    "DISABILITY TYPE BY DETAILED AGE",
}


def _normalize_column_names(df: pd.DataFrame, table_id: str) -> pd.DataFrame:
    """
    Rename known Census label variants to their canonical names.
    Operates on human-readable column names after the col_map rename step.
    """
    tid = table_id.upper()
    rename_map: Dict[str, str] = {}
    existing = set(df.columns)

    if tid.startswith("S1811"):
        for col in df.columns:
            for grp in _S1811_GROUPS:
                prefix = f"{grp} | "
                if not col.startswith(prefix):
                    continue
                remainder = col[len(prefix):]
                for old_infix, new_infix in _S1811_INFIX_UPGRADES:
                    if remainder.startswith(old_infix):
                        canonical = prefix + new_infix + remainder[len(old_infix):]
                        if canonical != col and canonical not in existing:
                            rename_map[col] = canonical
                        break
                break

    elif tid.startswith("S1810"):
        for col in df.columns:
            subj_pipe = f"{_S1810_2018_SUBJECT} | "

            # ── 2018: "Subject" middle segment ─────────────────────────────────
            if col.startswith(subj_pipe):
                rest = col[len(subj_pipe):]
                canonical = rest if rest.startswith(_S1810_TCNP) else f"{_S1810_TCNP} | {rest}"
                if canonical not in existing:
                    rename_map[col] = canonical

            elif f" | {_S1810_2018_SUBJECT} | " in col:
                if f" | {_S1810_2018_SUBJECT} | {_S1810_TCNP}" in col:
                    canonical = col.replace(f" | {_S1810_2018_SUBJECT} | ", " | ")
                else:
                    canonical = col.replace(f" | {_S1810_2018_SUBJECT} | ", f" | {_S1810_TCNP} | ")
                if canonical not in existing:
                    rename_map[col] = canonical

            # ── 2010-2017: C01 columns — TCNP wrapper missing ──────────────────
            # Labels like "SEX | Male" or "AGE | 18 to 34 years" after label_to_column_name.
            elif col.split(" | ")[0] in _S1810_KNOWN_CATEGORIES:
                canonical = f"{_S1810_TCNP} | {col}"
                canonical = canonical.replace(" | One Race | ", " | ")
                if canonical not in existing:
                    rename_map[col] = canonical

            # ── 2010-2017: C02 columns — "With a disability | <cat> | ..." ─────
            elif col.startswith("With a disability | "):
                rest = col[len("With a disability | "):]
                first_seg = rest.split(" | ")[0]
                if first_seg == _S1810_TCNP:
                    pass  # Already canonical.
                elif first_seg in _S1810_KNOWN_CATEGORIES:
                    canonical = f"With a disability | {_S1810_TCNP} | {rest}"
                    canonical = canonical.replace(" | One Race | ", " | ")
                    if canonical not in existing:
                        rename_map[col] = canonical
                elif rest == "Hispanic or Latino (of any race)":
                    # 2010-2017: Hispanic flat label not nested under RACE category.
                    canonical = (f"With a disability | {_S1810_TCNP} | "
                                 f"RACE AND HISPANIC OR LATINO ORIGIN | {rest}")
                    if canonical not in existing:
                        rename_map[col] = canonical

    if rename_map:
        logger.info(f"  Normalized {len(rename_map)} column variants -> canonical labels")
        df = df.rename(columns=rename_map)
    return df


# ── Curated column schema ─────────────────────────────────────────────────────
# Maps the canonical human-readable label → short output name for each table.
# Only columns listed here survive into the cleaned CSV; everything else is dropped.
# Applied after _normalize_column_names so canonical labels are guaranteed.

_TABLE_SCHEMAS: Dict[str, Dict[str, str]] = {
    # ── S1810: Disability Population & Demographics ────────────────────────────
    "S1810": {
        "Total civilian noninstitutionalized population":                                                                                              "pop_total",
        "Total civilian noninstitutionalized population | SEX | Male":                                                                                 "pop_male",
        "Total civilian noninstitutionalized population | SEX | Female":                                                                               "pop_female",
        "Total civilian noninstitutionalized population | AGE | 18 to 34 years":                                                                       "pop_age_18_34",
        "Total civilian noninstitutionalized population | AGE | 35 to 64 years":                                                                       "pop_age_35_64",
        # 2010-2014 used coarser flat age buckets (no sub-breakdown into 18-34/35-64).
        "Population 18 to 64 years":                                                                                                                   "pop_age_18_64",
        "Population 65 years and over":                                                                                                                "pop_age_65_plus",
        "With a disability | Total civilian noninstitutionalized population":                                                                           "dis_pop_total",
        "With a disability | Total civilian noninstitutionalized population | SEX | Male":                                                              "dis_pop_male",
        "With a disability | Total civilian noninstitutionalized population | SEX | Female":                                                            "dis_pop_female",
        "With a disability | Total civilian noninstitutionalized population | RACE AND HISPANIC OR LATINO ORIGIN | White alone":                        "dis_pop_white",
        "With a disability | Total civilian noninstitutionalized population | RACE AND HISPANIC OR LATINO ORIGIN | Black or African American alone":    "dis_pop_black",
        "With a disability | Total civilian noninstitutionalized population | RACE AND HISPANIC OR LATINO ORIGIN | Asian alone":                        "dis_pop_asian",
        "With a disability | Total civilian noninstitutionalized population | RACE AND HISPANIC OR LATINO ORIGIN | Hispanic or Latino (of any race)":   "dis_pop_hispanic",
        "With a disability | Total civilian noninstitutionalized population | AGE | 18 to 34 years":                                                    "dis_pop_age_18_34",
        "With a disability | Total civilian noninstitutionalized population | AGE | 35 to 64 years":                                                    "dis_pop_age_35_64",
        "With a disability | Total civilian noninstitutionalized population | AGE | 65 to 74 years":                                                    "dis_pop_age_65_74",
        # 2010-2014 flat age buckets.
        "With a disability | Population 18 to 64 years":                                                                                               "dis_pop_age_18_64",
        "With a disability | Population 65 years and over":                                                                                            "dis_pop_age_65_plus",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With a hearing difficulty":             "dis_type_hearing_total",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With a hearing difficulty | Population 18 to 64 years":        "dis_type_hearing_18_64",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With a vision difficulty":              "dis_type_vision_total",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With a vision difficulty | Population 18 to 64 years":         "dis_type_vision_18_64",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With a cognitive difficulty":           "dis_type_cognitive_total",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With a cognitive difficulty | Population 18 to 64 years":      "dis_type_cognitive_18_64",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With an ambulatory difficulty":         "dis_type_ambulatory_total",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With an ambulatory difficulty | Population 18 to 64 years":    "dis_type_ambulatory_18_64",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With a self-care difficulty":           "dis_type_self_care_total",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With a self-care difficulty | Population 18 to 64 years":      "dis_type_self_care_18_64",
        "With a disability | Total civilian noninstitutionalized population | DISABILITY TYPE BY DETAILED AGE | With an independent living difficulty | Population 18 to 64 years": "dis_type_indep_living_18_64",
    },
    # ── S1811: Employment, Sector, Industry, Education ─────────────────────────
    "S1811": {
        # Population & employment status
        "Total Civilian Noninstitutionalized Population | Population Age 16 and Over":                                                                 "pop_16_plus",
        "Total Civilian Noninstitutionalized Population | Employed Population Age 16 and Over":                                                        "pop_employed",
        "With a Disability | Population Age 16 and Over":                                                                                              "dis_pop_16_plus",
        "With a Disability | Population Age 16 and Over | EMPLOYMENT STATUS | Employed":                                                               "dis_employed",
        "With a Disability | Population Age 16 and Over | EMPLOYMENT STATUS | Not in Labor Force":                                                     "dis_not_in_lf",
        "With a Disability | Employed Population Age 16 and Over":                                                                                     "dis_employed_total",
        "No Disability | Population Age 16 and Over":                                                                                                  "nodis_pop_16_plus",
        "No Disability | Population Age 16 and Over | EMPLOYMENT STATUS | Employed":                                                                   "nodis_employed",
        "No Disability | Employed Population Age 16 and Over":                                                                                         "nodis_employed_total",
        # Class of worker (sector)
        "With a Disability | Employed Population Age 16 and Over | CLASS OF WORKER | Private for-profit wage and salary workers":                      "dis_sector_private_forprofit",
        "With a Disability | Employed Population Age 16 and Over | CLASS OF WORKER | Employee of private company workers":                             "dis_sector_private_employee",
        "With a Disability | Employed Population Age 16 and Over | CLASS OF WORKER | Self-employed in own incorporated business workers":              "dis_sector_self_emp_inc",
        "With a Disability | Employed Population Age 16 and Over | CLASS OF WORKER | Private not-for-profit wage and salary workers":                  "dis_sector_nonprofit",
        "With a Disability | Employed Population Age 16 and Over | CLASS OF WORKER | Local government workers":                                        "dis_sector_local_govt",
        "With a Disability | Employed Population Age 16 and Over | CLASS OF WORKER | State government workers":                                        "dis_sector_state_govt",
        "With a Disability | Employed Population Age 16 and Over | CLASS OF WORKER | Federal government workers":                                      "dis_sector_federal_govt",
        "With a Disability | Employed Population Age 16 and Over | CLASS OF WORKER | Self-employed in own not incorporated business workers":          "dis_sector_self_emp_uninc",
        "With a Disability | Employed Population Age 16 and Over | CLASS OF WORKER | Unpaid family workers":                                           "dis_sector_unpaid_family",
        # Occupation
        "With a Disability | Employed Population Age 16 and Over | OCCUPATION | Management, business, science, and arts occupations":                  "dis_occ_management",
        "With a Disability | Employed Population Age 16 and Over | OCCUPATION | Service occupations":                                                  "dis_occ_service",
        "With a Disability | Employed Population Age 16 and Over | OCCUPATION | Sales and office occupations":                                         "dis_occ_sales_office",
        "With a Disability | Employed Population Age 16 and Over | OCCUPATION | Natural resources, construction, and maintenance occupations":          "dis_occ_natural_resources",
        "With a Disability | Employed Population Age 16 and Over | OCCUPATION | Production, transportation, and material moving occupations":          "dis_occ_production",
        # Industry
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Agriculture, forestry, fishing and hunting, and mining":                 "dis_ind_agriculture",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Construction":                                                           "dis_ind_construction",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Manufacturing":                                                          "dis_ind_manufacturing",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Wholesale trade":                                                        "dis_ind_wholesale",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Retail trade":                                                           "dis_ind_retail",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Transportation and warehousing, and utilities":                          "dis_ind_transportation",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Information":                                                            "dis_ind_information",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Finance and insurance, and real estate and rental and leasing":          "dis_ind_finance",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Professional, scientific, and management, and administrative and waste management services": "dis_ind_professional",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Educational services, and health care and social assistance":            "dis_ind_education_health",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Arts, entertainment, and recreation, and accommodation and food services": "dis_ind_arts_food",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Other services (except public administration)":                          "dis_ind_other_services",
        "With a Disability | Employed Population Age 16 and Over | INDUSTRY | Public administration":                                                  "dis_ind_public_admin",
        # Education
        "With a Disability | EDUCATIONAL ATTAINMENT | Population Age 25 and Over":                                                                     "dis_edu_pop_25_plus",
        "With a Disability | EDUCATIONAL ATTAINMENT | Population Age 25 and Over | Less than high school graduate":                                    "dis_edu_less_than_hs",
        "With a Disability | EDUCATIONAL ATTAINMENT | Population Age 25 and Over | High school graduate (includes equivalency)":                       "dis_edu_hs_grad",
        "With a Disability | EDUCATIONAL ATTAINMENT | Population Age 25 and Over | Some college or associate's degree":                               "dis_edu_some_college",
        "With a Disability | EDUCATIONAL ATTAINMENT | Population Age 25 and Over | Bachelor's degree or higher":                                       "dis_edu_bachelors_plus",
        "No Disability | EDUCATIONAL ATTAINMENT | Population Age 25 and Over | Bachelor's degree or higher":                                           "nodis_edu_bachelors_plus",
        # Work from home
        "With a Disability | COMMUTING TO WORK | Workers Age 16 and Over | Worked from home":                                                          "dis_work_from_home",
        "No Disability | COMMUTING TO WORK | Workers Age 16 and Over | Worked from home":                                                              "nodis_work_from_home",
    },
    # ── B18120: Labor Force Status by Disability Type ──────────────────────────
    "B18120": {
        "Total":                                                                      "pop_total",
        "In the labor force":                                                         "in_labor_force",
        "In the labor force | Employed":                                              "employed_total",
        "In the labor force | Employed | With a disability":                          "dis_employed",
        "In the labor force | Employed | With a disability | With a hearing difficulty":          "dis_employed_hearing",
        "In the labor force | Employed | With a disability | With a vision difficulty":           "dis_employed_vision",
        "In the labor force | Employed | With a disability | With a cognitive difficulty":        "dis_employed_cognitive",
        "In the labor force | Employed | With a disability | With an ambulatory difficulty":      "dis_employed_ambulatory",
        "In the labor force | Employed | With a disability | With a self-care difficulty":        "dis_employed_self_care",
        "In the labor force | Employed | With a disability | With an independent living difficulty": "dis_employed_indep_living",
        "In the labor force | Employed | No disability":                              "nodis_employed",
        "In the labor force | Unemployed | With a disability":                        "dis_unemployed",
        "In the labor force | Unemployed | No disability":                            "nodis_unemployed",
        "Not in labor force":                                                         "not_in_labor_force",
        "Not in labor force | With a disability":                                     "dis_not_in_lf",
        "Not in labor force | No disability":                                         "nodis_not_in_lf",
    },
    # ── B18121: Work Experience by Disability Type ─────────────────────────────
    "B18121": {
        "Total":                                                                      "pop_total",
        "Worked full-time, year round":                                               "fulltime_total",
        "Worked full-time, year round | With a disability":                           "dis_fulltime",
        "Worked full-time, year round | With a disability | With a hearing difficulty":          "dis_fulltime_hearing",
        "Worked full-time, year round | With a disability | With a vision difficulty":           "dis_fulltime_vision",
        "Worked full-time, year round | With a disability | With a cognitive difficulty":        "dis_fulltime_cognitive",
        "Worked full-time, year round | With a disability | With an ambulatory difficulty":      "dis_fulltime_ambulatory",
        "Worked full-time, year round | With a disability | With a self-care difficulty":        "dis_fulltime_self_care",
        "Worked full-time, year round | With a disability | With an independent living difficulty": "dis_fulltime_indep_living",
        "Worked full-time, year round | No disability":                               "nodis_fulltime",
        "Worked less than full-time, year round":                                     "parttime_total",
        "Worked less than full-time, year round | With a disability":                 "dis_parttime",
        "Worked less than full-time, year round | No disability":                     "nodis_parttime",
        "Did not work":                                                               "did_not_work_total",
        "Did not work | With a disability":                                           "dis_did_not_work",
        "Did not work | No disability":                                               "nodis_did_not_work",
    },
}


def _apply_column_schema(df: pd.DataFrame, table_id: str) -> pd.DataFrame:
    """
    Keep only whitelisted columns and rename them to short output names.
    Geo columns are always preserved. No-op for unknown table IDs.
    """
    schema = _TABLE_SCHEMAS.get(table_id.upper())
    if schema is None:
        return df

    geo_cols  = [c for c in df.columns if c in {
        "year", "survey_type", "geo_id", "level",
        "state", "state_fips", "county", "county_fips", "fips",
    }]
    data_keep = [c for c in df.columns if c in schema]

    dropped = len(df.columns) - len(geo_cols) - len(data_keep)
    if dropped:
        logger.info(f"  Schema filter: kept {len(data_keep)} / {len(df.columns) - len(geo_cols)} data columns")

    return df[geo_cols + data_keep].rename(columns=schema)


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

    # Normalize label variants so the same concept gets the same column name
    # across all years — Census periodically rewrites S-table label hierarchies.
    table_id = meta.get("table_id", "")
    df = _normalize_column_names(df, table_id)

    # Second-pass percent conversion on human-readable names.  Catches early-year
    # S1811 labels where the pre-rename detection missed the parent-child relationship.
    if table_id.upper().startswith("S1811"):
        df = _convert_labeled_percents(df)

    # Apply curated whitelist: keep only relevant columns and rename to short names.
    df = _apply_column_schema(df, table_id)

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
