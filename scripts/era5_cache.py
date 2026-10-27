#!/usr/bin/env python3
"""
Local ERA5 climatology store  --  build once, query forever.

WHAT IT IS
    A local, resumable cache of ERA5 zonal-mean (zonal average) u profiles at
    ALL 37 pressure levels, for 1991-2020, on ARCO-ERA5's native 0.25-deg grid.
    Because each ARCO chunk holds every level for one 6-hourly step, one
    download yields 37 level profiles -> any level/latitude/aggregate can be
    answered locally from then on (no more ARCO round-trips per figure).

STORE
    data/era5_cache/profiles.npy   float32 [n_steps, 37, 721]  (level, lat)
    data/era5_cache/manifest.csv   step_id, year, month, day, hour, level_index
    data/era5_cache/lat.txt        721 latitudes, 90 -> -90 (0.25 deg)

USAGE
    # build / extend (resumable; only fetches what is missing)
    python3 scripts/era5_cache.py build --start 10-25 --end 11-09 --years 1991-2020

    # query (instant, local)
    python3 scripts/era5_cache.py clim --level 10 --lat 60
    python3 scripts/era5_cache.py clim --level 10 --start 10-25 --end 11-09

    from era5_cache import climatology
    mean, std = climatology(10, lat=60, start="10-25", end="11-09")
"""
import argparse
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests
import numcodecs

CACHE = os.environ.get("ERA5_CACHE_DIR", "/workspace/data/era5_cache")
PROF = f"{CACHE}/profiles.npy"
MAN = f"{CACHE}/manifest.csv"
LATFILE = f"{CACHE}/lat.txt"
B37 = ("https://storage.googleapis.com/gcp-public-data-arco-era5/ar/"
       "1959-2022-full_37-6h-0p25deg-chunk-1.zarr-v2")
LEV37 = [1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 125, 150, 175, 200, 225, 250,
         300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 775, 800, 825, 850,
         875, 900, 925, 950, 975, 1000]
NLAT, NLEV = 721, 37
BLOSC = numcodecs.Blosc()
_lock = threading.Lock()
_miss = []


# ----------------------------------------------------------------- store io
def _load_lat():
    if not os.path.exists(LATFILE):
        os.makedirs(CACHE, exist_ok=True)
        r = requests.get(f"{B37}/latitude/0", timeout=(20, 120))
        r.raise_for_status()
        lat = np.frombuffer(BLOSC.decode(r.content), dtype="<f4").astype(np.float64)
        np.savetxt(LATFILE, lat)
    return np.loadtxt(LATFILE)


def manifest():
    if os.path.exists(MAN):
        return pd.read_csv(MAN)
    return pd.DataFrame(columns=["step_id", "year", "month", "day", "hour",
                                 "used_hour", "level_index"])


def _append(rows, profiles):
    """Append (step rows, profile blocks) to the store, writing a valid .npy."""
    os.makedirs(CACHE, exist_ok=True)
    m = manifest()
    start = len(m)
    n = len(profiles)
    if os.path.exists(PROF):
        old = np.load(PROF, mmap_mode="r")
        arr = np.zeros((start + n, NLEV, NLAT), dtype="<f4")
        arr[:start] = old[:]
    else:
        arr = np.zeros((start + n, NLEV, NLAT), dtype="<f4")
    arr[start:] = np.asarray(profiles, dtype="<f4")
    np.save(PROF + ".tmp.npy", arr)
    os.replace(PROF + ".tmp.npy", PROF)
    m = pd.concat([m, pd.DataFrame(rows, columns=list(m.columns))], ignore_index=True)
    m.to_csv(MAN, index=False)


def _chunk(tidx, tries=4):
    """Fetch one ARCO chunk; returns None if that step is absent from the store."""
    for k in range(tries):
        try:
            r = requests.get(f"{B37}/u_component_of_wind/{tidx}.0.0.0", timeout=(20, 300))
            if r.status_code != 200:
                return None
            return np.frombuffer(BLOSC.decode(r.content), dtype="<f4").reshape(NLEV, NLAT, 1440)
        except Exception:
            time.sleep(1.5 * (k + 1))
    return None


def _time_axis():
    parts = []
    for i in range(4):
        r = requests.get(f"{B37}/time/{i}", timeout=(20, 120))
        r.raise_for_status()
        parts.append(np.frombuffer(BLOSC.decode(r.content), dtype="<i8"))
    hrs = np.concatenate(parts)
    return np.array([np.datetime64("1959-01-01") + np.timedelta64(int(x), "h") for x in hrs])


# ----------------------------------------------------------------- builder
def build(start="10-25", end="11-09", years=range(1991, 2021), hour=12, workers=8):
    """Fetch every (year, date) in the window that is not already cached."""
    t = _time_axis()
    m0 = manifest()
    have = set(zip(m0["year"], m0["month"], m0["day"], m0["hour"])) if len(m0) else set()
    s_m, s_d = (int(x) for x in start.split("-"))
    e_m, e_d = (int(x) for x in end.split("-"))
    jobs = []
    for y in years:
        cur = pd.Timestamp(f"{y}-{s_m:02d}-{s_d:02d}")
        last = pd.Timestamp(f"{y}-{e_m:02d}-{e_d:02d}")
        while cur <= last:
            if (y, cur.month, cur.day, hour) not in have:
                jobs.append((y, cur))
            cur += pd.Timedelta(days=1)
    print(f"cache: {len(jobs)} steps to fetch ({len(m0)} already stored)", flush=True)
    if not jobs:
        return
    t0 = time.time()

    def work(job):
        y, d = job
        ts = d + pd.Timedelta(hours=hour)
        i = int(np.searchsorted(t, np.datetime64(ts, "ns")))
        for off in range(0, 7):
            for o in ((off, -off) if off else (0,)):
                c = _chunk(i + o)
                if c is None:
                    continue
                prof = c.mean(axis=2)                    # (37, 721) zonal mean
                used = ts + np.timedelta64(o * 6, "h")
                return (y, d.month, d.day, hour, i + o, used), prof
        raise RuntimeError(f"no ARCO data near {ts}")

    rows, profs = [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for k, (r_, p_) in enumerate(ex.map(work, jobs)):
            rows.append((r_[4], r_[0], r_[1], r_[2], r_[3],
                         str(pd.Timestamp(r_[5]).replace(tzinfo=None)), LEV37.index(10)))
            profs.append(p_)
            if (k + 1) % 50 == 0:
                el = time.time() - t0
                print(f"  {k+1}/{len(jobs)} {el:.0f}s eta {el/(k+1)*(len(jobs)-k-1):.0f}s", flush=True)
    _append(rows, profs)
    print("cache updated:", len(rows), "steps added ->", len(manifest()), "total", flush=True)


# ----------------------------------------------------------------- query
def climatology(level_hPa, lat=None, start="01-01", end="12-31",
                years=range(1991, 2021), agg="mean"):
    """
    Return (mean, std, count) of ERA5 zonal-mean u at `level_hPa` from the
    local cache, for every (date, hour) in the window, aggregated over `years`.

    lat=None  -> NH (0-90N) area-weighted mean
    lat=float -> zonal-mean u at that latitude
    """
    m = manifest()
    if not len(m):
        raise RuntimeError("ERA5 cache empty - run: era5_cache.py build")
    latv = _load_lat()
    lev = LEV37.index(int(level_hPa))
    s_m, s_d = (int(x) for x in start.split("-"))
    e_m, e_d = (int(x) for x in end.split("-"))
    sel = m[(m["year"].isin(list(years)))]
    sel = sel[((sel["month"] > s_m) | ((sel["month"] == s_m) & (sel["day"] >= s_d))) &
              ((sel["month"] < e_m) | ((sel["month"] == e_m) & (sel["day"] <= e_d)))]
    if not len(sel):
        raise RuntimeError(f"no cached data for {start}-{end}")
    # profiles.npy rows are in manifest order; step_id is the ARCO chunk index
    arr = np.load(PROF, mmap_mode="r")
    rows_ix = sel.index.to_numpy()            # positional rows in the store
    block = np.asarray(arr[rows_ix, lev, :], dtype=np.float64)  # (n, 721)
    if lat is None:
        w = np.cos(np.deg2rad(latv[latv >= 0]))
        vals = (block[:, latv >= 0] * w).sum(axis=1) / w.sum()
    else:
        j = int(np.abs(latv - float(lat)).argmin())
        vals = block[:, j]
    key = sel["month"].astype(str) + "-" + sel["day"].astype(str) + "-" + sel["hour"].astype(str)
    g = pd.DataFrame({"k": key.to_numpy(), "v": vals}).groupby("k")["v"]
    mean, std, cnt = g.mean(), g.std(), g.count()
    return mean, std, cnt


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "clim", "info"])
    ap.add_argument("--start", default="10-25")
    ap.add_argument("--end", default="11-09")
    ap.add_argument("--level", type=int, default=10)
    ap.add_argument("--lat", type=float, default=60.0)
    ap.add_argument("--years", default=None, help="e.g. 1991-2020 (default 1991-2020)")
    ap.add_argument("--hour", type=int, default=12)
    a = ap.parse_args()
    if a.cmd == "build":
        years = range(int(a.years.split("-")[0]), int(a.years.split("-")[1]) + 1) if a.years else range(1991, 2021)
        build(a.start, a.end, years=years, hour=a.hour)
    elif a.cmd == "clim":
        mean, std, cnt = climatology(a.level, lat=a.lat, start=a.start, end=a.end)
        print(pd.DataFrame({"mean": mean, "std": std, "n": cnt}).to_string())
    else:
        m = manifest()
        print("cached steps:", len(m), "size:",
              f"{os.path.getsize(PROF)/1e6:.0f} MB" if os.path.exists(PROF) else "n/a")
        if len(m):
            print(m.head(3).to_string())
            md = (m["month"].astype(int) * 100 + m["day"].astype(int))
            print("date range:", f"{md.min()//100:02d}-{md.min()%100:02d}",
                  "->", f"{md.max()//100:02d}-{md.max()%100:02d}")
