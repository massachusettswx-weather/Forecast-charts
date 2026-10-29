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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.environ.get("ERA5_CACHE_DIR", os.path.join(ROOT, "data", "era5_cache"))
# ARCO variable names -> the local store keeps one profile file per variable
VARS = {"u": "u_component_of_wind",
        "v": "v_component_of_wind",
        "t": "temperature",
        "z": "geopotential"}


def prof_path(var="u"):
    return f"{CACHE}/profiles.npy" if var == "u" else f"{CACHE}/profiles_{var}.npy"


def man_path(var="u"):
    return f"{CACHE}/manifest.csv" if var == "u" else f"{CACHE}/manifest_{var}.csv"


PROF = prof_path()
MAN = man_path()
LATFILE = f"{CACHE}/lat.txt"
SLIMIDX = f"{CACHE}/level_index.npy"      # present only in the slim (subset) cache
B37 = ("https://storage.googleapis.com/gcp-public-data-arco-era5/ar/"
       "1959-2022-full_37-6h-0p25deg-chunk-1.zarr-v2")
LEV37 = [1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 125, 150, 175, 200, 225, 250,
         300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 775, 800, 825, 850,
         875, 900, 925, 950, 975, 1000]
NLAT, NLEV = 721, 37
BLOSC = None
_lock = threading.Lock()


def blosc():
    """numcodecs is only needed to read the ARCO zarr chunks (cache building)."""
    global BLOSC
    if BLOSC is None:
        import numcodecs
        BLOSC = numcodecs.Blosc()
    return BLOSC
_miss = []


# ----------------------------------------------------------------- store io
def _load_lat():
    if not os.path.exists(LATFILE):
        os.makedirs(CACHE, exist_ok=True)
        r = requests.get(f"{B37}/latitude/0", timeout=(20, 120))
        r.raise_for_status()
        lat = np.frombuffer(blosc().decode(r.content), dtype="<f4").astype(np.float64)
        np.savetxt(LATFILE, lat)
    return np.loadtxt(LATFILE)


def manifest(var="u"):
    mf = man_path(var)
    if os.path.exists(mf):
        return pd.read_csv(mf)
    return pd.DataFrame(columns=["step_id", "year", "month", "day", "hour",
                                 "used_hour", "level_index"])


# The store is a memory-mapped .npy with room for the whole record: appending
# rewrote the whole file (245 MB and growing) on every flush, and on this
# network volume that read+write dominated the build (minutes per flush).
# With the memmap a flush only writes the new rows.
CAPACITY = 14000         # rows: 30 years x 365 days = 10950, plus the
                        # 1959-1990 rows still in the store (the resize
                        # rewrites the whole file, so do it once)


def _append(rows, profiles, var="u"):
    """Append (step rows, profile blocks) to the store, in place."""
    os.makedirs(CACHE, exist_ok=True)
    m = manifest(var)
    start = len(m)
    n = len(profiles)
    pf = prof_path(var)
    if os.path.exists(pf):
        arr = np.lib.format.open_memmap(pf, mode="r+")
        if arr.shape[0] < start + n:
            # the store was written before the memmap change: grow it once
            old = np.asarray(arr[:start], dtype="<f4")
            del arr
            arr = np.lib.format.open_memmap(pf + ".tmp.npy", mode="w+",
                                            dtype="<f4",
                                            shape=(max(CAPACITY, start + n),
                                                   NLEV, NLAT))
            arr[:start] = old
            arr.flush()
            del arr
            os.replace(pf + ".tmp.npy", pf)
            arr = np.lib.format.open_memmap(pf, mode="r+")
    else:
        arr = np.lib.format.open_memmap(pf, mode="w+", dtype="<f4",
                                        shape=(max(CAPACITY, start + n),
                                               NLEV, NLAT))
    arr[start:start + n] = np.asarray(profiles, dtype="<f4")
    arr.flush()
    del arr
    m = pd.concat([m, pd.DataFrame(rows, columns=list(m.columns))], ignore_index=True)
    m.to_csv(man_path(var), index=False)


def _chunk(tidx, tries=4, var="u"):
    """Fetch one ARCO chunk; returns None if that step is absent from the store."""
    name = VARS.get(var, var)
    for k in range(tries):
        try:
            r = requests.get(f"{B37}/{name}/{tidx}.0.0.0", timeout=(20, 300))
            if r.status_code != 200:
                return None
            return np.frombuffer(blosc().decode(r.content), dtype="<f4").reshape(NLEV, NLAT, 1440)
        except Exception:
            time.sleep(1.5 * (k + 1))
    return None


def _time_axis():
    parts = []
    for i in range(4):
        r = requests.get(f"{B37}/time/{i}", timeout=(20, 120))
        r.raise_for_status()
        parts.append(np.frombuffer(blosc().decode(r.content), dtype="<i8"))
    hrs = np.concatenate(parts)
    return np.array([np.datetime64("1959-01-01") + np.timedelta64(int(x), "h") for x in hrs])


# ----------------------------------------------------------------- builder
def build(start="10-25", end="11-09", years=range(1991, 2021), hour=12, workers=8,
          var="u"):
    """Fetch every (year, date) in the window that is not already cached."""
    if os.path.exists(SLIMIDX):
        raise RuntimeError(
            f"{CACHE} is the SLIM cache (levels 1/3/10/30/50/100/250) - it is "
            "read-only. Point ERA5_CACHE_DIR at the full cache to extend it.")
    t = _time_axis()
    m0 = manifest(var)
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
    # the store must carry its latitude axis: the queries (NH mean, polar
    # cap) and the slim export both need it
    if not os.path.exists(LATFILE):
        np.savetxt(LATFILE, 90.0 - 0.25 * np.arange(NLAT), fmt="%.6f")
    print(f"cache[{var}]: {len(jobs)} steps to fetch ({len(m0)} already stored)",
          flush=True)
    if not jobs:
        return
    t0 = time.time()

    def work(job):
        y, d = job
        ts = d + pd.Timedelta(hours=hour)
        i = int(np.searchsorted(t, np.datetime64(ts, "ns")))
        for off in range(0, 7):
            for o in ((off, -off) if off else (0,)):
                c = _chunk(i + o, var=var)
                if c is None:
                    continue
                prof = c.mean(axis=2)                    # (37, 721) zonal mean
                used = ts + np.timedelta64(o * 6, "h")
                return (y, d.month, d.day, hour, i + o, used), prof
        raise RuntimeError(f"no ARCO data near {ts}")

    rows, profs = [], []
    FLUSH = 25          # the store is rewritten on each flush - do it in batches
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for k, (r_, p_) in enumerate(ex.map(work, jobs)):
            rows.append((r_[4], r_[0], r_[1], r_[2], r_[3],
                         str(pd.Timestamp(r_[5]).replace(tzinfo=None)), LEV37.index(10)))
            profs.append(p_)
            if (k + 1) % FLUSH == 0:
                _append(rows, profs, var)
                rows, profs = [], []
                el = time.time() - t0
                print(f"  {k+1}/{len(jobs)} {el:.0f}s eta {el/(k+1)*(len(jobs)-k-1):.0f}s",
                      flush=True)
    if rows:
        _append(rows, profs, var)
    print(f"cache[{var}] updated: {len(rows)} steps added -> "
          f"{len(manifest(var))} total", flush=True)


# ----------------------------------------------------------------- query
def climatology(level_hPa, lat=None, start="01-01", end="12-31",
                years=range(1991, 2021), agg="mean", lat_range=None, var="u"):
    """
    Return (mean, std, count) of ERA5 zonal-mean u at `level_hPa` from the
    local cache, for every (date, hour) in the window, aggregated over `years`.

    lat=None    -> NH (0-90N) area-weighted mean
    lat=float   -> zonal-mean u at that latitude
    lat_range   -> area-weighted mean over that latitude band, e.g. (65, 90)
                   for the polar cap
    """
    m = manifest(var)
    if not len(m):
        raise RuntimeError(f"ERA5 cache[{var}] empty - run: era5_cache.py build")
    latv = _load_lat()
    lev = LEV37.index(int(level_hPa))
    if os.path.exists(SLIMIDX):
        # slim cache: rows are [1, 3, 10, 30, 50, 100, 250], not all 37 levels
        slim = np.load(SLIMIDX)
        hit = np.where(slim == lev)[0]
        if not len(hit):
            raise RuntimeError(f"level {level_hPa} hPa is not in the slim cache {CACHE}")
        lev = int(hit[0])
    sel = m[(m["year"].isin(list(years)))]
    sel = sel[_season_mask(sel["month"], sel["day"], start, end)]
    if not len(sel):
        raise RuntimeError(f"no cached data for {start}-{end}")
    # profiles.npy rows are in manifest order; step_id is the ARCO chunk index
    arr = np.load(prof_path(var), mmap_mode="r")
    rows_ix = sel.index.to_numpy()            # positional rows in the store
    block = np.asarray(arr[rows_ix, lev, :], dtype=np.float64)  # (n, 721)
    if lat_range is not None:
        lo, hi = float(lat_range[0]), float(lat_range[1])
        sel_lat = (latv >= lo) & (latv <= hi)
        w = np.cos(np.deg2rad(latv[sel_lat]))
        vals = (block[:, sel_lat] * w).sum(axis=1) / w.sum()
    elif lat is None:
        w = np.cos(np.deg2rad(latv[latv >= 0]))
        vals = (block[:, latv >= 0] * w).sum(axis=1) / w.sum()
    else:
        j = int(np.abs(latv - float(lat)).argmin())
        vals = block[:, j]
    # zero-pad, so the keys sort as the calendar does and match what the
    # charts look up ("MM-DD-12"); unpadded keys sorted as 10-1, 10-10, 10-2...
    key = (sel["month"].astype(str).str.zfill(2) + "-" +
           sel["day"].astype(str).str.zfill(2) + "-" +
           sel["hour"].astype(str).str.zfill(2))
    g = pd.DataFrame({"k": key.to_numpy(), "v": vals}).groupby("k")["v"]
    mean, std, cnt = g.mean(), g.std(), g.count()
    return mean, std, cnt


_DAILY_CACHE = {}


def _season_mask(month, day, start, end):
    """Rows inside the (month-day) window, which may wrap the year end.

    The SSW season runs Nov -> May, so "after the start" OR "before the end"
    is the right test there (a plain AND would select nothing).
    """
    s_m, s_d = (int(x) for x in start.split("-"))
    e_m, e_d = (int(x) for x in end.split("-"))
    after = (month > s_m) | ((month == s_m) & (day >= s_d))
    before = (month < e_m) | ((month == e_m) & (day <= e_d))
    return (after & before) if s_m <= e_m else (after | before)

def daily_series(level_hPa, lat=None, lat_range=None, years=range(1991, 2021),
                 start="11-01", end="05-31", var="u"):
    """Every DAILY value in the cache for the window -> (dates, values).

    The climatology() query averages the years away; this keeps them, which is
    what the daily statistics (e.g. how often the wind is easterly) need.
    """
    m = manifest(var)
    if not len(m):
        raise RuntimeError(f"ERA5 cache[{var}] empty - run: era5_cache.py build")
    latv = _load_lat()
    lev = LEV37.index(int(level_hPa))
    if os.path.exists(SLIMIDX):
        slim = np.load(SLIMIDX)
        hit = np.where(slim == lev)[0]
        if not len(hit):
            raise RuntimeError(f"level {level_hPa} hPa is not in the slim cache {CACHE}")
        lev = int(hit[0])
    sel = m[m["year"].isin(list(years))]
    sel = sel[_season_mask(sel["month"], sel["day"], start, end)]
    if not len(sel):
        raise RuntimeError(f"no cached data for {start}-{end}")
    # the block read is the expensive part - cache it (the domains share it)
    key = (var, int(level_hPa), tuple(years), start, end)
    hit = _DAILY_CACHE.get(key)
    if hit is None:
        arr = np.load(prof_path(var), mmap_mode="r")
        rows_ix = sel.index.to_numpy()
        block = np.asarray(arr[rows_ix, lev, :], dtype=np.float64)
        dates = pd.to_datetime(sel["year"].astype(str) + "-" +
                               sel["month"].astype(str).str.zfill(2) + "-" +
                               sel["day"].astype(str).str.zfill(2))
        _DAILY_CACHE[key] = (dates.reset_index(drop=True), block)
    else:
        dates, block = hit
    if lat_range is not None:
        lo, hi = float(lat_range[0]), float(lat_range[1])
        sel_lat = (latv >= lo) & (latv <= hi)
        w = np.cos(np.deg2rad(latv[sel_lat]))
        vals = (block[:, sel_lat] * w).sum(axis=1) / w.sum()
    elif lat is None:
        w = np.cos(np.deg2rad(latv[latv >= 0]))
        vals = (block[:, latv >= 0] * w).sum(axis=1) / w.sum()
    else:
        j = int(np.abs(latv - float(lat)).argmin())
        vals = block[:, j]
    return dates, pd.Series(vals)


def record_min(level_hPa, lat=None, lat_range=None, years=range(1991, 2021),
               start="11-01", end="05-31", var="u"):
    """The lowest daily value in the record -> (value, "YYYY-MM-DD").

    At 60N/10 hPa the daily climatology is westerly all season, so this is the
    yardstick for how unusual a forecast reversal really is.
    """
    dates, vals = daily_series(level_hPa, lat, lat_range, years, start, end, var)
    i = int(np.argmin(vals.to_numpy()))
    return float(vals.iloc[i]), str(dates.iloc[i].date())


def easterly_frequency(level_hPa, lat=None, lat_range=None,
                       years=range(1991, 2021), start="11-01", end="05-31",
                       var="u"):
    """% of the days in the record with an easterly (< 0 m/s) wind, per
    calendar day.  {month-day: percent}"""
    dates, vals = daily_series(level_hPa, lat, lat_range, years, start, end, var)
    md = dates.dt.strftime("%m-%d").to_numpy()
    v = vals.to_numpy()
    below = v < 0
    out = {}
    for k in np.unique(md):
        m = md == k
        out[k] = float(below[m].mean() * 100.0)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "clim", "info"])
    ap.add_argument("--start", default="10-25")
    ap.add_argument("--end", default="11-09")
    ap.add_argument("--level", type=int, default=10)
    ap.add_argument("--lat", type=float, default=60.0)
    ap.add_argument("--years", default=None, help="e.g. 1991-2020 (default 1991-2020)")
    ap.add_argument("--hour", type=int, default=12)
    ap.add_argument("--var", default="u", choices=sorted(VARS))
    ap.add_argument("--workers", type=int, default=8,
                    help="parallel chunk downloads (the build is network bound)")
    a = ap.parse_args()
    if a.cmd == "build":
        years = range(int(a.years.split("-")[0]), int(a.years.split("-")[1]) + 1) if a.years else range(1991, 2021)
        build(a.start, a.end, years=years, hour=a.hour, var=a.var,
              workers=a.workers)
    elif a.cmd == "clim":
        mean, std, cnt = climatology(a.level, lat=a.lat, start=a.start, end=a.end)
        print(pd.DataFrame({"mean": mean, "std": std, "n": cnt}).to_string())
    else:
        m = manifest(a.var)
        print(f"cached steps[{a.var}]:", len(m), "size:",
              f"{os.path.getsize(PROF)/1e6:.0f} MB" if os.path.exists(PROF) else "n/a")
        if len(m):
            print(m.head(3).to_string())
            md = (m["month"].astype(int) * 100 + m["day"].astype(int))
            print("date range:", f"{md.min()//100:02d}-{md.min()%100:02d}",
                  "->", f"{md.max()//100:02d}-{md.max()%100:02d}")
