# How to Run the Pipeline — Step-by-Step Tutorial

This guide is for anyone who needs to refresh the disability employment data in BigQuery. You do not need to be a programmer to follow these steps.

---

## What the pipeline does

Each time you run it, the pipeline:
1. Downloads the latest disability employment data from the U.S. Census and BLS
2. Cleans and standardizes the data
3. Uploads it to BigQuery (which feeds the Looker Studio dashboard)
4. Generates a validation report so you can confirm everything looks right

The whole process takes roughly **20–40 minutes** depending on your internet speed.

---

## Before your first run

These steps only need to be done once.

### Step 1 — Make sure you have the files

You should have received a folder called `ETL Pipeline Building` (or cloned it from GitHub). Inside it you will see a file called `run_pipeline.bat`.

If you need to download it fresh:
```
git clone https://github.com/dat-c-le/enabled-talent-etl-pipeline.git
```

### Step 2 — Check that Python is installed

Open **Command Prompt** (search for `cmd` in the Start menu) and type:
```
"C:\Program Files\Python313\python.exe" --version
```
You should see something like `Python 3.13.x`. If you get an error, contact your technical maintainer — Python needs to be installed first.

### Step 3 — Install the required packages (first time only)

In Command Prompt, navigate to the pipeline folder and run:
```
cd "C:\Users\YourName\Desktop\ETL Pipeline Building"
"C:\Program Files\Python313\python.exe" -m pip install -r requirements.txt
```

### Step 4 — Set up your credentials file

In the pipeline folder there is a file called `.env.example`. Make a copy of it named `.env` (no `.example`), then open it in Notepad and fill in your API keys:

```
CENSUS_API_KEY=your_census_key_here
BLS_API_KEY=your_bls_key_here
GCP_PROJECT_ID=msba-capstone-498915
BQ_DATASET=disability_employment
GCP_CREDENTIALS_PATH=service_account.json
```

Also make sure your Google Cloud service account file (`service_account.json`) is placed in the same folder.

---

## Running the pipeline

### The easy way — double-click the launcher

1. Open the `ETL Pipeline Building` folder
2. Find the file called **`run_pipeline.bat`**
3. Double-click it

A black terminal window will open and you will see the pipeline running. It will print progress messages as it goes through each step.

**Do not close the window while it is running.**

When it finishes you will see:

```
Done. Latest validation report:
  output\reports\validation_summary.csv
  output\reports\validation_column_detail.csv

Press any key to continue . . .
```

Press any key to close the window.

---

## Checking the results

### Validation report

After each run, open the file:
```
output\reports\validation_summary.csv
```

Open it in Excel. Each row is one data table. Check the columns:

| Column | What to look for |
|---|---|
| `row_count` | Should be in the tens of thousands for ACS tables, ~34,000 for CPS |
| `duplicate_rows` | Should be 0 |
| `missing_fips` | Should be 0 for ACS tables |
| `year_min` / `year_max` | ACS should show 2010–2024, CPS should show 2008–2024 |

If anything looks wrong, do not proceed to load the data — contact your technical maintainer.

### BigQuery / Looker Studio

After a successful run the BigQuery tables are automatically updated. Open Looker Studio and **refresh** your dashboard — the charts will reflect the latest data.

---

## What to do if something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| Window closes immediately with red text | A Python error occurred | Screenshot the error and send to your maintainer |
| `Could not find Python` message | Python is not at the expected path | Contact your maintainer to update the Python path in `run_pipeline.bat` |
| Row counts look very low in the validation report | The data may not have downloaded fully | Run the pipeline again; if it persists, contact your maintainer |
| Dashboard looks the same after the run | Browser cache | Hard-refresh Looker Studio (Ctrl + Shift + R) |

---

## How often to run it

The Census ACS data updates **once per year** (typically released in September for the prior year). The BLS CPS data updates **monthly**.

Recommended refresh schedule:
- **Monthly** — run the pipeline to capture the latest BLS CPS series
- **Every September** — run after Census releases the new ACS 1-year estimates

---

## Quick reference

| Task | How |
|---|---|
| Run the full pipeline | Double-click `run_pipeline.bat` |
| Check the results | Open `output\reports\validation_summary.csv` in Excel |
| View updated dashboard | Refresh Looker Studio after the pipeline finishes |
| Find the raw data | `output\cleaned\` folder |
| Find error logs | Printed in the terminal window during the run |
