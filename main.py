"""
ETL Pipeline — Disability Employment Data
=========================================
Orchestrates: Extract → Transform → Validate → Load

Usage
-----
Run the full pipeline:
    python main.py

Run individual steps:
    python main.py --step extract
    python main.py --step transform
    python main.py --step validate
    python main.py --step load

Run only ACS or BLS extraction:
    python main.py --step extract --source acs
    python main.py --step extract --source bls

First time? Run setup first:
    python setup_bigquery.py
"""

import argparse
import logging
import sys
from pathlib import Path

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def _check_env() -> None:
    """Warn if API keys or credentials are missing before running."""
    import config
    if not config.CENSUS_API_KEY:
        logger.warning(
            "CENSUS_API_KEY is not set. The Census API will work without a key "
            "but rate limits are much stricter (500 req/day vs unlimited with key). "
            "Request one at: https://api.census.gov/data/key_signup.html"
        )
    if not config.BLS_API_KEY:
        logger.warning(
            "BLS_API_KEY is not set. BLS API allows only 25 requests/day without a key. "
            "Request one at: https://data.bls.gov/registrationEngine/"
        )
    creds = Path(config.GCP_CREDENTIALS_PATH)
    if not creds.exists():
        logger.info(
            f"GCP service account file not found at '{creds}'. "
            "Will attempt Application Default Credentials for BigQuery load. "
            "Run setup_bigquery.py if you need setup guidance."
        )


# ── Step functions ─────────────────────────────────────────────────────────────

def step_extract(source: str) -> None:
    import config
    if source in ("acs", "all"):
        logger.info("── EXTRACT: ACS ──────────────────────────────────────")
        logger.info(
            f"Tables : {list(config.ACS_TABLES.keys())}\n"
            f"Years  : {config.ACS_YEARS[0]}–{config.ACS_YEARS[-1]} (skipping 2020)\n"
            f"Output : {config.ACS_RAW_DIR}"
        )
        from extract.acs import run_acs_extraction
        run_acs_extraction(config.ACS_RAW_DIR)

    if source in ("bls", "all"):
        logger.info("── EXTRACT: BLS QCEW ─────────────────────────────────")
        logger.info(
            f"Industries : {len(config.QCEW_INDUSTRY_CODES)} NAICS codes\n"
            f"States     : {len(config.STATE_FIPS)} (50 states + DC + PR)\n"
            f"Output     : {config.BLS_RAW_DIR}"
        )
        from extract.bls import run_qcew_extraction, run_cps_extraction
        run_qcew_extraction(config.BLS_RAW_DIR)

        logger.info("── EXTRACT: BLS CPS disability series ────────────────")
        run_cps_extraction(config.BLS_RAW_DIR)


def step_transform(source: str = "all") -> None:
    import config
    if source in ("acs", "all"):
        logger.info("── TRANSFORM: ACS ────────────────────────────────────")
        from transform.acs_transform import run_acs_transform
        run_acs_transform(config.ACS_RAW_DIR, config.CLEANED_DIR)

    if source in ("bls", "all"):
        logger.info("── TRANSFORM: BLS ────────────────────────────────────")
        from transform.bls_transform import run_bls_transform
        run_bls_transform(config.BLS_RAW_DIR, config.CLEANED_DIR)


def step_validate() -> None:
    import config
    logger.info("── VALIDATE ──────────────────────────────────────────")
    from validate.report import run_validation
    run_validation(config.CLEANED_DIR, config.REPORTS_DIR)


def step_load() -> None:
    import config
    logger.info("── LOAD → BigQuery ───────────────────────────────────")
    from load.bigquery_loader import run_load
    run_load(config.CLEANED_DIR, config.GCP_PROJECT_ID, config.BQ_DATASET)


# ── CLI ────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Disability Employment ETL Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--step",
        choices=["extract", "transform", "validate", "load", "all"],
        default="all",
        help="Pipeline step to run (default: all)",
    )
    parser.add_argument(
        "--source",
        choices=["acs", "bls", "all"],
        default="all",
        help="Data source for the extract step (default: all)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    _check_env()

    print("\n" + "=" * 60)
    print("  Disability Employment ETL Pipeline")
    print("=" * 60)

    if args.step in ("extract", "all"):
        step_extract(args.source)

    if args.step in ("transform", "all"):
        step_transform(args.source)

    if args.step in ("validate", "all"):
        step_validate()

    if args.step in ("load", "all"):
        print(
            "\nThe validation report is in output/reports/.\n"
            "Review it before loading to BigQuery.\n"
        )
        step_load()

    print("\nPipeline finished.\n")


if __name__ == "__main__":
    main()
