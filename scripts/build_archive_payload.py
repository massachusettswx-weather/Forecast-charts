#!/usr/bin/env python3
"""
Build the ERA5 archive payload for the browser.

One JSON file per domain, fetched on demand when the reader picks it:

    data/store/archive/u_<domain>.json
        dates      the 13902 days of the record
        levels     the 37 pressure levels
        u          the zonal-mean u for every level and day
        clim       the 1991-2020 daily mean and sigma, per level and day

The files are gzipped (the site serves them as .json.gz and the browser
unpacks them). Nothing here needs the network - it all comes out of the
local ERA5 store.

    python3 scripts/build_archive_payload.py
"""
import gzip
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERIES = os.path.join(ROOT, "data", "era5_series.npz")
CLIM = os.path.join(ROOT, "data", "era5_clim_full.json")
OUT = os.path.join(ROOT, "data", "store", "archive")


def main():
    os.makedirs(OUT, exist_ok=True)
    z = np.load(SERIES)
    dates = [str(d) for d in z["dates"]]
    levels = [int(l) for l in z["levels"]]
    clim = json.load(open(CLIM))
    # the reversal events of each domain: the browser marks them on the
    # series, so they travel with the payload (the markers must follow the
    # domain the reader picks)
    arch = json.load(open(os.path.join(ROOT, "data", "ssw_archive.json")))
    ev_by_dom = {d: [{"start": e["start"], "end": e["end"], "min": e["min"],
                      "days": e["days"], "level": e.get("min_level")}
                     for e in (v.get("events") or [])]
                 for d, v in (arch.get("domains") or {}).items()}

    for dom in ("60", "70", "0", "cap"):
        s = z[f"u_{dom}"]
        u = [[None if x != x else round(float(x), 2) for x in s[li]]
             for li in range(len(levels))]
        # the climatology, as a per-level map of calendar day -> (mean, sigma)
        cd = clim["domains"].get(dom, {})
        cl = {}
        for li, lev in enumerate(levels):
            e = cd.get(str(lev)) or {}
            dd = e.get("dates") or []
            cl[str(lev)] = {
                "dates": [d[5:] for d in dd],          # MM-DD
                "mean": [round(float(x), 2) for x in (e.get("mean") or [])],
                "sigma": [round(float(x), 2) for x in (e.get("sigma") or [])],
            }
        payload = {"dates": dates, "levels": levels, "u": u, "clim": cl,
                   "events": ev_by_dom.get(dom, [])}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        p = os.path.join(OUT, f"u_{dom}.json.gz")
        with gzip.open(p, "wb", compresslevel=9) as fh:
            fh.write(raw)
        print(f"  {dom}: {len(raw)/1e6:.1f} MB raw -> "
              f"{os.path.getsize(p)/1e6:.2f} MB gzipped "
              f"({len(payload['events'])} events)", flush=True)


if __name__ == "__main__":
    main()
