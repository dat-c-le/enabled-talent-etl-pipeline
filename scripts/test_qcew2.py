import sys; sys.path.insert(0,'.')
from extract.bls import fetch_qcew_state_year
import config

target = set(config.QCEW_INDUSTRY_CODES)

df = fetch_qcew_state_year(2022, "01")
print("Total rows:", len(df))
print()

# own_code=0 rows only
own0 = df[df["own_code"].str.strip() == "0"]
print(f"own_code=0 rows: {len(own0)}")
print()

# Check which of our target industry codes are in the CSV
avail_codes = set(own0["industry_code"].str.strip().tolist())
matched = target & avail_codes
missing = target - avail_codes
print(f"Target codes matched: {sorted(matched)}")
print(f"Target codes missing from CSV: {sorted(missing)}")
print()

# Show all industry codes with own_code=0 that look like our targets
print("All own_code=0 industry codes:")
print(sorted(avail_codes)[:50])
