#!/usr/bin/env python3
"""AIFS Single (deterministic) 18z 2026-10-25, u @ 10 hPa, all 6-hourly steps."""
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

RUN_DATE = os.environ.get("RUN_DATE", "20261025")
CYCLE = os.environ.get("CYCLE", "1800")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
BASE = (f"https://data.ecmwf.int/forecasts/{RUN_DATE}/{CYCLE[:2]}z"
        f"/aifs-single/0p25/oper")
RUN = f"{RUN_DATE}{CYCLE}00"
LEVEL = os.environ.get("LEVEL", "10")
OUT = os.environ.get("OUT", os.path.join(DATA, f"aifs_single_u{LEVEL}.npz"))
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


def fetch(url, start=None, end=None, tries=5):
    hdr = {"Range": f"bytes={start}-{end}"} if start is not None else {}
    last = "?"
    for t in range(tries):
        try:
            r = session().get(url, headers=hdr, timeout=(15, 180))
            if r.status_code in (200, 206):
                return r.content
            last = f"http {r.status_code}"
        except Exception as e:
            last = repr(e)
        time.sleep(1 + 1.5 * t)
    raise RuntimeError(f"{url} {last}")


def index_rows(step):
    p = f"{IDX}/aifs_single_{RUN}_{step}h.index"   # run-scoped: offsets differ per run
    if not os.path.exists(p) or os.path.getsize(p) == 0:
        tmp = p + ".part"
        with open(tmp, "wb") as f:
            f.write(fetch(f"{BASE}/{RUN}-{step}h-oper-fc.index"))
        os.replace(tmp, p)
    rows = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return [r for r in rows if r.get("param") == "u" and r.get("levelist") == LEVEL
            and r.get("levtype") == "pl"]


def decode(blob):
    gid = eccodes.codes_new_from_message(blob)
    ni = int(eccodes.codes_get(gid, "Ni"))
    nj = int(eccodes.codes_get(gid, "Nj"))
    vals = np.asarray(eccodes.codes_get_array(gid, "values"), float).reshape(nj, ni)
    lat = np.asarray(eccodes.codes_get_array(gid, "latitudes"), float).reshape(nj, ni)[:, 0]
    eccodes.codes_release(gid)
    return vals, lat


STEPS = list(range(0, 361, 6))


def work(step):
    rows = index_rows(step)
    r = rows[0]
    blob = fetch(f"{BASE}/{RUN}-{step}h-oper-fc.grib2", r["_offset"], r["_offset"] + r["_length"] - 1)
    vals, lat = decode(blob)
    z = vals.mean(axis=1)
    i60 = int(np.abs(lat - 60.0).argmin())
    nh = lat >= 0
    w = np.cos(np.deg2rad(lat[nh]))
    nhm = float((vals[nh] * w[:, None]).sum() / (vals.shape[1] * w.sum()))
    return step, float(z[i60]), nhm, z.astype(np.float32), lat


res = []
t0 = time.time()
with ThreadPoolExecutor(max_workers=10) as ex:
    for k, out in enumerate(ex.map(work, STEPS)):
        res.append(out)
        if (k + 1) % 15 == 0:
            print(f"  {k+1}/{len(STEPS)} {time.time()-t0:.0f}s", flush=True)

res.sort(key=lambda x: x[0])
steps = np.array([r[0] for r in res])
u60 = np.array([r[1] for r in res])
nhm = np.array([r[2] for r in res])
zonal = np.array([r[3] for r in res])
lats = res[0][4]
np.savez_compressed(OUT, steps=steps, u60=u60, nh_mean=nhm, zonal=zonal, lats=lats)
print("saved", OUT)
print("AIFS Single u@10hPa 60N: F000 %.2f | F360 %.2f | NH F000 %.2f F360 %.2f"
      % (u60[0], u60[-1], nhm[0], nhm[-1]))
