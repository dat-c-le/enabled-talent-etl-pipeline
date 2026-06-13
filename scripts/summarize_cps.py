import pandas as pd

df = pd.read_csv('output/cleaned/bls_cps_disability_cleaned.csv', low_memory=False)

print('=== SHAPE ===')
print(f'Rows: {len(df):,}, Columns: {len(df.columns)}')

print('\n=== DTYPES ===')
print(df.dtypes.to_string())

print('\n=== YEAR RANGE ===')
yr = df['year']
print(f'Min: {yr.min()}, Max: {yr.max()}')
print('Period distribution (top 15):')
print(df['period'].value_counts().sort_index().head(15).to_string())

print('\n=== PERIODICITY ===')
print(df['periodicity'].value_counts().to_string())

print('\n=== SEASONAL ADJUSTMENT ===')
print(df['seasonal_adjustment'].value_counts().to_string())

print('\n=== DISABILITY STATUS ===')
print(df['disability_status'].value_counts().to_string())

print('\n=== SEX ===')
print(df['sex'].value_counts().to_string())

print('\n=== AGE GROUP ===')
print(df['age_group'].value_counts().to_string())

print('\n=== RACE ETHNICITY ===')
print(df['race_ethnicity'].value_counts().to_string())

print('\n=== LABOR FORCE STATUS ===')
print(df['labor_force_status'].value_counts().to_string())

print('\n=== OCCUPATION (non-empty) ===')
occ = df[df['occupation'].notna() & (df['occupation'] != '')]
print(occ['occupation'].value_counts().to_string())

print('\n=== INDUSTRY (non-empty, top 20) ===')
ind = df[df['industry'].notna() & (df['industry'] != '')]
print(ind['industry'].value_counts().head(20).to_string())

print('\n=== CLASS OF WORKER (non-empty) ===')
cow = df[df['class_of_worker'].notna() & (df['class_of_worker'] != '')]
print(cow['class_of_worker'].value_counts().to_string())

print('\n=== NILF SUBCATEGORY (non-empty) ===')
nilf = df[df['nilf_subcategory'].notna() & (df['nilf_subcategory'] != '')]
print(nilf['nilf_subcategory'].value_counts().to_string())

print('\n=== VALUE STATS ===')
print(df['value'].describe().to_string())

print('\n=== NULLS PER COLUMN ===')
print(df.isnull().sum().to_string())

print('\n=== UNIQUE SERIES COUNT ===')
print(f'Total unique series: {df["series_id"].nunique()}')
print(f'Monthly series (M periodicity): {df[df["periodicity"]=="M"]["series_id"].nunique()}')
print(f'Annual series (A periodicity): {df[df["periodicity"]=="A"]["series_id"].nunique()}')
