"""
Part 2 — Predictive regression model.

Target  : National disability employment-population ratio (BLS CPS),
          annual average computed from the monthly NSA series
          LNU02374597 (With disability, Both sexes, All races, 16+).
Features: year + national ACS education-attainment shares for the
          population with a disability, age 25+ (bachelor's+, some
          college, HS grad, less than HS). Disability-type and
          work-from-home features were dropped — they only have
          5 overlapping annual observations with the target series,
          too few to fit reliably (see correlation analysis for those
          relationships instead).

Models: Linear Regression (with OLS prediction intervals) and
        Random Forest (with across-tree prediction spread as an
        approximate interval), evaluated via leave-one-out CV given
        the small sample (n=11).

Forecast: 2025-2027, using linear trend extrapolation of each
          feature, fed into the trained outcome models.

Run: python -m analysis.regression
"""
import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import LeaveOneOut
from sklearn.metrics import mean_absolute_error, r2_score

import config

logger = logging.getLogger(__name__)

ANALYSIS_DIR = config.OUTPUT_DIR / "analysis"
PLOTS_DIR    = ANALYSIS_DIR / "plots"
FORECAST_YEARS = [2025, 2026, 2027]

FEATURE_COLS = ["edu_bachelors_share", "edu_some_college_share", "edu_hs_grad_share"]
# The four education shares (bachelors+/some college/HS grad/less-than-HS)
# sum to ~1.0 every year by construction (they partition the population).
# Including all four with an intercept is a perfect-collinearity dummy-
# variable trap — coefficients become numerically unstable and meaningless.
# "edu_less_than_hs_share" is dropped as the reference category; the three
# remaining coefficients represent each group's effect relative to that
# less-than-HS baseline.


# ── Build target series ─────────────────────────────────────────────────

def build_target() -> pd.DataFrame:
    cps = pd.read_csv(config.CLEANED_DIR / "bls_cps_disability_cleaned.csv", low_memory=False)
    monthly = cps[(cps["series_id"] == "LNU02374597") & (cps["period"].str.match(r"M\d\d", na=False))]
    annual = monthly.groupby("year").agg(value=("value", "mean"), n_months=("value", "count")).reset_index()
    annual = annual[annual["n_months"] == 12]  # drop partial year (2008 has only 7 months)
    return annual[["year", "value"]].rename(columns={"value": "employment_pop_ratio"})


# ── Build national education features ──────────────────────────────────

def build_features() -> pd.DataFrame:
    s1811 = pd.read_csv(config.COMBINED_DIR / "acs_S1811_combined.csv", low_memory=False,
                         dtype={"state_fips": str})
    state = s1811[s1811["level"] == "state"]
    edu_detail_cols = ["dis_edu_pop_25_plus", "dis_edu_bachelors_plus", "dis_edu_some_college",
                        "dis_edu_hs_grad", "dis_edu_less_than_hs"]
    # Require all five education columns present — some years only have the
    # population total with NaN breakdowns, which would otherwise sum to a
    # false "0% share" instead of being excluded.
    state = state[state[edu_detail_cols].notna().all(axis=1)]

    nat = state.groupby("year").agg(
        edu_pop_25_plus=("dis_edu_pop_25_plus", "sum"),
        edu_bachelors=("dis_edu_bachelors_plus", "sum"),
        edu_some_college=("dis_edu_some_college", "sum"),
        edu_hs_grad=("dis_edu_hs_grad", "sum"),
        edu_less_than_hs=("dis_edu_less_than_hs", "sum"),
    ).reset_index()

    nat["edu_bachelors_share"]    = nat["edu_bachelors"]     / nat["edu_pop_25_plus"]
    nat["edu_some_college_share"] = nat["edu_some_college"]  / nat["edu_pop_25_plus"]
    nat["edu_hs_grad_share"]      = nat["edu_hs_grad"]       / nat["edu_pop_25_plus"]
    # edu_less_than_hs_share is intentionally not a feature (collinearity —
    # see FEATURE_COLS note above) but kept here for reference/QA.
    nat["edu_less_than_hs_share"] = nat["edu_less_than_hs"]  / nat["edu_pop_25_plus"]

    return nat[["year"] + FEATURE_COLS + ["edu_less_than_hs_share"]]


# ── Feature extrapolation for forecast years ────────────────────────────

def extrapolate_features(features: pd.DataFrame) -> pd.DataFrame:
    """Linear trend extrapolation of each feature to FORECAST_YEARS."""
    rows = []
    for yr in FORECAST_YEARS:
        row = {"year": yr}
        for col in FEATURE_COLS:
            coeffs = np.polyfit(features["year"], features[col], 1)
            row[col] = np.polyval(coeffs, yr)
        rows.append(row)
    return pd.DataFrame(rows)


# ── Modeling ─────────────────────────────────────────────────────────────

def loocv_eval(X: np.ndarray, y: np.ndarray, model_name: str) -> dict:
    loo = LeaveOneOut()
    preds = np.zeros_like(y, dtype=float)
    for train_idx, test_idx in loo.split(X):
        if model_name == "linear":
            m = LinearRegression()
        else:
            m = RandomForestRegressor(n_estimators=200, max_depth=3, random_state=42)
        m.fit(X[train_idx], y[train_idx])
        preds[test_idx] = m.predict(X[test_idx])
    return {
        "predictions": preds,
        "mae": mean_absolute_error(y, preds),
        "r2": r2_score(y, preds),
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    target   = build_target()
    features = build_features()
    df = features.merge(target, on="year", how="inner").sort_values("year").reset_index(drop=True)

    logger.info(f"Training data: {len(df)} annual observations ({df['year'].min()}-{df['year'].max()})")
    print(df.to_string(index=False))

    X = df[FEATURE_COLS].values
    y = df["employment_pop_ratio"].values

    # ── Leave-one-out cross-validation ──────────────────────────────────
    lin_cv = loocv_eval(X, y, "linear")
    rf_cv  = loocv_eval(X, y, "rf")
    print(f"\nLeave-one-out CV (n={len(df)}):")
    print(f"  Linear Regression — MAE: {lin_cv['mae']:.3f}, R2: {lin_cv['r2']:.3f}")
    print(f"  Random Forest     — MAE: {rf_cv['mae']:.3f}, R2: {rf_cv['r2']:.3f}")

    # ── Fit final models on all data ────────────────────────────────────
    lin_model = LinearRegression().fit(X, y)
    rf_model  = RandomForestRegressor(n_estimators=200, max_depth=3, random_state=42).fit(X, y)

    # OLS for proper prediction intervals (linear model)
    X_sm = sm.add_constant(X)
    ols = sm.OLS(y, X_sm).fit()

    # ── Forecast 2025-2027 ───────────────────────────────────────────────
    future_features = extrapolate_features(features)
    X_future = future_features[FEATURE_COLS].values
    X_future_sm = sm.add_constant(X_future, has_constant="add")

    ols_pred = ols.get_prediction(X_future_sm).summary_frame(alpha=0.10)  # 90% PI
    rf_tree_preds = np.array([t.predict(X_future) for t in rf_model.estimators_])
    rf_mean = rf_tree_preds.mean(axis=0)
    rf_lo   = np.percentile(rf_tree_preds, 5, axis=0)
    rf_hi   = np.percentile(rf_tree_preds, 95, axis=0)
    # NOTE: tree-based models cannot extrapolate beyond the feature range seen
    # in training — every forecast year's extrapolated features fall past the
    # training max, so all three years land in the same terminal leaf(s) and
    # produce near-identical predictions. This is expected RF behavior, not a
    # bug; it means the RF forecast is far less informative than the linear
    # one for out-of-range years. Reported as-is rather than disguised.

    forecast = pd.DataFrame({
        "year": FORECAST_YEARS,
        "linear_pred":    ols_pred["mean"].values,
        "linear_lo90":    ols_pred["obs_ci_lower"].values,
        "linear_hi90":    ols_pred["obs_ci_upper"].values,
        "rf_pred":        rf_mean,
        "rf_lo90":        rf_lo,
        "rf_hi90":        rf_hi,
    })
    forecast_path = ANALYSIS_DIR / "employment_forecast_2025_2027.csv"
    forecast.to_csv(forecast_path, index=False)
    logger.info(f"Saved: {forecast_path}")
    print("\nForecast 2025-2027:")
    print(forecast.to_string(index=False))

    # ── Save fitted values for actual-vs-predicted plot ─────────────────
    fitted = df[["year", "employment_pop_ratio"]].copy()
    fitted["linear_loocv_pred"] = lin_cv["predictions"]
    fitted["rf_loocv_pred"]     = rf_cv["predictions"]
    fitted_path = ANALYSIS_DIR / "employment_model_fit.csv"
    fitted.to_csv(fitted_path, index=False)
    logger.info(f"Saved: {fitted_path}")

    # ── Plot: actual vs predicted (history) + forecast ──────────────────
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(df["year"], df["employment_pop_ratio"], "o-", color="#1F4E79", label="Actual", linewidth=2)
    ax.plot(df["year"], lin_cv["predictions"], "s--", color="#2E75B6", label="Linear Reg. (LOOCV)", alpha=0.8)
    ax.plot(df["year"], rf_cv["predictions"], "^--", color="#C00000", label="Random Forest (LOOCV)", alpha=0.8)

    ax.plot(forecast["year"], forecast["linear_pred"], "s-", color="#2E75B6")
    ax.fill_between(forecast["year"], forecast["linear_lo90"], forecast["linear_hi90"],
                     color="#2E75B6", alpha=0.15, label="Linear 90% PI")
    ax.plot(forecast["year"], forecast["rf_pred"], "^-", color="#C00000")
    ax.fill_between(forecast["year"], forecast["rf_lo90"], forecast["rf_hi90"],
                     color="#C00000", alpha=0.15, label="RF 90% PI")

    ax.axvline(df["year"].max() + 0.5, color="gray", linestyle=":", linewidth=1)
    ax.set_xlabel("Year")
    ax.set_ylabel("Disability employment-population ratio (%)")
    ax.set_title("National Disability Employment Rate — Actual vs Predicted, with 2025-2027 Forecast")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(PLOTS_DIR / "employment_actual_vs_predicted.png", dpi=120)
    plt.close(fig)

    # ── Plot: feature importance (RF) + coefficients (Linear) ───────────
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    axes[0].barh(FEATURE_COLS, rf_model.feature_importances_, color="#C00000")
    axes[0].set_title("Random Forest Feature Importance")
    axes[0].invert_yaxis()

    axes[1].barh(FEATURE_COLS, lin_model.coef_, color="#2E75B6")
    axes[1].set_title("Linear Regression Coefficients")
    axes[1].axvline(0, color="black", linewidth=0.8)
    axes[1].invert_yaxis()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "employment_feature_importance.png", dpi=120)
    plt.close(fig)

    logger.info("Saved plots: employment_actual_vs_predicted.png, employment_feature_importance.png")


if __name__ == "__main__":
    main()
