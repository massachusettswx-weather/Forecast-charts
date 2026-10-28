#!/usr/bin/env python3
"""
Export a small summary of the ERA5 climatology cache.

The runner only carries the slim cache, so the long-record statistics (the
daily mean/sigma per level and domain, the record minimum, how often the
polar night is broken) are exported here into a compact artifact that is
committed with the repo and read by the site build.

    ERA5_CACHE_DIR=... ./venv/bin/python scripts/era5_summary.py \
        --years 1991-2020 --out data/era5_summary.json
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import era5_cache as E  # noqa: E402

DOMAINS = {"60N": dict(lat=60.0), "70N": dict(lat=70.0),
           "NH": dict(lat=None), "cap": dict(lat_range=(65.0, 90.0))}
# the SSW season - the record statistics are quoted for the winter half-year
# (Nov -> May), not for the whole year: a summer value would not be a
# polar-vortex yardstick, and the deepest reversals of a season often come in
# January/February, which an Oct-Dec window would miss
SEASON = ("11-01", "05-31")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="1991-2020")
    ap.add_argument("--levels", default="1,3,10,30,50,100,250")
    ap.add_argument("--start", default="01-01")
    ap.add_argument("--end", default="12-31")
    ap.add_argument("--out", default="data/era5_summary.json")
    a = ap.parse_args()
    y0, y1 = (int(x) for x in a.years.split("-"))
    years = range(y0, y1 + 1)
    levels = [int(x) for x in a.levels.split(",")]

    out = {"source": "ERA5 via ARCO-ERA5 (google-cloud-public-data-arco-era5)",
           "years": [y0, y1], "months": [a.start, a.end],
           "season": list(SEASON), "levels_hPa": levels,
           "domains": {}, "climatology": {}}

    for dname, kw in DOMAINS.items():
        d = {}
        for lev in levels:
            try:
                v, dt = E.record_min(lev, years=years, start=SEASON[0],
                                     end=SEASON[1], **kw)
                d[str(lev)] = {"record_min": v, "record_min_date": dt}
            except Exception as exc:
                d[str(lev)] = {"error": str(exc)}
        out["domains"][dname] = d
        print(dname, json.dumps(d), flush=True)

    # the daily climatology (mean/sigma) for the levels the site uses
    for lev in levels:
        for dname, kw in DOMAINS.items():
            key = f"{dname}|{lev}"
            try:
                mean, sd, cnt = E.climatology(lev, years=years, start=a.start,
                                              end=a.end, **kw)
                idx = pd.date_range(f"{y0}-{a.start}", periods=len(mean),
                                    freq="D")
                out["climatology"][key] = {
                    "dates": [str(d.date()) for d in idx],
                    "mean": [float(x) for x in np.asarray(mean).ravel()],
                    "sigma": [float(x) for x in np.asarray(sd).ravel()],
                    "n": [int(x) for x in np.asarray(cnt).ravel()]}
            except Exception as exc:
                print("clim failed", key, exc, flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(out, fh)
    print("wrote", a.out, os.path.getsize(a.out) / 1e6, "MB")


if __name__ == "__main__":
    main()
