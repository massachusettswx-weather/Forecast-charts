#!/usr/bin/env python3
"""
AIFS ENS 18z 2026-10-25 -- zonal wind (u) at 100 hPa (10 mb).

Pulls every member of the ECMWF AIFS-ENS open-data ensemble (control + 50
perturbed = 51) for all 6-hourly forecast steps 0-360 h (day 0-15) and reduces
each field to:
    * area-weighted global mean zonal wind (m/s)
    * zonal-mean (latitudinal) profile of u (m/s)

Only the `u`/100 hPa GRIB message of each member is downloaded, using the byte
offsets published in the ECMWF .index files (HTTP range requests).

Source: https://data.ecmwf.int/forecasts/20261025/18z/aifs-ens/0p25/enfo/
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

RUN_DATE = os.environ.get("RUN_DATE", "20261025")
CYCLE = os.environ.get("CYCLE", "1800")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
BASE = (f"https://data.ecmwf.int/forecasts/{RUN_DATE}/{CYCLE[:2]}z"
        f"/aifs-ens/0p25/enfo")
RUN = f"{RUN_DATE}{CYCLE}00"
MAX_STEP = int(os.environ.get("MAX_STEP", "360"))
STEPS = list(range(0, MAX_STEP + 1, 6))
IDX_DIR = os.path.join(DATA, "idx")
PARAM = "u"
LEVEL = os.environ.get("LEVEL", "100")
OUT_NPZ = os.environ.get("OUT", os.path.join(DATA, f"aifs_u{LEVEL}.npz"))
WORKERS = int(os.environ.get("WORKERS", "12"))

os.makedirs(IDX_DIR, exist_ok=True)

_local = threading.local()


def session():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        ad = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0)
        s.mount("https://", ad)
        _local.s = s
    return s


def fetch(url, start=None, end=None, tries=6):
    hdr = {"Range": f"bytes={start}-{end}"} if start is not None else {}
    last = "?"
    for t in range(tries):
        try:
            r = session().get(url, headers=hdr, timeout=(15, 240))
            if r.status_code in (200, 206):
                return r.content
            last = f"http {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = repr(e)
        time.sleep(1.0 + 1.5 * t)
    raise RuntimeError(f"fetch failed {url} ({last})")


def index_rows(step, typ):
    """Return index rows for u @ 100 hPa from the .index file of one step/stream.

    Returns None when the archive has not published that step yet - the cycle is
    then published with the steps that ARE disseminated (ECMWF fills the rest in
    over the following hours).
    """
    p = f"{IDX_DIR}/{RUN}-{step}h-enfo-{typ}.index"
    if not os.path.exists(p):
        url = f"{BASE}/{RUN}-{step}h-enfo-{typ}.index"
        tmp = p + ".part"
        try:
            with open(tmp, "wb") as f:
                f.write(fetch(url))
        except Exception as e:
            print(f"  step {step}h {typ}: not published yet ({e})", flush=True)
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
        os.replace(tmp, p)
    rows = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("param") == PARAM and r.get("levelist") == LEVEL and r.get("levtype") == "pl":
                rows.append(r)
    return rows


def decode_u(blob):
    """Decode a single GRIB2 message -> (global area-weighted mean, zonal profile, lats)."""
    gid = eccodes.codes_new_from_message(blob)
    try:
        ni = int(eccodes.codes_get(gid, "Ni"))
        nj = int(eccodes.codes_get(gid, "Nj"))
        vals = np.asarray(eccodes.codes_get_array(gid, "values"), dtype=np.float64)
        lats = np.asarray(eccodes.codes_get_array(gid, "latitudes"), dtype=np.float64)
        lons = np.asarray(eccodes.codes_get_array(gid, "longitudes"), dtype=np.float64)
    finally:
        eccodes.codes_release(gid)
    vals = vals.reshape(nj, ni)
    lat1d = lats.reshape(nj, ni)[:, 0]
    lon1d = lons.reshape(nj, ni)[0, :]
    assert np.all(np.diff(lat1d) < 0), "unexpected latitude scan order"
    assert np.all(np.diff(lon1d) > 0), "unexpected longitude scan order"
    w = np.cos(np.deg2rad(lat1d))
    gmean = float((vals * w[:, None]).sum() / (ni * w.sum()))
    zonal = vals.mean(axis=1)
    return gmean, zonal, lat1d


def task(args):
    step, member, url, off, ln = args
    blob = fetch(url, off, off + ln - 1)
    gmean, zonal, lats = decode_u(blob)
    return step, member, gmean, zonal


def main():
    t0 = time.time()
    print("building task list ...", flush=True)
    tasks = []
    steps_ok = []
    for step in STEPS:
        rows_cf = index_rows(step, "cf")
        rows_pf = index_rows(step, "pf")
        if not rows_cf or not rows_pf:
            continue                      # step not disseminated yet
        steps_ok.append(step)
        r = rows_cf[0]
        url = f"{BASE}/{RUN}-{step}h-enfo-cf.grib2"
        tasks.append((step, 0, url, r["_offset"], r["_length"]))
        rows_pf = [r for r in rows_pf if "number" in r]
        url = f"{BASE}/{RUN}-{step}h-enfo-pf.grib2"
        for r in rows_pf:
            tasks.append((step, int(r["number"]), url, r["_offset"], r["_length"]))
    if not tasks:
        raise SystemExit(f"no AIFS ENS steps published for {RUN} yet")
    print(f"{len(tasks)} messages queued ({len(steps_ok)}/{len(STEPS)} steps disseminated)",
          flush=True)

    nstep, nmem = len(steps_ok), 51
    gmean = np.full((nstep, nmem), np.nan)
    zonal = np.full((nstep, nmem, 721), np.nan)
    lat1d = None
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for step, mem, gm, zo in ex.map(task, tasks):
            gmean[steps_ok.index(step), mem] = gm
            zonal[steps_ok.index(step), mem, :] = zo
            if lat1d is None:
                lat1d = zo  # placeholder, fixed below
            done += 1
            if done % 250 == 0:
                el = time.time() - t0
                print(f"  {done}/{len(tasks)}  {el:6.1f}s  eta {el/done*(len(tasks)-done):6.1f}s", flush=True)

    # latitudes from one authoritative decode
    row = index_rows(steps_ok[0], "cf")[0]
    blob = fetch(f"{BASE}/{RUN}-{steps_ok[0]}h-enfo-cf.grib2", row["_offset"],
                 row["_offset"] + row["_length"] - 1)
    _, _, lat1d = decode_u(blob)
    np.savez_compressed(
        OUT_NPZ,
        steps=np.array(steps_ok),
        members=np.arange(nmem),
        gmean=gmean,
        zonal=zonal,
        lats=lat1d,
    )
    print(f"saved {OUT_NPZ}  in {time.time()-t0:.1f}s", flush=True)
    print(f"global mean u@{LEVEL}hPa (ctl) at 0h, 360h:", gmean[0, 0], gmean[-1, 0], flush=True)
    print("ens mean at 360h:", np.nanmean(gmean[-1, 1:]), flush=True)


if __name__ == "__main__":
    sys.exit(main())
