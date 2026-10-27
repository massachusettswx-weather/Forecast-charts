#!/usr/bin/env python3
"""
Multi-model 60N zonal-mean zonal wind at 10 hPa (1 mb), 12z cycle.

  ECMWF AIFS ENS   mean + control        (15 d)
  ECMWF AIFS Single                       (15 d)
  NOAA GEFS        mean + control        (16 d)
  NOAA GFS         deterministic         (16 d)
  ERA5 1991-2020 climatology (+/-1,2 sigma)

Usage:
    python3 scripts/make_multimodel.py --date 20261026 --cyc 1200 --level 10 --lat 60
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
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
os.makedirs(OUT_DIR, exist_ok=True)

C_MEAN, C_CTL, C_CLIM, C_SINGLE = "#ff6b6b", "#8b1a1a", "#000000", "#6a0dad"
C_GFS_MEAN, C_GFS_CTL, C_GFS_DET = "#1b7f3b", "#0b5c2e", "#0b6e8f"
C_EPS_MEAN, C_IFS_CTL = "#1f77b4", "#08306b"


def load_aifs(date, cyc, level):
    e = np.load(os.path.join(DATA, f"aifs_{date}{cyc}_u{level}.npz"), allow_pickle=True)
    s = np.load(os.path.join(DATA, f"aifs_single_{date}{cyc}_u{level}.npz"), allow_pickle=True)
    return e, s


def noaa(name, date, cyc):
    p = os.path.join(DATA, f"{name}_{date}{cyc}.npz")
    return np.load(p, allow_pickle=True) if os.path.exists(p) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261026")
    ap.add_argument("--cyc", default="1200")
    ap.add_argument("--level", type=int, default=10)
    ap.add_argument("--lat", type=float, default=60.0)
    a = ap.parse_args()
    cyc = a.cyc.zfill(4)
    cyc2 = a.cyc[:2].zfill(2)      # NOAA file names use the 2-digit cycle

    print("step1: load aifs", flush=True)
    e, s = load_aifs(a.date, cyc, a.level)
    lats = e["lats"]
    i_lat = int(np.abs(lats - a.lat).argmin())
    ens_steps = e["steps"]
    zall = e["zonal"]                       # decompress once
    zsing = s["zonal"]
    ens = {int(st): {m: zall[i, m, i_lat] for m in range(zall.shape[1])}
           for i, st in enumerate(ens_steps)}
    pert = [m for m in sorted(ens[ens_steps[0]]) if m != 0]
    aifs_mean = np.array([np.mean([ens[int(st)][m] for m in pert]) for st in ens_steps])
    aifs_ctl = np.array([ens[int(st)][0] for st in ens_steps])
    aifs_sing = np.array([zsing[i, i_lat] for i, st in enumerate(s["steps"])])

    def series(npz):
        return None if npz is None else (npz["steps"], npz["u60"])

    def _npz(path):
        return np.load(path, allow_pickle=True) if os.path.exists(path) else None
    # ECMWF publishes the 06z/18z control and EPS only out to 144 h, so those
    # runs are left off the chart entirely (same rule as the site).
    ecmwf_ops = cyc2 not in ("06", "18")
    eps = _npz(os.path.join(DATA, f"eps_{a.date}{cyc}_u{a.level}.npz")) if ecmwf_ops else None
    ifs_ctl = _npz(os.path.join(DATA, f"ifs_ctl_{a.date}{cyc}_u{a.level}.npz")) if ecmwf_ops else None
    gefs_mean = series(noaa("gefs_mean", a.date, cyc2))
    gefs_ctl = series(noaa("gefs_ctl", a.date, cyc2))
    gfs_det = series(noaa("gfs_ctl", a.date, cyc2))

    t0 = pd.Timestamp(f"{a.date[:4]}-{a.date[4:6]}-{a.date[6:8]} {a.cyc[:2]}:{a.cyc[2:4]}:00")
    aifs_t = t0 + pd.to_timedelta(ens_steps, unit="h")
    def t_of(steps):
        return t0 + pd.to_timedelta(steps, unit="h")

    # ---- ERA5 climatology from the local cache --------------------------
    all_t = [aifs_t]
    for srs in (gefs_mean, gefs_ctl, gfs_det):
        if srs is not None:
            all_t.append(t_of(srs[0]))
    allv = pd.DatetimeIndex(np.concatenate([np.asarray(x) for x in all_t]))
    start_md = f"{allv.min().month:02d}-{allv.min().day:02d}"
    end_md = f"{allv.max().month:02d}-{allv.max().day:02d}"
    print("step2: era5 query", start_md, end_md, flush=True)
    mean, std, cnt = EC.climatology(a.level, lat=a.lat, start=start_md, end=end_md)
    print("step3: era5 done", flush=True)
    def clim_at(times):
        out = []
        for t in times:
            k = f"{t.month}-{t.day}-12"
            out.append(float(mean.get(k, np.nan)))
        return np.array(out)
    def sd_at(times):
        out = []
        for t in times:
            k = f"{t.month}-{t.day}-12"
            out.append(float(std.get(k, np.nan)))
        return np.array(out)

    print("step4: plotting", flush=True)
    # ---- plot -------------------------------------------------------------
    STEP = 2
    fig, ax = plt.subplots(figsize=(13.5, 7.6))

    c_aifs = clim_at(aifs_t); c_aifs_sd = sd_at(aifs_t)
    ax.fill_between(aifs_t[::STEP], c_aifs[::STEP] - 2 * c_aifs_sd[::STEP],
                    c_aifs[::STEP] + 2 * c_aifs_sd[::STEP], color="#d9dce0", alpha=0.75, lw=0, zorder=1)
    ax.fill_between(aifs_t[::STEP], c_aifs[::STEP] - c_aifs_sd[::STEP],
                    c_aifs[::STEP] + c_aifs_sd[::STEP], color="#b4b9be", alpha=0.75, lw=0, zorder=1)
    ax.plot(aifs_t[::STEP], c_aifs[::STEP], color=C_CLIM, lw=2.6, zorder=6,
            label="ERA5 1991-2020 climatology")

    ax.plot(aifs_t[::STEP], aifs_mean[::STEP], color=C_MEAN, lw=3.0, marker="o", markersize=4.6,
            markerfacecolor=C_MEAN, markeredgecolor="#8b1a1a", markeredgewidth=0.9, zorder=8,
            label=f"ECMWF AIFS ENS {cyc2}z mean (51)")
    ax.plot(aifs_t[::STEP], aifs_ctl[::STEP], color=C_CTL, lw=3.4, ls="-", marker="o",
            markersize=4.6, markerfacecolor=C_CTL, markeredgecolor="#4a0d0d", markeredgewidth=0.9,
            zorder=7, label=f"ECMWF AIFS ENS {cyc2}z control (cf)")
    ax.plot(aifs_t[::STEP], aifs_sing[::STEP], color=C_SINGLE, lw=2.2, marker="o", markersize=4.6,
            markerfacecolor=C_SINGLE, markeredgecolor="#3d0864", markeredgewidth=0.9, zorder=9,
            label=f"ECMWF AIFS Single {cyc2}z")

    if eps is not None:
        te = t_of(eps["steps"])
        ax.plot(te[::STEP], eps["u60"][::STEP], color=C_EPS_MEAN, lw=2.8, marker="D",
                markersize=4.2, markerfacecolor=C_EPS_MEAN, markeredgecolor="#08346b",
                markeredgewidth=0.9, zorder=8, label=f"ECMWF EPS {cyc2}z ens mean (50 members)")
    if ifs_ctl is not None:
        ti = t_of(ifs_ctl["steps"])
        ax.plot(ti[::STEP], ifs_ctl["u60"][::STEP], color=C_IFS_CTL, lw=2.6, marker="D",
                markersize=4.2, markerfacecolor=C_IFS_CTL, markeredgecolor="#04173a",
                markeredgewidth=0.9, zorder=7, label=f"ECMWF IFS {cyc2}z control (EPS ctl)")
    if gefs_mean is not None:
        t = t_of(gefs_mean[0])
        c = clim_at(t); cs = sd_at(t)
        ax.plot(t[::STEP], gefs_mean[1][::STEP], color=C_GFS_MEAN, lw=2.8, marker="s", markersize=4.2,
                markerfacecolor=C_GFS_MEAN, markeredgecolor="#0b3d1c", markeredgewidth=0.9, zorder=8,
                label=f"NOAA GEFS {cyc2}z mean")
    if gefs_ctl is not None:
        t = t_of(gefs_ctl[0])
        ax.plot(t[::STEP], gefs_ctl[1][::STEP], color=C_GFS_CTL, lw=2.6, ls="-", marker="s",
                markersize=4.2, markerfacecolor=C_GFS_CTL, markeredgecolor="#072b13",
                markeredgewidth=0.9, zorder=7, label=f"NOAA GEFS {cyc2}z control (gec00)")
    if gfs_det is not None:
        t = t_of(gfs_det[0])
        ax.plot(t[::STEP], gfs_det[1][::STEP], color=C_GFS_DET, lw=2.4, ls="-", marker="^",
                markersize=4.4, markerfacecolor=C_GFS_DET, markeredgecolor="#06414f",
                markeredgewidth=0.9, zorder=7, label=f"NOAA GFS {cyc2}z (deterministic)")

    ax.axhline(0.0, color="#000000", lw=2.0, zorder=5)
    ax.set_xlim(allv.min().normalize(), allv.max() + pd.Timedelta(hours=6))
    ax.set_ylim(-45, 45)          # symmetric: 0 m/s sits mid-plot
    ax.set_ylabel("Zonal Wind (m/s)", labelpad=3)
    ax.yaxis.set_major_locator(MultipleLocator(5))
    ax.grid(True, alpha=0.35, lw=0.7)
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", frameon=True, framealpha=0.95, fontsize=9.6)
    ax.text(0.995, 0.975, f"Analysis {pd.Timestamp(f'{a.date[:4]}-{a.date[4:6]}-{a.date[6:8]}'):%d %b} {a.cyc[:2]}Z: "
            f"{aifs_ctl[0]:.1f} m/s", transform=ax.transAxes, ha="right", va="top",
            fontsize=11, fontweight="bold", color="#111111",
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#999999", lw=0.8))
    tick_days = pd.date_range(allv.min().normalize(), allv.max(), freq="2D")
    ax.xaxis.set_major_locator(FixedLocator(mdates.date2num(tick_days)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
    ax.xaxis.set_minor_locator(mdates.DayLocator(interval=1))
    ax.tick_params(axis="x", labelrotation=0, pad=2)
    ax.set_xlabel("")

    dom = f"{a.lat:.0f}\u02daN"
    bits = ["ECMWF AIFS ENS 0.25\u00b0"]
    if eps is not None or ifs_ctl is not None:
        bits.append("EPS")
    bits.append("NOAA GFS + GEFS")
    title = f"{' + '.join(bits)}  {dom} Zonal Mean Zonal Wind"
    t = fig.suptitle(title, x=0.075, ha="left", fontsize=16.5, fontweight="bold", y=0.972)
    fig.canvas.draw()
    w = t.get_window_extent(fig.canvas.get_renderer()).width
    avail = fig.get_figwidth() * fig.dpi * 0.9
    if abs(min(avail / w, 1.30) - 1.0) > 0.01:
        t.set_fontsize(16.5 * min(avail / w, 1.30))
    fig.text(0.075, 0.933,
             f"{cyc2[:2]}z {pd.Timestamp(f'{a.date[:4]}-{a.date[4:6]}-{a.date[6:8]}'):%b %d}, {a.date[:4]}"
             f"   \u2022   ERA5 1991\u20132020 climatology   \u2022   10 hPa",
             ha="left", va="top", fontsize=10.5, color="#333333")
    fig.text(0.5, 0.005, "Valid Days", ha="center", va="bottom", fontsize=12.5, color="#111111")
    fig.text(0.988, 0.004, "@Massachusettswx", ha="right", va="bottom",
             fontsize=12.5, fontweight="bold", color="#111111")
    fig.subplots_adjust(left=0.062, right=0.988, top=0.905, bottom=0.062)
    out = f"{OUT_DIR}/MULTIMODEL_{a.date}_{cyc}_u{a.level}hPa_{int(a.lat)}N.png"
    fig.savefig(out, dpi=200)
    fig.savefig(out.replace(".png", ".pdf"))
    print("wrote", out)

    # ---- companion CSV (union of all model grids) ------------------------
    series = {"aifs_ens_mean": (aifs_t, aifs_mean), "aifs_ens_ctl": (aifs_t, aifs_ctl),
              "aifs_single": (aifs_t, aifs_sing), "era5_clim": (aifs_t, c_aifs),
              "era5_sd": (aifs_t, c_aifs_sd)}
    if eps is not None:
        series["ecmwf_eps_mean"] = (t_of(eps["steps"]), eps["u60"])
    if ifs_ctl is not None:
        series["ecmwf_ifs_ctl"] = (t_of(ifs_ctl["steps"]), ifs_ctl["u60"])
    if gefs_mean is not None:
        series["gefs_mean"] = (t_of(gefs_mean[0]), gefs_mean[1])
        series["gefs_ctl"] = (t_of(gefs_ctl[0]), gefs_ctl[1])
    if gfs_det is not None:
        series["gfs_det"] = (t_of(gfs_det[0]), gfs_det[1])
    grid = pd.DatetimeIndex(sorted(set(np.concatenate([np.asarray(t) for t, _ in series.values()]))))
    df = pd.DataFrame({"valid_time": grid})
    for k, (t, v) in series.items():
        df[k] = pd.Series(np.asarray(v, float), index=pd.DatetimeIndex(t)).reindex(grid).to_numpy()
    df["lead_days"] = (df["valid_time"] - df["valid_time"].min()).dt.total_seconds() / 86400.
    df["era5_clim"] = clim_at(grid)          # climatology over the full union window
    df["era5_sd"] = sd_at(grid)
    df.to_csv(out.replace(".png", "_timeseries.csv"), index=False)
    print("wrote csv")


if __name__ == "__main__":
    main()
