@echo off
REM Launcher for the disability employment ETL pipeline.
REM Always runs the full pipeline end to end (extract -> transform -> combine
REM -> validate -> load), so every run pulls fresh data for all sources and
REM regenerates the validation report in output/reports/.
REM
REM Uses the global Python install directly, since a local .venv on this
REM machine shadows "python"/"py" but is missing required packages.

setlocal
cd /d "%~dp0"

set PYTHON_EXE=C:\Program Files\Python313\python.exe

if not exist "%PYTHON_EXE%" (
    echo Could not find Python at "%PYTHON_EXE%".
    echo Edit PYTHON_EXE in run_pipeline.bat to point at your Python install.
    pause
    exit /b 1
)

"%PYTHON_EXE%" main.py --step all --source all
if errorlevel 1 (
    echo.
    echo Pipeline failed - see the error above. Validation report was not regenerated.
    pause
    exit /b 1
)

echo.
echo Done. Latest validation report:
echo   output\reports\validation_summary.csv
echo   output\reports\validation_column_detail.csv
pause
