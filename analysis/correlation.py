"""
Part 1 — Correlation analysis (state level, ACS).

Three analyses, each producing a tidy CSV (for Looker Studio) and a
scatter plot, plus a combined summary of Pearson r / p-value / n.

1. Education level vs disability employment rate       (S1811 only)
2. Disability type prevalence vs employment rate        (B18120 + S1810)
3. Talent pool size vs employment rate                  (S1810 + S1811)

Run: python -m analysis.correlation
"""
import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import pearsonr

import config

logger = logging.getLogger(__name__)

ANALYSIS_DIR = config.OUTPUT_DIR / "analysis"
PLOTS_DIR    = ANALYSIS_DIR / "plots"


def _load_state(table: str) -> pd.DataFrame:
    df = pd.read_csv(config.COMBINED_DIR / f"acs_{table}_combined.csv",
                      low_memory=False, dtype={"state_fips": str})
    return df[df["level"] == "state"].copy()


def _corr(x: pd.Series, y: pd.Series) -> tuple:
    mask = x.notna() & y.notna()
    x, y = x[mask], y[mask]
    if len(x) < 3:
        return np.nan, np.nan, len(x)
    r, p = pearsonr(x, y)
    return r, p, len(x)


def _scatter(df: pd.DataFrame, x: str, y: str, title: str, fname: str, r: float, p: float) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(df[x], df[y], alpha=0.5, s=20, color="#2E75B6")
    # Best-fit line
    mask = df[x].notna() & df[y].notna()
    if mask.sum() >= 2:
        z = np.polyfit(df.loc[mask, x], df.loc[mask, y], 1)
        xs = np.linspace(df.loc[mask, x].min(), df.loc[mask, x].max(), 100)
        ax.plot(xs, np.polyval(z, xs), color="#C00000", linewidth=1.5)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.set_title(f"{title}\nr = {r:.3f}, p = {p:.4f}, n = {mask.sum()}")
    fig.tight_layout()
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(PLOTS_DIR / fname, dpi=120)
    plt.close(fig)


# ── Analysis 1: Education vs employment rate (S1811) ───────────────────────

def analysis_education(summary_rows: list) -> pd.DataFrame:
    s1811 = _load_state("S1811")
    df = s1811[s1811["dis_edu_pop_25_plus"].notna()].copy()

    df["employment_rate"]       = df["dis_employed_total"] / df["dis_pop_16_plus"]
    df["edu_bachelors_share"]   = df["dis_edu_bachelors_plus"]   / df["dis_edu_pop_25_plus"]
    df["edu_some_college_share"]= df["dis_edu_some_college"]     / df["dis_edu_pop_25_plus"]
    df["edu_hs_grad_share"]     = df["dis_edu_hs_grad"]          / df["dis_edu_pop_25_plus"]
    df["edu_less_than_hs_share"]= df["dis_edu_less_than_hs"]     / df["dis_edu_pop_25_plus"]

    out = df[["state", "state_fips", "year", "employment_rate",
              "edu_bachelors_share", "edu_some_college_share",
              "edu_hs_grad_share", "edu_less_than_hs_share"]].copy()
    out_path = ANALYSIS_DIR / "education_vs_employment.csv"
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    logger.info(f"Saved: {out_path}  ({len(out)} rows)")

    for edu_col, label in [
        ("edu_bachelors_share",    "Bachelor's degree or higher"),
        ("edu_some_college_share", "Some college / associate degree"),
        ("edu_hs_grad_share",      "High school graduate, no college"),
        ("edu_less_than_hs_share", "Less than high school"),
    ]:
        r, p, n = _corr(out[edu_col], out["employment_rate"])
        summary_rows.append({
            "analysis": "Education vs Employment Rate",
            "x_variable": label, "y_variable": "Disability employment rate (16+)",
            "pearson_r": r, "p_value": p, "n": n,
        })
        _scatter(out, edu_col, "employment_rate",
                 f"{label} vs Employment Rate",
                 f"education_{edu_col}.png", r, p)

    return out


# ── Analysis 2: Disability type prevalence vs employment rate ──────────────

def analysis_disability_type(summary_rows: list) -> pd.DataFrame:
    b18120 = _load_state("B18120")
    s1810  = _load_state("S1810")

    type_cols = ["dis_type_hearing_18_64", "dis_type_vision_18_64",
                 "dis_type_cognitive_18_64", "dis_type_ambulatory_18_64",
                 "dis_type_self_care_18_64", "dis_type_indep_living_18_64"]
    s1810_sub = s1810[s1810[type_cols].notna().any(axis=1)][["state_fips", "year"] + type_cols]

    merged = b18120.merge(s1810_sub, on=["state_fips", "year"], how="inner")
    merged["employment_rate"] = merged["dis_employed"] / merged["pop_total"]

    label_map = {
        "dis_type_hearing_18_64":      "Hearing difficulty prevalence",
        "dis_type_vision_18_64":       "Vision difficulty prevalence",
        "dis_type_cognitive_18_64":    "Cognitive difficulty prevalence",
        "dis_type_ambulatory_18_64":   "Ambulatory difficulty prevalence",
        "dis_type_self_care_18_64":    "Self-care difficulty prevalence",
        "dis_type_indep_living_18_64": "Independent living difficulty prevalence",
    }
    for col in type_cols:
        merged[f"{col}_prevalence"] = merged[col] / merged["pop_total"]

    prevalence_cols = [f"{c}_prevalence" for c in type_cols]
    out = merged[["state", "state_fips", "year", "employment_rate"] + prevalence_cols].copy()
    out_path = ANALYSIS_DIR / "disability_type_vs_employment.csv"
    out.to_csv(out_path, index=False)
    logger.info(f"Saved: {out_path}  ({len(out)} rows)")

    for col in type_cols:
        prev_col = f"{col}_prevalence"
        label = label_map[col]
        r, p, n = _corr(out[prev_col], out["employment_rate"])
        summary_rows.append({
            "analysis": "Disability Type vs Employment Rate",
            "x_variable": label, "y_variable": "Overall disability employment rate (18-64)",
            "pearson_r": r, "p_value": p, "n": n,
        })
        _scatter(out, prev_col, "employment_rate",
                 f"{label} vs Employment Rate",
                 f"disability_type_{col}.png", r, p)

    return out


# ── Analysis 3: Talent pool size vs employment rate (S1810 + S1811) ────────

def analysis_talent_pool(summary_rows: list) -> pd.DataFrame:
    s1810 = _load_state("S1810")[["state", "state_fips", "year", "dis_pop_total", "pop_total"]]
    s1811 = _load_state("S1811")[["state_fips", "year", "dis_employed_total", "dis_pop_16_plus"]]

    merged = s1810.merge(s1811, on=["state_fips", "year"], how="inner")
    merged["employment_rate"]   = merged["dis_employed_total"] / merged["dis_pop_16_plus"]
    merged["talent_pool_size"]  = merged["dis_pop_total"]
    merged["talent_pool_log"]   = np.log(merged["talent_pool_size"])
    merged["talent_pool_share"] = merged["dis_pop_total"] / merged["pop_total"]  # prevalence, controls for state size

    out = merged[["state", "state_fips", "year", "employment_rate",
                  "talent_pool_size", "talent_pool_log", "talent_pool_share"]].copy()
    out_path = ANALYSIS_DIR / "talent_pool_vs_employment.csv"
    out.to_csv(out_path, index=False)
    logger.info(f"Saved: {out_path}  ({len(out)} rows)")

    for col, label in [
        ("talent_pool_size",  "Talent pool size (raw count)"),
        ("talent_pool_log",   "Talent pool size (log)"),
        ("talent_pool_share", "Talent pool share of total population"),
    ]:
        r, p, n = _corr(out[col], out["employment_rate"])
        summary_rows.append({
            "analysis": "Talent Pool Size vs Employment Rate",
            "x_variable": label, "y_variable": "Disability employment rate (16+)",
            "pearson_r": r, "p_value": p, "n": n,
        })
        _scatter(out, col, "employment_rate",
                 f"{label} vs Employment Rate",
                 f"talent_pool_{col}.png", r, p)

    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    summary_rows: list = []
    analysis_education(summary_rows)
    analysis_disability_type(summary_rows)
    analysis_talent_pool(summary_rows)

    summary = pd.DataFrame(summary_rows)
    summary_path = ANALYSIS_DIR / "correlation_summary.csv"
    summary.to_csv(summary_path, index=False)
    logger.info(f"Saved: {summary_path}")

    print("\n" + "=" * 78)
    print("CORRELATION SUMMARY")
    print("=" * 78)
    with pd.option_context("display.max_colwidth", 40, "display.width", 120):
        print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
