import sys; sys.path.insert(0,'.')
from extract.bls import fetch_qcew_state_year

df = fetch_qcew_state_year(2022, "01")

# Check what own_codes are available for industry "11" (Agriculture)
for ind in ["11", "22", "23", "51", "52"]:
    rows = df[df["industry_code"].str.strip() == ind]
    if rows.empty:
        print(f"Industry {ind}: NOT FOUND")
    else:
        print(f"Industry {ind}: own_codes available = {sorted(rows['own_code'].str.strip().tolist())}")

print()
# Check agglvl_code for industry 11
ind11 = df[df["industry_code"].str.strip() == "11"]
if not ind11.empty:
    print("Industry 11 rows:")
    print(ind11[["own_code","agglvl_code","size_code","annual_avg_emplvl","disclosure_code"]].to_string())

print()
# What agglvl_codes exist in the file?
print("agglvl_code distribution:")
print(df["agglvl_code"].value_counts().head(20).to_string())
