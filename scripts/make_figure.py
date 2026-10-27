#!/usr/bin/env python3
"""
One-command builder for the AIFS ENS / AIFS Single "Zonal Mean Zonal Wind"
figure at 10 hPa (or any level ECMWF publishes).

    python3 scripts/make_figure.py --run 20261025 --time 1800 --level 10 --lat 60

What it does
  1. pulls the raw ECMWF open-data GRIB2 for every AIFS ENS member
     (control + 50 perturbed) and the AIFS Single, at the requested level,
     for all 6-hourly steps of the run;
  2. reduces each field to the zonal-mean u profile and the area-weighted
     NH mean (no synthesis, no interpolation - the archive values);
  3. pulls the ERA5 1991-2020 climatology from the LOCAL cache
     (data/era5_cache), extending the cache automatically if the requested
     window is not there yet;
  4. renders the single-panel figure in the house style and writes the
     underlying 6-hourly series to CSV.

Everything is cached on disk, so a repeat run of the same cycle is fast.
"""
import argparse
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FixedLocator, MultipleLocator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import era5_cache as EC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))

OUT_DIR = os.path.join(ROOT, "output")
IDX_DIR = os.path.join(ROOT, "data", "idx")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(IDX_DIR, exist_ok=True)

C_MEM, C_MEAN, C_CTL, C_CLIM, C_SINGLE = "#1f4fd8", "#ff6b6b", "#8b1a1a", "#000000", "#6a0dad"
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
            r = session().get(url, headers=hdr, timeout=(15, 180))
            if r.status_code in (200, 206):
                return r.content
            last = f"http {r.status_code}"
        except Exception as e:
            last = repr(e)
        time.sleep(1 + 1.5 * t)
    raise RuntimeError(f"{url} {last}")


def index_rows(url, cache):
    if not os.path.exists(cache) or os.path.getsize(cache) == 0:
        tmp = cache + ".part"
        with open(tmp, "wb") as f:
            f.write(get(url))
        os.replace(tmp, cache)
    rows = []
    with open(cache) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def decode(blob):
    import eccodes
    gid = eccodes.codes_new_from_message(blob)
    try:
        ni = int(eccodes.codes_get(gid, "Ni"))
        nj = int(eccodes.codes_get(gid, "Nj"))
        vals = np.asarray(eccodes.codes_get_array(gid, "values"), float).reshape(nj, ni)
        lat = np.asarray(eccodes.codes_get_array(gid, "latitudes"), float).reshape(nj, ni)[:, 0]
    finally:
        eccodes.codes_release(gid)
    return vals, lat


def profile(vals, lat):
    """Zonal mean per latitude + area-weighted NH mean (cos-lat weights)."""
    z = vals.mean(axis=1)
    nh = lat >= 0
    w = np.cos(np.deg2rad(lat[nh]))
    nh_mean = float((z[nh] * w).sum() / w.sum())
    return z, nh_mean


def collect_tasks(base, run, steps, pattern, level, cache_tag):
    """Build (step, member, url, offset, length) for every message we need."""
    tasks = []
    for st in steps:
        idx = f"{base}/{run}-{st}h-{pattern}.index"
        grib = f"{base}/{run}-{st}h-{pattern}.grib2"
        rows = [r for r in index_rows(idx, f"{IDX_DIR}/{cache_tag}_{st}h.index")
                if r.get("param") == "u" and r.get("levtype") == "pl"
                and str(r.get("levelist")) == str(level)]
        for r in rows:
            mid = 0 if ("number" not in r or pattern.endswith("cf")) else int(r["number"])
            tasks.append((st, mid, grib, r["_offset"], r["_length"]))
    return tasks


def fetch_one(task):
    st, mid, url, off, ln = task
    vals, lat = decode(get(url, off, off + ln - 1))
    z, nhm = profile(vals, lat)
    return st, mid, z, nhm, lat


def run_tasks(tasks, workers=6):
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(fetch_one, tasks):
            out.append(r)
    return out



def _save_cached(ens, sing, lats, steps, ens_npz, sing_npz):
    """Persist the zonal profiles so the next run of this cycle is instant."""
    nsteps = len(steps)
    nmem = max(len(v) for v in ens.values())
    z = np.full((nsteps, nmem, len(lats)), np.nan, dtype=np.float32)
    for i, st in enumerate(steps):
        for m, (prof, _) in ens[st].items():
            z[i, m] = prof
    np.savez_compressed(ens_npz, steps=np.array(steps), zonal=z, lats=lats)
    zs = np.array([sing[st][0] for st in steps], dtype=np.float32)
    np.savez_compressed(sing_npz, steps=np.array(steps), zonal=zs, lats=lats)
    print(f"  cached AIFS profiles -> {ens_npz}, {sing_npz}", flush=True)


def build_figure(run, cyc, level, lat_target, max_step=360, workers=6, domain_label=None,
                 out_png=None, out_csv=None, title_domain=None):
    t0 = time.time()
    ens_base = (f"https://data.ecmwf.int/forecasts/{run}/{cyc[:2]}z/aifs-ens/0p25/enfo")
    sing_base = f"https://data.ecmwf.int/forecasts/{run}/{cyc[:2]}z/aifs-single/0p25/oper"
    runid = f"{run}{cyc}00"          # ECMWF file prefix: YYYYMMDDHHMM00

    steps = list(range(0, max_step + 1, 6))
    ens_npz = os.path.join(DATA, f"aifs_{run}{cyc}_u{level}.npz")
    sing_npz = os.path.join(DATA, f"aifs_single_{run}{cyc}_u{level}.npz")
    cached = os.path.exists(ens_npz) and os.path.exists(sing_npz)
    if cached:
        e = np.load(ens_npz, allow_pickle=True)
        sg = np.load(sing_npz, allow_pickle=True)
        lat_ref = e["lats"]
        zall = e["zonal"]                       # decompress once, not per member
        zsing = sg["zonal"]
        ens = {}
        for i, st in enumerate(e["steps"]):
            ens[int(st)] = {m: (zall[i, m], None) for m in range(zall.shape[1])}
        sing = {int(st): (zsing[i], None) for i, st in enumerate(sg["steps"])}
        print(f"  AIFS ENS + Single: from local cache ({len(ens)} steps) "
              f"({time.time()-t0:.0f}s)", flush=True)
    else:
        tasks = (collect_tasks(ens_base, runid, steps, "enfo-cf", level, f"aifsens_{runid}_cf")
                 + collect_tasks(ens_base, runid, steps, "enfo-pf", level, f"aifsens_{runid}_pf"))
        ens = {st: {} for st in steps}
        lat_ref = None
        for st, mid, z, nhm, lat in run_tasks(tasks):
            ens[st][mid] = (z, nhm)
            lat_ref = lat
        print(f"  AIFS ENS: {len(ens)} steps x {len(ens[0])} members "
              f"({time.time()-t0:.0f}s)", flush=True)

        sing = {}
        for st, mid, z, nhm, lat in run_tasks(collect_tasks(sing_base, runid, steps, "oper-fc",
                                                            level, f"aifssing_{runid}")):
            sing[st] = (z, nhm)
        print(f"  AIFS Single: {len(sing)} steps ({time.time()-t0:.0f}s)", flush=True)
        _save_cached(ens, sing, lat_ref, steps, ens_npz, sing_npz)

    # ---------------- ERA5 climatology (local cache) ----------------------
    valid = pd.Timestamp(f"{run[:4]}-{run[4:6]}-{run[6:8]} {cyc[:2]}:{cyc[2:]}:00") \
        + pd.to_timedelta(steps, unit="h")
    start_md = f"{valid[0].month:02d}-{valid[0].day:02d}"
    end_md = f"{valid[-1].month:02d}-{valid[-1].day:02d}"
    try:
        mean, std, cnt = EC.climatology(level, lat=lat_target, start=start_md, end=end_md)
    except Exception:
        print(f"  ERA5 cache miss for {start_md}-{end_md} -> building ...", flush=True)
        EC.build(start_md, end_md)
        mean, std, cnt = EC.climatology(level, lat=lat_target, start=start_md, end=end_md)

    def pick(series):
        out = []
        for v in valid:
            k = f"{v.month}-{v.day}-12"
            out.append(float(series.get(k, np.nan)))
        return np.array(out)

    clim, csd = pick(mean), pick(std)
    print(f"  ERA5 climatology from local cache ({start_md}-{end_md})", flush=True)

    # ---------------- series ----------------------------------------------
    i_lat = int(np.abs(lat_ref - lat_target).argmin())
    def nh_of(prof):
        nh = lat_ref >= 0
        w = np.cos(np.deg2rad(lat_ref[nh]))
        return float((prof[nh] * w).sum() / w.sum())
    members = sorted(ens[0].keys())
    ctl_id = 0
    pert = [m for m in members if m != ctl_id]
    u_ctl = np.array([ens[s][ctl_id][0][i_lat] for s in steps])
    u_pert = np.array([[ens[s][m][0][i_lat] for m in pert] for s in steps])
    u_mean = u_pert.mean(axis=1)
    u_sing = np.array([sing[s][0][i_lat] for s in steps])
    nh_ctl = np.array([ens[s][ctl_id][1] if ens[s][ctl_id][1] is not None else nh_of(ens[s][ctl_id][0])
                       for s in steps])
    nh_mean = np.array([np.mean([ens[s][m][1] if ens[s][m][1] is not None else nh_of(ens[s][m][0])
                                 for m in pert]) for s in steps])

    # ---------------- plot -------------------------------------------------
    STEP = 2                       # 12-hourly frames
    v = valid[::STEP]
    fig, ax = plt.subplots(figsize=(11.5, 7.2))
    for i in range(u_pert.shape[1]):
        ax.plot(v, u_pert[::STEP, i], color=C_MEM, lw=1.15, alpha=0.50, zorder=2)
    ax.fill_between(v, clim[::STEP] - 2 * csd[::STEP], clim[::STEP] + 2 * csd[::STEP],
                    color="#d9dce0", alpha=0.75, lw=0, zorder=1)
    ax.fill_between(v, clim[::STEP] - csd[::STEP], clim[::STEP] + csd[::STEP],
                    color="#b4b9be", alpha=0.75, lw=0, zorder=1)
    ax.plot(v, clim[::STEP], color=C_CLIM, lw=2.6, zorder=6, label="ERA5 1991-2020 climatology")
    ax.plot(v, u_ctl[::STEP], color=C_CTL, lw=3.4, marker="o", markersize=5.0,
            markerfacecolor=C_CTL, markeredgecolor="#4a0d0d", markeredgewidth=0.9,
            zorder=7, label="AIFS ENS control (cf)")
    ax.plot(v, u_mean[::STEP], color=C_MEAN, lw=3.0, marker="o", markersize=5.0,
            markerfacecolor=C_MEAN, markeredgecolor="#8b1a1a", markeredgewidth=0.9,
            zorder=8, label="AIFS ENS ensemble mean (51)")
    ax.plot(v, u_sing[::STEP], color=C_SINGLE, lw=2.2, marker="o", markersize=5.0,
            markerfacecolor=C_SINGLE, markeredgecolor="#3d0864", markeredgewidth=0.9,
            zorder=9, label="AIFS Single (oper)")
    ax.axhline(0.0, color="#000000", lw=2.0, zorder=5)

    lo = min(float(np.nanmin(u_pert)), float(np.nanmin(clim - 2 * csd)), 0.0)
    hi = max(float(np.nanmax(u_pert)), float(np.nanmax(clim + 2 * csd)))
    ax.set_ylim(lo - 0.06 * (hi - lo), hi + 0.06 * (hi - lo))
    ax.set_xlim(valid[0].normalize(), valid[-1] + pd.Timedelta(hours=6))
    ax.set_ylabel("Zonal Wind (m/s)")
    ax.yaxis.set_major_locator(MultipleLocator(5))
    ax.grid(True, alpha=0.35, lw=0.7)
    ax.set_axisbelow(True)
    ax.legend(loc="upper left", frameon=True, framealpha=0.95, fontsize=10.5)
    tick_days = pd.date_range(valid[0].normalize(), valid[-1], freq="2D")
    ax.xaxis.set_major_locator(FixedLocator(mdates.date2num(tick_days)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
    ax.xaxis.set_minor_locator(mdates.DayLocator(interval=1))
    ax.tick_params(axis="x", labelrotation=0)

    dom = title_domain or (f"{lat_target:.0f}˚N" if lat_target else "NH (0-90N)")
    title = f"ECMWF AIFS ENS 0.25°  + AIFS Single  {dom} Zonal Mean Zonal Wind"
    t = fig.suptitle(title, x=0.075, ha="left", fontsize=16.5, fontweight="bold", y=0.972)
    fig.canvas.draw()
    w = t.get_window_extent(fig.canvas.get_renderer()).width
    avail = fig.get_figwidth() * fig.dpi * 0.9
    fig.text(0.075, 0.933,
             f"{cyc[:2]}z {pd.Timestamp(f'{run[:4]}-{run[4:6]}-{run[6:8]}'):%b %d}, {run[:4]}"
             f"   •   ERA5 1991–2020 climatology",
             ha="left", va="top", fontsize=10.5, color="#333333")
    fig.text(0.5, 0.018, "Valid Days", ha="center", va="bottom", fontsize=12.5, color="#111111")
    fig.text(0.975, 0.016, "@Massachusettswx", ha="right", va="bottom",
             fontsize=12.5, fontweight="bold", color="#111111")
    scale = min(avail / w, 1.30)
    if abs(scale - 1.0) > 0.01:
        t.set_fontsize(16.5 * scale)
    fig.subplots_adjust(left=0.085, right=0.975, top=0.885, bottom=0.115)
    dom_tag = f"{int(lat_target)}N" if lat_target else "NHmean"
    out_png = out_png or f"{OUT_DIR}/AIFS_ENS_{run}_{cyc}Z_u{level}hPa_{dom_tag}.png"
    fig.savefig(out_png, dpi=200)
    fig.savefig(out_png.replace(".png", ".pdf"))
    plt.close(fig)
    print("wrote", out_png)

    out_csv = out_csv or out_png.replace(".png", "_timeseries.csv")
    pd.DataFrame({
        "valid_time": valid, "lead_days": np.array(steps) / 24.0,
        "u_ctl": u_ctl, "u_ens_mean": u_mean, "u_spread": u_pert.std(axis=1),
        "u_single": u_sing, "nh_ctl": nh_ctl, "nh_ens_mean": nh_mean,
        "era5_clim": clim, "era5_sd": csd,
    }).to_csv(out_csv, index=False)
    print("wrote", out_csv)
    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="20261025", help="run date YYYYMMDD")
    ap.add_argument("--cyc", default="1800", help="cycle HHMM")
    ap.add_argument("--level", type=int, default=10, help="pressure level hPa")
    ap.add_argument("--lat", type=float, default=60.0, help="latitude (0 = NH mean)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-step", type=int, default=360)
    a = ap.parse_args()
    build_figure(a.run, a.cyc, a.level, a.lat, max_step=a.max_step, out_png=a.out)
