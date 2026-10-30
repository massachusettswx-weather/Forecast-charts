#!/usr/bin/env python3
"""
The ERA5 geopotential-height cache: the zonal-mean height, 1990-2020.

The stratosphere is best read in height: a polar vortex is a cold, deep
column of air, and the classic measure of a sudden stratospheric warming is
the ANOMALY OF THE GEOPOTENTIAL HEIGHT at 10 hPa over the polar cap (the
lower the height, the warmer and weaker the vortex).

This downloads the ERA5 `geopotential` on the 37 pressure levels from ARCO
and keeps only the zonal means it needs:

    cap    the area-weighted mean over 60-90N
    60     the 60N latitude (the reference the SSW literature uses)
    0      the area-weighted mean over 0-90N

The ARCO chunks hold one 6-hourly step of the whole 0.25 deg globe
(37 x 721 x 1440, ~120 MB compressed), so only the 12Z step of each day is
fetched - that is the daily series. It is ~1.3 TB of transfer for 1990-2020;
the job is resumable (one row per finished day) and is meant to run in the
background.

    python3 scripts/era5_height_cache.py --years 1990-2020 --workers 4
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from era5_cache import B37, NLEV, NLAT, LEV37, blosc          # noqa: E402

CACHE = os.environ.get("ERA5_CACHE_DIR",
                        os.path.join(ROOT, "data", "era5_cache"))
PROF = os.path.join(CACHE, "height_profiles.npy")
MAN = os.path.join(CACHE, "height_manifest.csv")
LATFILE = os.path.join(CACHE, "lat.txt")

# the three zonal means we keep, in the order they are stored
DOMS = ["cap", "60", "0"]


def lat_axis():
    if os.path.exists(LATFILE):
        return np.loadtxt(LATFILE)
    lat = 90.0 - 0.25 * np.arange(NLAT)
    os.makedirs(CACHE, exist_ok=True)
    np.savetxt(LATFILE, lat, fmt="%.6f")
    return lat


def time_axis():
    """The ARCO time axis: 6-hourly steps from 1959-01-01."""
    parts = []
    for i in range(4):
        r = requests.get(f"{B37}/time/{i}", timeout=(20, 180))
        r.raise_for_status()
        parts.append(np.frombuffer(blosc().decode(r.content), dtype="<i8"))
    hrs = np.concatenate(parts)
    return np.array([np.datetime64("1959-01-01") + np.timedelta64(int(x), "h")
                     for x in hrs])


def chunk(tidx, tries=5):
    """One ARCO chunk -> (37, 721, 1440) float32, or None."""
    url = f"{B37}/geopotential/{tidx}.0.0.0"
    for k in range(tries):
        try:
            r = requests.get(url, timeout=(30, 600))
            if r.status_code != 200:
                return None
            return np.frombuffer(blosc().decode(r.content),
                                 dtype="<f4").reshape(NLEV, NLAT, 1440)
        except Exception:
            time.sleep(2.0 * (k + 1))
    return None


def zonal(c, lat):
    """The three zonal means of one chunk."""
    w = np.cos(np.deg2rad(lat))
    cap_sel = (lat >= 60.0) & (lat <= 90.0)
    nh_sel = lat >= 0.0
    out = np.empty((len(DOMS), NLEV), dtype="<f4")
    for j in range(NLEV):
        prof = c[j].mean(axis=1)                      # (721,) the zonal mean
        out[0, j] = np.average(prof[cap_sel], weights=w[cap_sel])
        i60 = int(np.abs(lat - 60.0).argmin())
        out[1, j] = prof[i60]
        out[2, j] = np.average(prof[nh_sel], weights=w[nh_sel])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="1990-2020")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--hour", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0, help="stop after N days")
    a = ap.parse_args()

    y0, y1 = (int(x) for x in a.years.split("-"))
    os.makedirs(CACHE, exist_ok=True)
    lat = lat_axis()
    t = time_axis()

    dates = []
    for y in range(y0, y1 + 1):
        d = pd.Timestamp(f"{y}-01-01")
        while d.year == y:
            dates.append(d)
            d += pd.Timedelta(days=1)

    man = (pd.read_csv(MAN) if os.path.exists(MAN)
           else pd.DataFrame(columns=["job", "date", "step"]))
    done = set(man["job"].tolist()) if len(man) else set()

    n = len(dates)
    want = (n, len(DOMS), NLEV)
    arr = None
    if os.path.exists(PROF):
        arr = np.lib.format.open_memmap(PROF, mode="r+")
        if arr.shape != want:
            # the store is sized by the year range - a partial run with a
            # different range leaves the wrong shape behind
            del arr
            os.remove(PROF)
            if os.path.exists(MAN):
                os.remove(MAN)
            arr = None
    if arr is None:
        arr = np.lib.format.open_memmap(PROF, mode="w+", dtype="<f4",
                                        shape=want)

    jobs = [i for i in range(n) if i not in done]
    if a.limit:
        jobs = jobs[:a.limit]
    print(f"height cache: {len(jobs)} days to fetch ({len(done)} done)",
          flush=True)
    if not jobs:
        return

    idx = {}
    for i in jobs:
        ts = dates[i] + pd.Timedelta(hours=a.hour)
        idx[i] = int(np.searchsorted(t, np.datetime64(ts, "ns")))

    t0 = time.time()
    rows = []

    def work(i):
        for off in range(0, 5):
            for o in ((off, -off) if off else (0,)):
                c = chunk(idx[i] + o)
                if c is None:
                    continue
                z = zonal(c, lat)
                del c
                return i, idx[i] + o, z
        return i, None, None

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for k, (i, sid, z) in enumerate(ex.map(work, jobs)):
            if z is None:
                print(f"  step {i} unavailable - skipped", flush=True)
                continue
            arr[i] = z
            rows.append((i, str(dates[i].date()), sid))
            if (k + 1) % 50 == 0:
                arr.flush()
                m = pd.concat([man, pd.DataFrame(rows, columns=["job", "date",
                                                               "step"])],
                              ignore_index=True)
                m.to_csv(MAN, index=False)
                rows = []
                el = time.time() - t0
                rate = (k + 1) / max(el, 1e-9)
                left = (len(jobs) - k - 1) / max(rate, 1e-9)
                print(f"  {k+1}/{len(jobs)}  {el/60:.1f} min  "
                      f"{rate*3600/1000:.1f} days/h  eta {left/60:.0f} min",
                      flush=True)
    arr.flush()
    del arr
    m = pd.concat([man, pd.DataFrame(rows, columns=["job", "date", "step"])],
                  ignore_index=True)
    m.to_csv(MAN, index=False)
    print(f"done -> {PROF}", flush=True)


if __name__ == "__main__":
    main()
