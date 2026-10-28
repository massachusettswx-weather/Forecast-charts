#!/usr/bin/env python3
"""Export a slim ERA5 climatology cache (only the levels the site plots) so the
repo can carry it; the full 37-level cache stays on the build machine.

The site only ever plots northern latitudes (60N, 70N, the 0-90N mean and the
65-90N polar cap), so the export keeps 0-90N only - that keeps the file under
GitHub's 100 MB per-file limit for a full 30-year, all-season cache.

    python3 scripts/export_era5_slim.py --src data/era5_cache --out data/era5_cache_slim
"""
import argparse
import os
import shutil

import numpy as np
import pandas as pd

LEV = [1, 3, 10, 30, 50, 100, 250]
LAT_MAX = 90.0            # the site's domains are all in 0-90N

ap = argparse.ArgumentParser()
ap.add_argument("--src", default="data/era5_cache")
ap.add_argument("--out", default="data/era5_cache_slim")
ap.add_argument("--years", default="1991-2020")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

LV37 = [1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 125, 150, 175, 200, 225, 250,
        300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 775, 800, 825, 850,
        875, 900, 925, 950, 975, 1000]
idx = [LV37.index(l) for l in LEV]

y0, y1 = (int(x) for x in a.years.split("-"))
m = pd.read_csv(os.path.join(a.src, "manifest.csv"))
prof = np.load(os.path.join(a.src, "profiles.npy"), mmap_mode="r")
lat = np.loadtxt(os.path.join(a.src, "lat.txt"))

keep_year = m[(m["year"] >= y0) & (m["year"] <= y1)].index.to_numpy()
keep_lat = np.where(lat <= LAT_MAX)[0]

# the manifest keeps its original order; the profiles are indexed by position
m_out = m.loc[keep_year].reset_index(drop=True)
rows = m_out.index.to_numpy()                     # positions in the source store
sub = np.asarray(prof[rows][:, idx, :][:, :, keep_lat], dtype=np.float16)
np.save(os.path.join(a.out, "profiles.npy"), sub)
m_out.to_csv(os.path.join(a.out, "manifest.csv"), index=False)
np.savetxt(os.path.join(a.out, "lat.txt"), lat[keep_lat], fmt="%.6f")
np.save(os.path.join(a.out, "level_index.npy"), np.array(idx))
print(f"slim cache written to {a.out}: {sub.shape} levels={LEV} "
      f"lats={lat[keep_lat][0]:.1f}..{lat[keep_lat][-1]:.1f} "
      f"({os.path.getsize(os.path.join(a.out, 'profiles.npy'))/1e6:.1f} MB)")
