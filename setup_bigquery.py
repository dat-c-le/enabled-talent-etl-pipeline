"""
One-time Google Cloud / BigQuery setup.

Run this script ONCE before using the pipeline for the first time.
It will guide you through authentication and create the BigQuery dataset.

Usage:
    python setup_bigquery.py
"""

import os
import sys
from pathlib import Path

# ── Step-by-step GCP setup instructions ──────────────────────────────────────

SETUP_GUIDE = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                  GOOGLE CLOUD SETUP GUIDE                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Your GCP Project ID: msba-capstone-498915

────────────────────────────────────────────────────────────────────────────────
STEP 1 — Enable the BigQuery API
────────────────────────────────────────────────────────────────────────────────
  1. Go to: https://console.cloud.google.com/apis/library/bigquery.googleapis.com
  2. Make sure project "msba-capstone-498915" is selected in the top dropdown.
  3. Click "Enable" if it is not already enabled.

────────────────────────────────────────────────────────────────────────────────
STEP 2 — Create a Service Account and download a key
────────────────────────────────────────────────────────────────────────────────
  1. Go to: https://console.cloud.google.com/iam-admin/serviceaccounts
  2. Click "+ CREATE SERVICE ACCOUNT".
  3. Name: etl-pipeline   (or any name you like)
  4. Click "Create and continue".
  5. Grant role: "BigQuery Admin"  (or "BigQuery Data Editor" + "BigQuery Job User").
  6. Click "Done".
  7. Click on the new service account → "Keys" tab → "Add Key" → "Create new key".
  8. Choose JSON format, click "Create".
  9. A file is downloaded — rename it to  service_account.json
     and place it in this folder:
       {folder}

────────────────────────────────────────────────────────────────────────────────
STEP 3 — Copy .env.example → .env and fill in your keys
────────────────────────────────────────────────────────────────────────────────
  1. Duplicate .env.example and name the copy  .env
  2. Fill in CENSUS_API_KEY and BLS_API_KEY (request links are in .env.example).
  3. GCP_PROJECT_ID is already set to msba-capstone-498915.
  4. GCP_CREDENTIALS_PATH defaults to service_account.json (same folder).

────────────────────────────────────────────────────────────────────────────────
STEP 4 — Connect Looker Studio (after data is loaded)
────────────────────────────────────────────────────────────────────────────────
  1. Go to: https://lookerstudio.google.com
  2. Click "Create" → "Data source".
  3. Choose "BigQuery".
  4. Select:
       Project : msba-capstone-498915
       Dataset : disability_employment
       Table   : (choose any table, e.g. acs_s1810)
  5. Click "Connect".
  6. You can now create charts and blend multiple tables.
"""


def print_guide() -> None:
    folder = Path(__file__).parent.resolve()
    print(SETUP_GUIDE.format(folder=folder))


def create_dataset() -> None:
    """Create the BigQuery dataset using credentials from .env."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    project_id       = os.getenv("GCP_PROJECT_ID", "msba-capstone-498915")
    dataset_id       = os.getenv("BQ_DATASET", "disability_employment")
    credentials_path = os.getenv("GCP_CREDENTIALS_PATH", "service_account.json")

    print(f"\nConnecting to BigQuery project: {project_id}")
    print(f"Dataset to create: {dataset_id}")

    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account

        if credentials_path and os.path.isfile(credentials_path):
            creds  = service_account.Credentials.from_service_account_file(
                credentials_path,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            client = bigquery.Client(project=project_id, credentials=creds)
            print(f"  Auth: service account from {credentials_path}")
        else:
            client = bigquery.Client(project=project_id)
            print("  Auth: Application Default Credentials")

        dataset_ref = bigquery.Dataset(f"{project_id}.{dataset_id}")
        dataset_ref.location = "US"
        client.create_dataset(dataset_ref, exists_ok=True)
        print(f"\n  Dataset '{dataset_id}' is ready in project '{project_id}'.")
        print("  You can now run: python main.py\n")

    except Exception as exc:
        print(f"\nERROR: {exc}")
        print(
            "\nMake sure you have:\n"
            "  1. Placed service_account.json in the project folder, OR\n"
            "  2. Run 'gcloud auth application-default login' in your terminal.\n"
            "  3. Enabled the BigQuery API (Step 1 in the guide above).\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    print_guide()
    answer = input("Have you completed Steps 1–3 above? Ready to create the dataset? [yes/no]: ").strip().lower()
    if answer in ("yes", "y"):
        create_dataset()
    else:
        print("\nComplete the setup steps first, then re-run this script.")
