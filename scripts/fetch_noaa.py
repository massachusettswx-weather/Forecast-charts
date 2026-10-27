#!/usr/bin/env python3
"""
Fetch u @ 10 hPa (and optionally any level) for NOAA GFS/GEFS ensemble products
straight from the NODD S3 buckets, using the .idx files to range-fetch ONLY the
message we need (a few hundred kB instead of the whole 10-40 MB file).

Products (12z cycle, 2026-10-26):
  GEFS mean     noaa-gefs-pds      gefs.YYMMDD/HH/atmos/pgrb2ap5/geavg.tHHz.pgrb2a.0p50.fFFF
  GEFS control  noaa-gefs-pds      gefs.YYMMDD/HH/atmos/pgrb2ap5/gec00.tHHz.pgrb2a.0p50.fFFF
  GFS  control  noaa-gfs-bdp-pds   gfs.YYMMDD/HH/atmos/gfs.tHHz.pgrb2.0p25.fFFF
  GFS  ens mean noaa-gfs-bdp-pds   gfs.YYMMDD/HH/atmos/gep_mean.tHHz.pgrb2.0p25.fFFF (if published)

Usage:
    python3 scripts/fetch_noaa.py --product gefs_mean  --date 20261026 --cyc 12
    python3 scripts/fetch_noaa.py --product gefs_ctl   --date 20261026 --cyc 12
    python3 scripts/fetch_noaa.py --product gfs_ctl    --date 20261026 --cyc 12
"""
import argparse
import io
import threading
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
import eccodes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
IDX = os.path.join(DATA, "idx")
os.makedirs(IDX, exist_ok=True)

PRODUCTS = {
    "gefs_members": ("noaa-gefs-pds", "gefs.{d}/{c}/atmos/pgrb2ap5/gep{MM:02d}.t{c}z.pgrb2a.0p50.f{step:03d}"),
    "gefs_mean": ("noaa-gefs-pds", "gefs.{d}/{c}/atmos/pgrb2ap5/geavg.t{c}z.pgrb2a.0p50.f{step:03d}"),
    "gefs_ctl":  ("noaa-gefs-pds", "gefs.{d}/{c}/atmos/pgrb2ap5/gec00.t{c}z.pgrb2a.0p50.f{step:03d}"),
    "gfs_ctl":   ("noaa-gfs-bdp-pds", "gfs.{d}/{c}/atmos/gfs.t{c}z.pgrb2.0p25.f{step:03d}"),
    "gfs_mean":  ("noaa-gfs-bdp-pds", "gfs.{d}/{c}/atmos/gep_mean.t{c}z.pgrb2.0p25.f{step:03d}"),
}


def s3(bucket, key):
    return f"https://{bucket}.s3.amazonaws.com/{key}"


_SESSION = None
_session_lock = threading.Lock()


def get(url, start=None, end=None, tries=4):
    global _SESSION
    hdr = {"Range": f"bytes={start}-{end}"} if start is not None else {}
    last = "?"
    with _session_lock:
        if _SESSION is None:
            _SESSION = requests.Session()
            ad = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4)
            _SESSION.mount("https://", ad)
    for t in range(tries):
        try:
            r = _SESSION.get(url, headers=hdr, timeout=(15, 240))
            if r.status_code in (200, 206):
                return r.content
            last = f"http {r.status_code}"
        except Exception as e:
            last = repr(e)
        time.sleep(1 + 1.5 * t)
    raise RuntimeError(f"{url} {last}")


def index_entry(bucket, key, cache, level=10):
    """Return (start, end) byte range of the UGRD message at `level` mb.
    NOAA .idx format: "msgno:start_byte:d=...:UGRD:10 mb:anl:..." where the
    byte field is the CUMULATIVE offset of each message start."""
    if not os.path.exists(cache) or os.path.getsize(cache) == 0:
        tmp = f"{cache}.{os.getpid()}.{threading.get_ident()}.part"
        try:
            with open(tmp, "wb") as f:
                f.write(get(s3(bucket, key) + ".idx"))
        except Exception as e:
            print(f"  step {key}: not published yet ({e})", flush=True)
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
        try:
            os.replace(tmp, cache)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
    lines = [l for l in open(cache, errors="ignore").read().split("\n") if l.strip()]
    want = f":UGRD:{level} mb:"
    for i, line in enumerate(lines):
        if want in line:
            start = int(line.split(":")[1])
            end = int(lines[i + 1].split(":")[1]) if i + 1 < len(lines) else None
            if end is None:
                sz = requests.head(s3(bucket, key), timeout=(15, 60)).headers.get("content-length")
                end = int(sz)
            return start, end - start
    raise RuntimeError(f"no UGRD:{level}mb message in {cache}")


def decode(blob, level=10):
    gid = eccodes.codes_new_from_message(blob)
    try:
        ni = int(eccodes.codes_get(gid, "Ni"))
        nj = int(eccodes.codes_get(gid, "Nj"))
        vals = np.asarray(eccodes.codes_get_array(gid, "values"), float).reshape(nj, ni)
        lat = np.asarray(eccodes.codes_get_array(gid, "latitudes"), float).reshape(nj, ni)[:, 0]
    finally:
        eccodes.codes_release(gid)
    z = vals.mean(axis=1)                     # zonal mean per latitude
    nh = lat >= 0
    w = np.cos(np.deg2rad(lat[nh]))
    nh_mean = float((z[nh] * w).sum() / w.sum())
    i60 = int(np.abs(lat - 60.0).argmin())
    return float(z[i60]), nh_mean, z, lat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True, choices=list(PRODUCTS))
    ap.add_argument("--date", default="20261026")
    ap.add_argument("--cyc", default="12")
    ap.add_argument("--maxstep", type=int, default=384)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    bucket, pat = PRODUCTS[a.product]
    cyc = a.cyc.zfill(2)
    steps = list(range(0, a.maxstep + 1, 6))
    out = a.out or os.path.join(DATA, f"{a.product}_{a.date}{cyc}.npz")

    def work(step):
        key = pat.format(d=a.date, c=cyc, step=step)
        cache = f"{IDX}/{a.product}_{a.date}{cyc}_{step}.idx"
        entry = index_entry(bucket, key, cache)
        if entry is None:
            return None                  # step not disseminated yet
        off, ln = entry
        blob = get(s3(bucket, key), off, off + ln - 1)
        u60, nhm, z, lat = decode(blob)
        return step, u60, nhm

    t0 = time.time()
    if a.product == "gefs_members":
        # 30 perturbed members per step; fetch step-by-step to keep RSS flat
        out_prof = {}

        def work_member(job):
            st, m = job
            key = pat.format(d=a.date, c=cyc, step=st, MM=m)
            cache = f"{IDX}/gefs_m{m:02d}_{a.date}{cyc}_{st}.idx"
            entry = index_entry(bucket, key, cache)
            if entry is None:
                return None
            off, ln = entry
            blob = get(s3(bucket, key), off, off + ln - 1)
            u60, nhm, z, lat = decode(blob)
            # the ZONAL PROFILE (u per latitude) - storing the latitudes here
            # silently flattened the GEFS member spaghetti
            return st, m, u60, nhm, z.astype(np.float32), lat.astype(np.float32)

        for si, st in enumerate(steps):
            with ThreadPoolExecutor(max_workers=4) as ex:
                for r in ex.map(work_member, [(st, m) for m in range(1, 31)]):
                    if r is None:
                        continue
                    st_, m, u60, nhm, z, lat = r
                    out_prof.setdefault(st_, {})[m] = (u60, nhm, z, lat)
            if (si + 1) % 10 == 0:
                print(f"  step {si+1}/{len(steps)} {time.time()-t0:.0f}s", flush=True)
        if not out_prof:
            raise SystemExit(f"no {a.product} steps published for {a.date} {cyc}z yet")
        steps_sorted = sorted(out_prof)
        prof = np.array([[out_prof[st][m][2] for m in range(1, 31)] for st in steps_sorted],
                        dtype=np.float32)      # (step, member, lat) - u per latitude
        u60 = np.array([[out_prof[st][m][0] for m in range(1, 31)] for st in steps_sorted])
        nh = np.array([[out_prof[st][m][1] for m in range(1, 31)] for st in steps_sorted])
        np.savez_compressed(out, steps=np.array(steps_sorted), u60=u60, nh_mean=nh,
                            zonal=prof, lats=out_prof[steps_sorted[0]][1][3])
        print(f"saved {out}  ({len(steps_sorted)} steps x 30 members, {time.time()-t0:.0f}s)", flush=True)
        print("  u60 F000 mean %.2f  F384 mean %.2f" % (u60[0].mean(), u60[-1].mean()), flush=True)
        return
    res, err = [], None
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(work, s): s for s in steps}
        for f in futs:
            pass
        for f, s in futs.items():
            try:
                r = f.result()
            except Exception as e:
                err = e
                break
            if r is not None:
                res.append(r)
    if err:
        print("FAILED:", err, flush=True)
        sys.exit(1)
    if not res:
        raise SystemExit(f"no {a.product} steps published for {a.date} {cyc}z yet")
    res.sort(key=lambda x: x[0])
    np.savez_compressed(out,
                        steps=np.array([r[0] for r in res]),
                        u60=np.array([r[1] for r in res]),
                        nh_mean=np.array([r[2] for r in res]))
    print(f"saved {out}  ({len(res)} steps, {time.time()-t0:.0f}s)", flush=True)
    print("  u60 F000 %.2f  F384 %.2f" % (res[0][1], res[-1][1]), flush=True)


if __name__ == "__main__":
    main()
