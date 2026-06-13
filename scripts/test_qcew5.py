"""Quick test: run QCEW extraction for 3 states, 2 years only."""
import sys; sys.path.insert(0,'.')
import config, time, logging
from pathlib import Path
from typing import List, Dict
import pandas as pd
from tqdm import tqdm
from extract.bls import fetch_qcew_state_year, _STATE_NAMES, _INDUSTRY_TITLES, _CODE_TO_CSV

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

csv_codes = {_CODE_TO_CSV.get(c, c) for c in config.QCEW_INDUSTRY_CODES}
numeric_cols = ["annual_avg_emplvl", "annual_avg_estabs", "total_annual_wages", "avg_annual_pay"]

test_states = ["01", "06", "48"]  # AL, CA, TX
test_years  = [2019, 2022]

all_frames: List[pd.DataFrame] = []
for year in test_years:
    for fips in test_states:
        df = fetch_qcew_state_year(year, fips)
        if df is None or df.empty:
            print(f"  {fips}/{year}: no data")
            continue

        df["own_code"]      = df["own_code"].str.strip()
        df["industry_code"] = df["industry_code"].str.strip()
        df["agglvl_code"]   = df["agglvl_code"].str.strip()

        records = []
        if "10" in csv_codes:
            row = df[(df["industry_code"] == "10") & (df["own_code"] == "0")]
            if not row.empty:
                r = row.iloc[0]
                records.append({"industry_code": "10", "annual_avg_emplvl": r["annual_avg_emplvl"],
                                 "industry_title": _INDUSTRY_TITLES["10"]})

        sector_targets = csv_codes - {"10"}
        sectors = df[(df["agglvl_code"] == "54") & (df["industry_code"].isin(sector_targets))].copy()
        for col in numeric_cols:
            sectors[col] = pd.to_numeric(sectors[col], errors="coerce")
        for ind_code, grp in sectors.groupby("industry_code"):
            records.append({"industry_code": ind_code, "annual_avg_emplvl": grp["annual_avg_emplvl"].sum(),
                             "industry_title": _INDUSTRY_TITLES.get(ind_code, "")})

        print(f"  {fips}/{year}: {len(records)} industry rows")
        if records:
            frame = pd.DataFrame(records)
            frame["state_fips"] = fips
            frame["year"] = year
            all_frames.append(frame)
        time.sleep(0.3)

if all_frames:
    result = pd.concat(all_frames, ignore_index=True)
    print(f"\nTotal rows: {len(result)}")
    print("Industry codes present:", sorted(result["industry_code"].unique()))
    print("\nSample (CA 2022):")
    sample = result[(result["state_fips"]=="06") & (result["year"]==2022)]
    print(sample[["industry_code","industry_title","annual_avg_emplvl"]].sort_values("industry_code").to_string(index=False))
