"""Load all cleaned CSVs to BigQuery (non-interactive)."""
import sys, logging
sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from pathlib import Path
from load.bigquery_loader import get_bq_client, create_dataset_if_not_exists, _group_csvs_by_table, load_table
import config

client = get_bq_client()
create_dataset_if_not_exists(client)

groups = _group_csvs_by_table(config.CLEANED_DIR)
print(f"\nTables to load: {sorted(groups.keys())}\n")

for tbl, paths in sorted(groups.items()):
    print(f"Loading {tbl} ({len(paths)} file(s))...")
    try:
        load_table(client, tbl, paths)
        print(f"  OK")
    except Exception as exc:
        print(f"  ERROR: {exc}")
        logging.exception(f"Load failed: {tbl}")

print("\nDone.")
