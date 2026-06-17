import sys; sys.path.insert(0, ".")
from load.bigquery_loader import get_bq_client
import config

client = get_bq_client()
dataset_ref = f"{client.project}.{config.BQ_DATASET}"
tables = list(client.list_tables(dataset_ref))

print(f"BigQuery dataset: {dataset_ref}")
print(f"Tables ({len(tables)}):")
for t in sorted(tables, key=lambda x: x.table_id):
    tbl = client.get_table(t.reference)
    modified = tbl.modified.strftime("%Y-%m-%d %H:%M UTC")
    print(f"  {t.table_id:<35} {tbl.num_rows:>8,} rows   last loaded: {modified}")
