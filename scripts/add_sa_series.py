"""Add seasonally adjusted (LNS1) counterparts for all monthly LNU0 CPS series."""
import json

with open("bls_series.json") as f:
    data = json.load(f)

# Mark existing series with seasonal indicator
for sid, meta in data["series"].items():
    if "seasonal" not in meta:
        meta["seasonal"] = "S" if sid.startswith("LNS") else "U"

# Generate LNS1 (SA) counterparts for all monthly LNU0 series
monthly_nsa = {
    sid: meta for sid, meta in data["series"].items()
    if sid.startswith("LNU0") and meta.get("periodicity") == "M"
}

sa_added = {}
for sid, meta in monthly_nsa.items():
    lns_sid = "LNS1" + sid[4:]  # LNU0XXXXXXX -> LNS1XXXXXXX
    if lns_sid not in data["series"]:
        sa_meta = dict(meta)
        sa_meta["title"] = meta["title"].replace("(unadj)", "(sadj)")
        sa_meta["seasonal"] = "S"
        sa_added[lns_sid] = sa_meta

data["series"].update(sa_added)

with open("bls_series.json", "w") as f:
    json.dump(data, f, indent=2)

print("Total series now:", len(data["series"]))
print("SA monthly series added:", len(sa_added))
print()
print("Sample LNS1 series generated:")
for sid in list(sa_added.keys())[:8]:
    print(" ", sid, ":", sa_added[sid]["title"])
