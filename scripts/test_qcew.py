import sys; sys.path.insert(0,'.')
from extract.bls import fetch_qcew_state_year
import config

target = set(config.QCEW_INDUSTRY_CODES)

for fips, year in [("01", 2022), ("06", 2019)]:
    df = fetch_qcew_state_year(year, fips)
    if df is None:
        print(f"{fips}/{year}: None returned")
        continue
    mask = (df["own_code"].str.strip() == "0") & (df["industry_code"].str.strip().isin(target))
    filtered = df[mask]
    print(f"{fips}/{year}: {len(filtered)} rows matched")
    print("  Industry codes:", filtered["industry_code"].str.strip().tolist())
    row = filtered.iloc[0]
    print("  Sample row:", {
        "annual_avg_emplvl":  row["annual_avg_emplvl"],
        "annual_avg_estabs":  row["annual_avg_estabs"],
        "total_annual_wages": row["total_annual_wages"],
        "avg_annual_pay":     row["avg_annual_pay"],
        "disclosure_code":    row["disclosure_code"],
    })
    print()
