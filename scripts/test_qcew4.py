import sys; sys.path.insert(0,'.')
from extract.bls import fetch_qcew_state_year

df = fetch_qcew_state_year(2022, "01")

# agglvl_code 54 = 2-digit industry by ownership at state level
# Check what's in each agglvl_code
print("=== agglvl 54 (2-digit NAICS, state level) ===")
lvl54 = df[df["agglvl_code"].str.strip() == "54"]
print(f"Rows: {len(lvl54)}")
print(lvl54[["own_code","industry_code","agglvl_code","annual_avg_emplvl"]].sort_values("industry_code").to_string())

print()
print("=== agglvl 55 ===")
lvl55 = df[df["agglvl_code"].str.strip() == "55"]
print(f"Rows: {len(lvl55)}")
print(lvl55[["own_code","industry_code","agglvl_code","annual_avg_emplvl"]].head(10).to_string())

print()
print("=== agglvl 52 ===")
lvl52 = df[df["agglvl_code"].str.strip() == "52"]
print(f"Rows: {len(lvl52)}")
print(lvl52[["own_code","industry_code","agglvl_code","annual_avg_emplvl"]].to_string())
