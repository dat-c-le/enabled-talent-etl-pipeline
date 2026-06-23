"""Backfill missing B18121 state-level extraction for all years except 2024."""
import sys, logging
sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import config
from extract.acs import extract_table_year

for year in config.ACS_YEARS:
    ok = extract_table_year(year, "B18121", "detailed", config.ACS_RAW_DIR)
    print(f"{year}: {'OK' if ok else 'FAILED'}")
