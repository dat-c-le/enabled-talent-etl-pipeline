"""
BLS data extractor — CPS disability series (national level only).

Series IDs are read from bls_series.json (user-configurable).
Requires a BLS API v2 key for >25 requests/day.
Output: output/raw/bls/bls_cps_disability_raw.csv
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests
from tqdm import tqdm

import config

logger = logging.getLogger(__name__)

BLS_API_V2 = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

_BLS_BATCH     = 50    # BLS v2 max series per request
_BLS_YEAR_SPAN = 20    # BLS v2 max year range per request


# ── QCEW ─────────────────────────────────────────────────────────────────────

# ── BLS CPS disability series ─────────────────────────────────────────────────

def _load_bls_series_config() -> Dict[str, str]:
    """
    Read series IDs from bls_series.json.
    Returns {series_id: title}.
    Supports both the new flat format {"series": {id: {title, ...}}}
    and the legacy nested format {"section": {"series": {id: title}}}.
    """
    path = config.BLS_SERIES_FILE
    if not path.exists():
        logger.warning(f"{path} not found — skipping CPS extraction.")
        return {}
    try:
        cfg = json.loads(path.read_text())
        # New flat format: top-level "series" key maps id → metadata dict.
        if "series" in cfg and isinstance(cfg["series"], dict):
            first = next(iter(cfg["series"].values()), None)
            if isinstance(first, dict):
                return {sid: meta["title"] for sid, meta in cfg["series"].items()}
            return cfg["series"]
        # Legacy nested format: sections with "series" sub-dicts of id → title strings.
        combined: Dict[str, str] = {}
        for section_key, section in cfg.items():
            if section_key.startswith("_"):
                continue
            if isinstance(section, dict) and "series" in section:
                combined.update(section["series"])
        return combined
    except Exception as exc:
        logger.error(f"Could not parse {path}: {exc}")
        return {}


def _bls_year_batches(years: List[int]) -> List[tuple]:
    """Split a list of years into (start, end) pairs that fit BLS API limits."""
    years_sorted = sorted(years)
    batches = []
    i = 0
    while i < len(years_sorted):
        start = years_sorted[i]
        end   = min(start + _BLS_YEAR_SPAN - 1, years_sorted[-1])
        batches.append((str(start), str(end)))
        i += sum(1 for y in years_sorted if start <= y <= end)
    return batches


def fetch_bls_series(
    series_ids: List[str],
    start_year: str,
    end_year: str,
) -> Optional[Dict]:
    """
    POST to BLS API v2 for a batch of series over a year range.
    Returns the parsed JSON response or None on failure.
    """
    payload: Dict = {
        "seriesid":  series_ids,
        "startyear": start_year,
        "endyear":   end_year,
    }
    if config.BLS_API_KEY:
        payload["registrationkey"] = config.BLS_API_KEY

    try:
        resp = requests.post(BLS_API_V2, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error(f"BLS API error ({start_year}–{end_year}): {exc}")
        return None


def run_cps_extraction(raw_dir: Path = config.BLS_RAW_DIR) -> None:
    """
    Fetch national CPS disability series from BLS API v2.
    Saves to output/raw/bls/bls_cps_disability_raw.csv.

    Output columns: series_id, series_description, year, period, value
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / "bls_cps_disability_raw.csv"

    if out_path.exists():
        logger.info(f"Skip (cached): {out_path.name}")
        return

    series_map = _load_bls_series_config()
    if not series_map:
        logger.warning("No BLS series configured. Edit bls_series.json to add series IDs.")
        return

    all_series_ids = list(series_map.keys())
    rows: List[Dict] = []
    year_batches = _bls_year_batches(config.CPS_YEARS)

    series_batches = [
        all_series_ids[i : i + _BLS_BATCH]
        for i in range(0, len(all_series_ids), _BLS_BATCH)
    ]

    for s_batch in tqdm(series_batches, desc="BLS CPS series batches"):
        for start_yr, end_yr in year_batches:
            result = fetch_bls_series(s_batch, start_yr, end_yr)
            if not result or result.get("status") != "REQUEST_SUCCEEDED":
                logger.warning(f"BLS request did not succeed: {result.get('message', '')}")
                continue

            for series_obj in result.get("Results", {}).get("series", []):
                sid = series_obj.get("seriesID", "")
                description = series_map.get(sid, "")
                for obs in series_obj.get("data", []):
                    # Keep annual averages (M13/Q05/A01) when present; otherwise
                    # keep all monthly observations (M01-M12) for series that
                    # do not publish a pre-computed annual average.
                    period = obs.get("period", "")
                    rows.append({
                        "series_id":          sid,
                        "series_description": description,
                        "year":               obs.get("year", ""),
                        "period":             period,
                        "value":              obs.get("value", ""),
                        "footnotes":          "; ".join(
                            f.get("text", "") for f in obs.get("footnotes", []) if f.get("text")
                        ),
                    })
            time.sleep(_REQUEST_DELAY)

    if not rows:
        logger.warning("No CPS disability data retrieved. Verify series IDs in bls_series.json.")
        return

    pd.DataFrame(rows).to_csv(out_path, index=False)
    logger.info(f"CPS disability saved: {out_path}  ({len(rows)} records)")
