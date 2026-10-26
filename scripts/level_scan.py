#!/usr/bin/env python3
"""
Level scan: zonal-mean zonal wind at 60N from REAL archive data.

  * ECMWF IFS analysis (oper 0h, 2026-10-25 12Z) at every available pressure
    level -- includes the deep stratosphere (1 hPa, 0.1 hPa).
  * ECMWF AIFS ENS control at F000 and F360, every level the AIFS ENS
    product publishes (10 hPa is its top).

Answers "where does ~20-22 m/s at 60N actually live?" with archive data.
"""
import json
import os
import sys
import warnings

import numpy as np
import requests

warnings.filterwarnings("ignore")
sys.path.insert(0, "/workspace/scripts")
import eccodes

IDX_DIR = "/workspace/data/idx"
os.makedirs(IDX_DIR, exist_ok=True)

S = requests.Session()


def get(url, start=None, end=None, tries=5):
    hdr = {"Range": f"bytes={start}-{end}"} if start is not None else {}
    for t in range(tries):
        try:
            r = S.get(url, headers=hdr, timeout=(15, 180))
            if r.status_code in (200, 206):
                return r.content
        except Exception:
            pass
        import time
        time.sleep(1 + t)
    raise RuntimeError(url)


def index_rows(url, cache):
    if not os.path.exists(cache):
        with open(cache, "wb") as f:
            f.write(get(url))
    out = []
    with open(cache) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def zonal_at_lat(blob, lat_target=60.0):
    gid = eccodes.codes_new_from_message(blob)
    ni = int(eccodes.codes_get(gid, "Ni"))
    nj = int(eccodes.codes_get(gid, "Nj"))
    vals = np.asarray(eccodes.codes_get_array(gid, "values"), float).reshape(nj, ni)
    la = np.asarray(eccodes.codes_get_array(gid, "latitudes"), float).reshape(nj, ni)[:, 0]
    eccodes.codes_release(gid)
    i = int(np.abs(la - lat_target).argmin())
    z = vals.mean(axis=1)
    ww = np.cos(np.deg2rad(la[la >= 0]))
    nh = float((vals[la >= 0] * ww[:, None]).sum() / (ni * ww.sum()))
    return float(z[i]), float(nh), float(la[i])


def scan(url, grib_url, cache, label, lat_target=60.0):
    rows = index_rows(url, cache)
    out = []
    for r in rows:
        if r.get("param") != "u" or r.get("levtype") != "pl":
            continue
        off, ln = r["_offset"], r["_length"]
        blob = get(grib_url, off, off + ln - 1)
        u60, nh, lat = zonal_at_lat(blob, lat_target)
        out.append((int(r["levelist"]), u60, nh))
    out.sort(key=lambda x: -x[0])
    return out


print("=" * 74)
print("REAL ARCHIVE DATA — zonal-mean zonal wind at 60N")
print("=" * 74)

# ---- ECMWF IFS analysis (12Z 25 Oct 2026, step 0h) -----------------------
ifs_base = "https://data.ecmwf.int/forecasts/20261025/12z/ifs/0p25/oper/20261025120000-0h-oper-fc"
ifs_rows = scan(ifs_base + ".index", ifs_base + ".grib2",
                f"{IDX_DIR}/ifs_20261025120000-0h-oper-fc.index", "ifs")
print("\nECMWF IFS ANALYSIS (oper 0h, 2026-10-25 12Z)")
print(f"  {'level(hPa)':>12s} {'u60N (m/s)':>12s} {'NH mean':>10s}")
for lev, u60, nh in ifs_rows:
    print(f"  {lev:>12d} {u60:>12.2f} {nh:>10.2f}")

# ---- AIFS ENS control: F000 and F360 ------------------------------------
aifs_base = "https://data.ecmwf.int/forecasts/20261025/18z/aifs-ens/0p25/enfo/20261025180000-{s}h-enfo-cf"
res = {}
for step in (0, 360):
    base = aifs_base.format(s=step)
    rows = scan(base + ".index", base + ".grib2",
                f"{IDX_DIR}/aifs_ens_cf_{step}h.index", "aifs")
    res[step] = {lev: (u60, nh) for lev, u60, nh in rows}

print("\nECMWF AIFS ENS CONTROL (run 20261025180000)")
print(f"  {'level(hPa)':>12s} {'u60N F000':>11s} {'u60N F360':>11s} {'change':>9s} {'NH F000':>9s} {'NH F360':>9s}")
for lev in sorted(res[0], reverse=True):
    a = res[0][lev][0]
    b = res[360][lev][0]
    na, nb = res[0][lev][1], res[360][lev][1]
    print(f"  {lev:>12d} {a:>11.2f} {b:>11.2f} {b-a:>9.2f} {na:>9.2f} {nb:>9.2f}")

np.savez("/workspace/data/level_scan.npz",
         ifs_levels=np.array([r[0] for r in ifs_rows]),
         ifs_u60=np.array([r[1] for r in ifs_rows]),
         ifs_nh=np.array([r[2] for r in ifs_rows]),
         aifs_levels=np.array(sorted(res[0], reverse=True)),
         aifs_u60_f000=np.array([res[0][l][0] for l in sorted(res[0], reverse=True)]),
         aifs_u60_f360=np.array([res[360][l][0] for l in sorted(res[0], reverse=True)]),
         aifs_nh_f000=np.array([res[0][l][1] for l in sorted(res[0], reverse=True)]),
         aifs_nh_f360=np.array([res[360][l][1] for l in sorted(res[0], reverse=True)]))
print("\nsaved /workspace/data/level_scan.npz")
