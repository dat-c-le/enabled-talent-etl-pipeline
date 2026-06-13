"""
Clean up bls_series.json:
1. Remove the 58 LNS disability series (confirmed non-existent in BLS API)
2. Add LNS1 SA counterparts for the 24 general population totals (these DO exist)
3. Fix periodicity of general population NSA series to 'M' (they have monthly data)
"""
import json

with open("bls_series.json") as f:
    data = json.load(f)

before = len(data["series"])

# 1. Remove non-existent LNS disability series
#    These start with LNS1 AND have disa_code in {1, 2} (disability-specific)
removed = [
    sid for sid, meta in data["series"].items()
    if sid.startswith("LNS1") and meta.get("disa_code", 0) in (1, 2)
]
for sid in removed:
    del data["series"][sid]
print(f"Removed {len(removed)} non-existent LNS disability series")

# 2. Fix periodicity for the 24 general population NSA series to 'M'
#    (they return monthly data from BLS API even though I originally set A)
fixed = 0
for sid, meta in data["series"].items():
    if sid.startswith("LNU0") and meta.get("disa_code", -1) == 0 and meta.get("periodicity") == "A":
        meta["periodicity"] = "M"
        fixed += 1
print(f"Fixed periodicity to 'M' for {fixed} general population NSA series")

# 3. Add LNS1 SA counterparts for all general population totals (disa_code=0)
gen_nsa = {
    sid: meta for sid, meta in data["series"].items()
    if sid.startswith("LNU0") and meta.get("disa_code", -1) == 0
}

sa_added = {}
for sid, meta in gen_nsa.items():
    lns_sid = "LNS1" + sid[4:]
    if lns_sid not in data["series"]:
        sa_meta = dict(meta)
        sa_meta["title"] = meta["title"].replace("(unadj)", "(sadj)")
        sa_meta["seasonal"] = "S"
        sa_meta["periodicity"] = "M"
        sa_added[lns_sid] = sa_meta

data["series"].update(sa_added)
print(f"Added {len(sa_added)} SA general population series (LNS1 counterparts)")

with open("bls_series.json", "w") as f:
    json.dump(data, f, indent=2)

after = len(data["series"])
print(f"\nTotal series: {before} → {after}")
print("\nSample SA general population series added:")
for sid in list(sa_added.keys())[:6]:
    print(f"  {sid}: {sa_added[sid]['title']}")
