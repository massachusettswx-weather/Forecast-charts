#!/usr/bin/env python3
"""
Export the ERA5 climatology (and the daily series) from the local u cache.

ONE sequential pass over the store. Everything the site needs from ERA5
comes out of this file:

    data/era5_clim_full.json
        levels_hPa        the 37 pressure levels
        domains/<name>/<level>/
            dates         the calendar days (MM-DD-12)
            mean, sigma   the 1991-2020 daily climatology and its spread
        recent/           the ERA5 analysis days (the reanalysis behind the
                          forecast), per domain and level

    data/era5_series.npz
        the raw daily series for the archive: every day of the record, every
        level, every domain - the thing the SSW archive and the time-height
        products are drawn from.

WHY ONE PASS
    The store is a memmap of [n_days, 37, 721] on a network volume. Reading
    it per level (37 passes of random access) takes hours; reading it once
    sequentially and reducing as we go takes minutes and a bounded amount of
    memory.
"""
import argparse
import csv
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.environ.get("ERA5_CACHE_DIR", os.path.join(ROOT, "data", "era5_cache"))
PROF = os.path.join(CACHE, "profiles.npy")
MAN = os.path.join(CACHE, "manifest.csv")
LAT = os.path.join(CACHE, "lat.txt")

LEV37 = [1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 125, 150, 175, 200, 225,
         250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 775, 800,
         825, 850, 875, 900, 925, 950, 975, 1000]

DOMAINS = {
    "60": {"lat": 60.0},
    "70": {"lat": 70.0},
    "0": {"lat": None},                 # NH: 0-90N, area weighted
    "cap": {"lat_range": (65.0, 90.0)},  # polar cap, area weighted
}


def load_manifest():
    rows = []
    with open(MAN, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append((int(r["year"]), int(r["month"]), int(r["day"]),
                         int(r["hour"])))
    return rows


def domain_sel(latv, kw):
    """(indices, weights) for a domain."""
    if "lat_range" in kw:
        lo, hi = kw["lat_range"]
        sel = np.where((latv >= lo) & (latv <= hi))[0]
        w = np.cos(np.deg2rad(latv[sel]))
    elif kw["lat"] is None:
        sel = np.where(latv >= 0)[0]
        w = np.cos(np.deg2rad(latv[sel]))
    else:
        sel = np.array([int(np.abs(latv - kw["lat"]).argmin())])
        w = np.ones(1)
    return sel, w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="1991-2020")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "era5_clim_full.json"))
    ap.add_argument("--series", default=os.path.join(ROOT, "data", "era5_series.npz"))
    ap.add_argument("--recent", type=int, default=120,
                    help="how many of the last ERA5 days to carry in the artifact")
    a = ap.parse_args()

    y0, y1 = (int(x) for x in a.years.split("-"))
    man = load_manifest()
    latv = np.loadtxt(LAT)
    arr = np.load(PROF, mmap_mode="r")
    n = len(man)
    print(f"store: {arr.shape[0]} rows, manifest {n} rows, lat {len(latv)}", flush=True)

    # the store rows follow the manifest order (the builder appends both)
    keys = [f"{m:02d}-{d:02d}-12" for (_y, m, d, _h) in man]
    years = np.array([r[0] for r in man])
    in_clim = (years >= y0) & (years <= y1)
    print(f"climatology years {y0}-{y1}: {int(in_clim.sum())} days", flush=True)

    # one pass: per (domain, level, calendar day) sum and sum of squares
    SELS = {d: domain_sel(latv, kw) for d, kw in DOMAINS.items()}
    ND = 372
    acc = {d: {"sum": np.zeros((len(LEV37), ND)),
               "sq": np.zeros((len(LEV37), ND)),
               "cnt": np.zeros(ND)} for d in DOMAINS}
    # the daily series for the archive, per domain and level
    series = {d: np.full((len(LEV37), n), np.nan, dtype=np.float32)
              for d in DOMAINS}
    # calendar-day index: (month-1)*31 + (day-1) -> 0..371 (Feb 29 included)
    md_idx = np.array([(m - 1) * 31 + (d - 1) for (_y, m, d, _h) in man],
                      dtype=int)
    ND = 372
    print("calendar-day index range:", md_idx.min(), md_idx.max(), flush=True)
    key_idx = md_idx

    CHUNK = 256
    for s in range(0, n, CHUNK):
        e = min(s + CHUNK, n)
        block = np.asarray(arr[s:e], dtype=np.float32)      # (n, 37, 721)
        for d, (sel, w) in SELS.items():
            sub = block[:, :, sel]                            # (n, 37, nlat)
            if sub.shape[2] > 1:
                v = (sub.astype(np.float64) * w).sum(axis=2) / w.sum()
            else:
                v = sub[:, :, 0].astype(np.float64)
            series[d][:, s:e] = v.T.astype(np.float32)
            m = in_clim[s:e]
            if not m.any():
                continue
            ki = key_idx[s:e][m]
            vi = v[m]
            for li in range(len(LEV37)):
                acc[d]["sum"][li] += np.bincount(ki, weights=vi[:, li], minlength=ND)
                acc[d]["sq"][li] += np.bincount(ki, weights=vi[:, li] ** 2,
                                               minlength=ND)
            acc[d]["cnt"] += np.bincount(ki, minlength=ND)
        if (s // CHUNK) % 10 == 0:
            print(f"  {e}/{n} days", flush=True)

    # ---------------------------------------------------------------- json
    out = {"years": [y0, y1], "levels_hPa": LEV37, "domains": {}}
    for d in DOMAINS:
        dd = {}
        s1, s2, cnt = acc[d]["sum"], acc[d]["sq"], acc[d]["cnt"]
        mean = np.where(cnt > 0, s1 / np.maximum(cnt, 1), np.nan)
        var = np.where(cnt > 1, s2 / np.maximum(cnt, 1) - mean ** 2, np.nan)
        sigma = np.sqrt(np.maximum(var, 0))
        for li, lev in enumerate(LEV37):
            ok = np.where(cnt > 0)[0]
            dates = [f"{y0}-{int(k)//31+1:02d}-{int(k)%31+1:02d}" for k in ok]
            dd[str(lev)] = {
                "dates": dates,
                "mean": [float(x) for x in mean[li, ok]],
                "sigma": [float(x) for x in sigma[li, ok]],
            }
        out["domains"][d] = dd
        print(f"domain {d}: {len(dd)} levels", flush=True)

    # the most recent ERA5 days (the reanalysis behind the forecast)
    nrec = min(a.recent, n)
    rec_rows = list(range(n - nrec, n))
    out["recent"] = {
        "dates": [f"{man[i][0]}-{man[i][1]:02d}-{man[i][2]:02d}" for i in rec_rows],
        "domains": {d: {str(lev): [float(x) for x in series[d][li, rec_rows]]
                        for li, lev in enumerate(LEV37)} for d in DOMAINS},
    }

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(out, fh)
    print("wrote", a.out, os.path.getsize(a.out) // 1024, "KB", flush=True)

    # ---------------------------------------------------------------- npz
    dates = np.array([f"{y:04d}-{m:02d}-{d:02d}" for (y, m, d, _h) in man])
    np.savez_compressed(
        a.series,
        dates=dates,
        levels=np.array(LEV37),
        **{f"u_{d}": series[d] for d in DOMAINS})
    print("wrote", a.series, os.path.getsize(a.series) // 1024, "KB", flush=True)


if __name__ == "__main__":
    main()
