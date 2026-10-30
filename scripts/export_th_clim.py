#!/usr/bin/env python3
"""
Export the ERA5 climatology for the stratosphere time-height.

The full-resolution climatology (`data/era5_clim_full.json`, 37 levels x 4
domains) is a build-machine artifact - it is not committed, because it is
3 MB and the runner does not need the levels nobody plots.

The forecast time-height is drawn on the runner, and it needs the
climatology at the levels it shows. So that slice - and only that slice -
is exported here and committed:

    data/era5_clim_th.json    15 levels x 4 domains, per calendar day

    python3 scripts/export_th_clim.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "era5_clim_full.json")
OUT = os.path.join(ROOT, "data", "era5_clim_th.json")

# the levels a stratosphere cross-section shows
LEVELS = [1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 150, 200, 250, 300]


def main():
    src = json.load(open(SRC))
    out = {"years": src["years"], "levels_hPa": LEVELS, "domains": {}}
    for dom, lv in (src.get("domains") or {}).items():
        d = {}
        for lev in LEVELS:
            e = lv.get(str(lev))
            if not e:
                continue
            d[str(lev)] = {
                # MM-DD, so the artifact does not repeat a year per level
                "dates": [x[5:] for x in e["dates"]],
                "mean": e["mean"],
                "sigma": e["sigma"],
            }
        out["domains"][dom] = d
        print(f"  {dom}: {len(d)} levels")
    with open(OUT, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"wrote {OUT} ({os.path.getsize(OUT)//1024} KB)")


if __name__ == "__main__":
    main()
