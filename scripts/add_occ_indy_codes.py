"""Add occupation_code, indy_code, class_code from Excel to bls_series.json."""
import json
import pandas as pd

df = pd.read_excel("Disability_series_codes.xlsx", sheet_name="All series")

# Build lookup: series_id -> {occupation_code, indy_code, class_code}
extra = {}
for _, row in df.iterrows():
    extra[str(row["series_id"])] = {
        "occupation_code": int(row["occupation_code"]),
        "indy_code":       int(row["indy_code"]),
        "class_code":      int(row["class_code"]),
    }

with open("bls_series.json") as f:
    data = json.load(f)

updated = 0
for sid, meta in data["series"].items():
    if sid in extra:
        meta.update(extra[sid])
        updated += 1
    elif "occupation_code" not in meta:
        meta["occupation_code"] = 0
        meta["indy_code"] = 0
        meta["class_code"] = 0

with open("bls_series.json", "w") as f:
    json.dump(data, f, indent=2)

print(f"Updated {updated} series with occupation/industry/class codes")
print(f"Remaining series (no Excel entry, defaulted to 0): {len(data['series']) - updated}")
