"""Test whether LNS seasonally adjusted disability series exist in BLS API."""
import requests, config

test_ids = [
    "LNS12074597",  # SA Employed, with disability 16+
    "LNS14074597",  # SA Unemployment rate, with disability 16+
    "LNS11074597",  # SA Civilian labor force, with disability 16+
    "LNS13000000",  # SA Unemployment rate, all persons (should exist)
    "LNS12000000",  # SA Employed, all persons (should exist)
    "LNS11000000",  # SA Civilian labor force, all persons
]
payload = {"seriesid": test_ids, "startyear": "2022", "endyear": "2023"}
if config.BLS_API_KEY:
    payload["registrationkey"] = config.BLS_API_KEY

resp = requests.post("https://api.bls.gov/publicAPI/v2/timeseries/data/", json=payload, timeout=30)
result = resp.json()
print("Status:", result.get("status"))
for s in result.get("Results", {}).get("series", []):
    n = len(s.get("data", []))
    print(" ", s["seriesID"], ":", n, "observations")

if result.get("message"):
    for m in result["message"]:
        print("API message:", m)
