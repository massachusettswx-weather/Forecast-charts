#!/usr/bin/env python3
"""Export a slim ERA5 climatology cache (only the levels the site plots) so the
repo can carry it; the full 295 MB cache stays on the build machine.

    python3 scripts/export_era5_slim.py --out data/era5_cache_slim
"""
import argparse
import os
import numpy as np
import pandas as pd

LEV = [1, 3, 10, 30, 50, 100, 250]

ap = argparse.ArgumentParser()
ap.add_argument("--src", default="/workspace/data/era5_cache")
ap.add_argument("--out", default="/workspace/data/era5_cache_slim")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

LV37 = [1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 125, 150, 175, 200, 225, 250,
        300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 775, 800, 825, 850,
        875, 900, 925, 950, 975, 1000]
idx = [LV37.index(l) for l in LEV]

m = pd.read_csv(os.path.join(a.src, "manifest.csv"))
prof = np.load(os.path.join(a.src, "profiles.npy"), mmap_mode="r")
sub = np.asarray(prof[:, idx, :], dtype=np.float16)
np.save(os.path.join(a.out, "profiles.npy"), sub)
m.to_csv(os.path.join(a.out, "manifest.csv"), index=False)
shutil_l = __import__("shutil").copy
shutil_l(os.path.join(a.src, "lat.txt"), os.path.join(a.out, "lat.txt"))
np.save(os.path.join(a.out, "level_index.npy"), np.array(idx))
print(f"slim cache written to {a.out}: {sub.shape} levels={LEV} "
      f"({os.path.getsize(os.path.join(a.out,'profiles.npy'))/1e6:.1f} MB)")
