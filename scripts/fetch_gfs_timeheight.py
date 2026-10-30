#!/usr/bin/env python3
"""
Fetch the GFS zonal-mean wind at EVERY level, for a whole forecast.

This is the model side of the stratosphere time-height: the polar vortex is
a vertical structure, so a single level is not enough - the reversal has to
be seen descending through the stratosphere.

The NOAA .idx files give the byte range of every message, so only the
messages we want are downloaded (the .idx lookup is what makes this cheap:
a 16-day, 15-level time-height is 0.6 GB, not the 27 GB the whole file
would be).

    python3 scripts/fetch_gfs_timeheight.py --date 20261026 --cyc 12 \
        --out data/store/series/gfs_th_2026102612.npz
"""
import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_noaa import PRODUCTS, s3, get, DATA      # noqa: E402

IDX = os.path.join(DATA, "idx")

# the levels a stratosphere cross-section shows (hPa)
PLEV = [1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 150, 200, 250, 300]


def _idx_ranges(bucket, key, level_mb, cache_dir):
    """(start, length) of the UGRD message at `level_mb` in this file."""
    cache = os.path.join(cache_dir, f"{key.replace('/', '_')}.idx")
    if not os.path.exists(cache) or os.path.getsize(cache) == 0:
        tmp = f"{cache}.{threading.get_ident()}.part"
        try:
            with open(tmp, "wb") as fh:
                fh.write(get(s3(bucket, key) + ".idx"))
            os.replace(tmp, cache)
        except Exception as exc:
            print(f"  {key}: index unavailable ({exc})", flush=True)
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
    lines = [l for l in open(cache, errors="ignore").read().split("\n")
             if l.strip()]
    want = f":UGRD:{level_mb} mb:"
    for i, line in enumerate(lines):
        if want in line:
            start = int(line.split(":")[1])
            end = (int(lines[i + 1].split(":")[1])
                   if i + 1 < len(lines) else None)
            if end is None:
                return None
            return start, end - start
    return None


def _zonal_mean(blob):
    """The zonal-mean u at every latitude, and the latitude axis."""
    import eccodes
    gid = eccodes.codes_new_from_message(blob)
    try:
        ni = int(eccodes.codes_get(gid, "Ni"))
        nj = int(eccodes.codes_get(gid, "Nj"))
        vals = np.asarray(eccodes.codes_get_array(gid, "values"),
                          float).reshape(nj, ni)
        lat = np.asarray(eccodes.codes_get_array(gid, "latitudes"),
                         float).reshape(nj, ni)[:, 0]
    finally:
        eccodes.codes_release(gid)
    return vals.mean(axis=1), lat


def fetch(product, date, cyc, maxstep, levels, workers, out):
    bucket, pat = PRODUCTS[product]
    cyc = cyc.zfill(2)
    steps = list(range(0, maxstep + 1, 6))
    os.makedirs(IDX, exist_ok=True)
    jobs = []
    for step in steps:
        key = pat.format(d=date, c=cyc, step=step)
        for lev in levels:
            jobs.append((step, lev, key))

    # the latitude axis is the same for every message
    lat = None
    prof = {}          # (step, level) -> zonal mean array

    def work(job):
        step, lev, key = job
        r = _idx_ranges(bucket, key, lev, IDX)
        if r is None:
            return None
        off, ln = r
        try:
            blob = get(s3(bucket, key), off, off + ln - 1)
        except Exception:
            return None
        try:
            z, la = _zonal_mean(blob)
        except Exception:
            return None
        return step, lev, z, la

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(work, jobs):
            done += 1
            if res is None:
                continue
            step, lev, z, la = res
            if lat is None:
                lat = la
            prof[(step, lev)] = z
            if done % 50 == 0:
                el = time.time() - t0
                print(f"  {done}/{len(jobs)} {el:.0f}s "
                      f"eta {el/done*(len(jobs)-done):.0f}s", flush=True)

    if lat is None:
        raise RuntimeError("nothing fetched - is the cycle published?")
    # the zonal mean on the GFS grid: one value per latitude
    out_prof = {}
    for lev in levels:
        rows = []
        for step in steps:
            z = prof.get((step, lev))
            rows.append(z if z is not None else np.full(len(lat), np.nan))
        out_prof[f"u_{lev}"] = np.array(rows, dtype=np.float32)  # (steps, lat)
    # the forecast time of each step, as an ISO string (the renderers work
    # in hours from the initialisation, like every other series here)
    init = pd.Timestamp(f"{date}-{cyc}")
    times = np.array([(init + pd.Timedelta(hours=int(s))).strftime("%Y-%m-%d %H:%M")
                       for s in steps])
    np.savez_compressed(out, steps=np.array(steps), times=times,
                        levels=np.array(levels),
                        lat=lat.astype(np.float32), **out_prof)
    print(f"wrote {out} ({os.path.getsize(out)//1024} KB) "
          f"in {time.time()-t0:.0f}s", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default="gfs_ctl", choices=list(PRODUCTS))
    ap.add_argument("--date", required=True)
    ap.add_argument("--cyc", default="12")
    ap.add_argument("--maxstep", type=int, default=384)
    ap.add_argument("--levels", default=",".join(str(p) for p in PLEV))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    levels = [int(x) for x in a.levels.split(",") if x.strip()]
    out = a.out or os.path.join(DATA, "series",
                                f"gfs_th_{a.date}{a.cyc.zfill(2)}.npz")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fetch(a.product, a.date, a.cyc, a.maxstep, levels, a.workers, out)


if __name__ == "__main__":
    main()
