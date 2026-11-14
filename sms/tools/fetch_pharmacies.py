"""Build app/data/pharmacies.json from the NPI Registry (CMS, public, no key).

    uv run python tools/fetch_pharmacies.py            # pharmacies within 50 miles of Athens
    uv run python tools/fetch_pharmacies.py 70         # a wider radius

Keeps active community/retail pharmacies (not technicians, pharmacists, hospital inpatient,
long-term-care, mail-order or student-only pharmacies). Location = the ZIP code's centre from
places.json, so distances are approximate. The registry has no opening hours: people are told to
call first.
"""
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import services  # noqa: E402

API = "https://npiregistry.cms.hhs.gov/api/"
CENTER = {"lat": 39.329, "lon": -82.101}  # Athens
RADIUS = float(sys.argv[1]) if len(sys.argv) > 1 else 50
KEEP = re.compile(r"^Pharmacy(, Community/Retail Pharmacy)?$")
SKIP_NAME = re.compile(r"STUDENT|INSTITUTIONAL|LONG TERM|MAIL ORDER|SPECIALTY|INFUSION|NUCLEAR|COMPOUNDING|"
                       r"RITE AID", re.I)  # Rite Aid closed all its stores in 2025; the registry still lists some


def short_name(raw: str) -> str:
    """'CVS PHARMACY #06184' -> 'CVS Pharmacy', 'FRUTH PHARMACY 09' -> 'Fruth Pharmacy'."""
    name = re.sub(r"\s*(#\s*[\d-]+|\b[\d-]{2,}|\bSTORE\b.*)$", "", raw.strip(), flags=re.I)
    name = re.sub(r"\bWAL-?MART\b", "Walmart", name, flags=re.I)
    name = re.sub(r",?\s*\b(LLC|INC|LTD|L\.?P\.?|CORP(ORATION)?|CO)\b\.?$", "", name, flags=re.I).strip(" ,")
    words = [w if w in ("CVS", "IGA", "OSU", "OU", "Walmart") else w.capitalize() for w in name.split()]
    return " ".join(words)[:40]


def main():
    zips = sorted({p["zip"]: p for p in services.places() if services.miles(CENTER, p) <= RADIUS}.values(),
                  key=lambda p: services.miles(CENTER, p))
    print(f"{len(zips)} ZIP codes within {RADIUS:.0f} miles of Athens")
    seen, out = set(), []
    for i, z in enumerate(zips, 1):
        try:
            r = requests.get(API, timeout=20, params={
                "version": "2.1", "taxonomy_description": "pharmacy", "postal_code": z["zip"],
                "state": "OH", "enumeration_type": "NPI-2", "limit": 200})
            results = r.json().get("results", [])
        except Exception as e:
            print("  skip", z["zip"], e)
            continue
        for res in results:
            basic = res.get("basic", {})
            tax = next((t for t in res.get("taxonomies", []) if t.get("primary")), {})
            loc = next((a for a in res.get("addresses", []) if a.get("address_purpose") == "LOCATION"), None)
            if basic.get("status") != "A" or not loc or not KEEP.match(tax.get("desc", "")):
                continue
            dba = next((o["organization_name"] for o in res.get("other_names", [])
                        if o.get("organization_name")), None)
            raw = dba or basic.get("organization_name") or ""
            if not raw or SKIP_NAME.search(raw):
                continue
            key = (loc["address_1"].upper(), loc["postal_code"][:5])
            if key in seen:
                continue
            seen.add(key)
            phone = re.sub(r"\D", "", loc.get("telephone_number", ""))
            out.append({
                "npi": res["number"], "name": short_name(raw), "address": loc["address_1"].title(),
                "town": loc["city"].title(), "zip": loc["postal_code"][:5],
                "phone": f"{phone[:3]}-{phone[3:6]}-{phone[6:10]}" if len(phone) >= 10 else "",
                "lat": z["lat"], "lon": z["lon"],
            })
        if i % 20 == 0:
            print(f"  {i}/{len(zips)} ZIPs, {len(out)} pharmacies so far")
        time.sleep(0.15)  # be gentle with a free public API
    data = {
        "_note": "Real pharmacies from the NPI Registry (CMS). Locations are ZIP-code centres, so distances "
                 "are approximate. No opening hours in the registry: tell people to call first. Rebuild with "
                 "tools/fetch_pharmacies.py.",
        "source": API, "retrieved": dt.date.today().isoformat(), "radius_miles": RADIUS,
        "pharmacies": sorted(out, key=lambda p: (p["town"], p["name"])),
    }
    (Path(__file__).resolve().parents[1] / "app/data/pharmacies.json").write_text(json.dumps(data, indent=1) + "\n")
    print(f"saved {len(out)} pharmacies to app/data/pharmacies.json")


if __name__ == "__main__":
    main()
