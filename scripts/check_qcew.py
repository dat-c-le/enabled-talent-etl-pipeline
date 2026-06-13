import pandas as pd

df = pd.read_csv("output/cleaned/bls_qcew_cleaned.csv")
print(f"Shape: {df.shape}")
print(f"Years present: {sorted(df['year'].unique())}")
print(f"state_fips sample values: {df['state_fips'].head(5).tolist()}")
print()

# Check CA
ca = df[df["state_name"]=="California"]
print(f"California rows: {len(ca)}")
print("CA 2022 sample:")
print(ca[ca["year"]==2022][["industry_code","industry_title","avg_monthly_employment","total_annual_wages_usd"]].head(5).to_string(index=False))

print()
print(f"Suppressed rows: {(df['disclosure_code']=='N').sum()}")
print()
# Check raw file years
raw = pd.read_csv("output/raw/bls/bls_qcew_raw.csv")
print(f"Raw years: {sorted(raw['year'].unique())}")
print(f"Raw rows: {len(raw)}")
