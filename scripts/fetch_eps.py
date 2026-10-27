#!/usr/bin/env python3
"""
ECMWF operational ensemble (EPS) + IFS control, u @ 10 hPa, for one cycle.

  EPS perturbed members (50) : .../ifs/0p25/enfo/{RUN}-{step}h-enfo-ef.grib2
  IFS control                : .../ifs/0p25/oper/{RUN}-{step}h-oper-fc.grib2

Uses the published .index files + HTTP Range requests, so only the u@10 hPa
message is downloaded for each member (the enfo files are ~1.5 GB each).

    RUN_DATE=20261026 CYCLE=1200 LEVEL=10 python3 scripts/fetch_eps.py
"""
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
import eccodes

RUN_DATE = os.environ.get("RUN_DATE", "20261026")
CYCLE = os.environ.get("CYCLE", "1200").zfill(4)
LEVEL = os.environ.get("LEVEL", "10")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
BASE = f"https://data.ecmwf.int/forecasts/{RUN_DATE}/{CYCLE[:2]}z/ifs/0p25"
RUN = f"{RUN_DATE}{CYCLE}00"
IDX = os.path.join(DATA, "idx")
os.makedirs(IDX, exist_ok=True)
_local = threading.local()


def session():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4))
        _local.s = s
    return s


def get(url, start=None, end=None, tries=5):
    hdr = {"Range": f"bytes={start}-{end}"} if start is not None else {}
    last = "?"
    for t in range(tries):
        try:
            r = session().get(url, headers=hdr, timeout=(15, 240))
            if r.status_code in (200, 206):
                return r.content
            last = f"http {r.status_code}"
        except Exception as e:
            last = repr(e)
        time.sleep(1 + 1.5 * t)
    raise RuntimeError(f"{url} {last}")


def index_rows(url, cache):
    """Rows of one step's index file; None when the archive has not published
    that step yet (the cycle is then published with the steps that ARE out)."""
    if not os.path.exists(cache) or os.path.getsize(cache) == 0:
        tmp = cache + ".part"
        try:
            with open(tmp, "wb") as f:
                f.write(get(url))
        except Exception as e:
            print(f"  step not published yet: {url.split('/')[-1]} ({e})", flush=True)
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
        os.replace(tmp, cache)
    rows = []
    with open(cache) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def decode(blob):
    gid = eccodes.codes_new_from_message(blob)
    try:
        ni = int(eccodes.codes_get(gid, "Ni"))
        nj = int(eccodes.codes_get(gid, "Nj"))
        vals = np.asarray(eccodes.codes_get_array(gid, "values"), float).reshape(nj, ni)
        lat = np.asarray(eccodes.codes_get_array(gid, "latitudes"), float).reshape(nj, ni)[:, 0]
    finally:
        eccodes.codes_release(gid)
    z = vals.mean(axis=1)
    nh = lat >= 0
    w = np.cos(np.deg2rad(lat[nh]))
    return (float(z[int(np.abs(lat - 60.0).argmin())]),
            float((z[nh] * w).sum() / w.sum()),
            z.astype(np.float32), lat.astype(np.float32))


MAX_STEP = int(os.environ.get("MAX_STEP", "360"))
STEPS = list(range(0, MAX_STEP + 1, 6))


def work_ef(step):
    """50 EPS perturbed members -> (step, mean_u60, mean_nh, n_members)"""
    url = f"{BASE}/enfo/{RUN}-{step}h-enfo-ef.grib2"
    cache = f"{IDX}/eps_ef_{RUN}_{step}h.index"
    rows = index_rows(url.replace(".grib2", ".index"), cache)
    if rows is None:
        return None
    rows = [r for r in rows
            if r.get("param") == "u" and r.get("levtype") == "pl"
            and str(r.get("levelist")) == LEVEL and r.get("type") == "pf"]
    if not rows:
        return None
    u60s, nhs, profs = [], [], []
    for r in rows:
        blob = get(url, r["_offset"], r["_offset"] + r["_length"] - 1)
        a, b, prof, lat = decode(blob)
        u60s.append(a); nhs.append(b); profs.append(prof)
    return (step, float(np.mean(u60s)), float(np.mean(nhs)), len(u60s),
            np.array(profs, dtype=np.float32), lat)


def work_oper(step):
    """IFS control (= EPS control member)"""
    url = f"{BASE}/oper/{RUN}-{step}h-oper-fc.grib2"
    cache = f"{IDX}/ifs_oper_{RUN}_{step}h.index"
    rows = index_rows(url.replace(".grib2", ".index"), cache)
    if rows is None:
        return None
    rows = [r for r in rows
            if r.get("param") == "u" and r.get("levtype") == "pl"
            and str(r.get("levelist")) == LEVEL]
    if not rows:
        return None
    r = rows[0]
    blob = get(url, r["_offset"], r["_offset"] + r["_length"] - 1)
    a, b, prof, lat = decode(blob)
    return step, a, b


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="both", choices=["both", "control", "eps"])
    args = ap.parse_args()
    t0 = time.time()
    out_eps = os.path.join(DATA, f"eps_{RUN_DATE}{CYCLE}_u{LEVEL}.npz")
    out_ctl = os.path.join(DATA, f"ifs_ctl_{RUN_DATE}{CYCLE}_u{LEVEL}.npz")

    ctl = []
    if args.mode in ("both", "control"):
        with ThreadPoolExecutor(max_workers=8) as ex:
            for r in ex.map(work_oper, STEPS):
                if r is not None:
                    ctl.append(r)
    if ctl:
        ctl.sort()
        np.savez_compressed(out_ctl, steps=np.array([r[0] for r in ctl]),
                            u60=np.array([r[1] for r in ctl]), nh_mean=np.array([r[2] for r in ctl]))
        print(f"IFS control saved {out_ctl} ({time.time()-t0:.0f}s)", flush=True)

    eps = []
    if args.mode in ("both", "eps"):
      with ThreadPoolExecutor(max_workers=6) as ex:
        for k, r in enumerate(ex.map(work_ef, STEPS)):
            if r is not None:
                eps.append(r)
            if (k + 1) % 10 == 0:
                print(f"  EPS {k+1}/{len(STEPS)} {time.time()-t0:.0f}s", flush=True)
    eps.sort()
    if eps:
        np.savez_compressed(out_eps, steps=np.array([r[0] for r in eps]),
                            u60=np.array([r[1] for r in eps]), nh_mean=np.array([r[2] for r in eps]),
                            nmembers=np.array([r[3] for r in eps]),
                            zonal=np.array([r[4] for r in eps]), lats=eps[0][5])
        print(f"EPS mean saved {out_eps} ({time.time()-t0:.0f}s)", flush=True)
        print("  EPS mean u60 F000 %.2f  F360 %.2f | members %d"
              % (eps[0][1], eps[-1][1], eps[0][3]), flush=True)
    if ctl:
        print("  IFS ctl  u60 F000 %.2f  F360 %.2f" % (ctl[0][1], ctl[-1][1]), flush=True)
