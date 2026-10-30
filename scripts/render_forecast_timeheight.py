#!/usr/bin/env python3
"""
The forecast stratosphere time-height.

The model forecast in a pressure x time cross-section, against the 1991-2020
ERA5 climatology for the same calendar days:

    panel 1   the zonal-mean wind (m/s), with the ERA5 climatology for the
              same calendar days as a heavy black line, and the anomaly
              against it in purple contours
    panel 2   the normalized anomaly (sigma)

The ERA5 *record* stops at the end of 2020 (ARCO has nothing after it), so
the past cannot be drawn next to today's forecast - the climatology is the
reference instead, which is the honest comparison. The historical side of
the record is the ERA5 archive tab.

    python3 scripts/render_forecast_timeheight.py \
        --npz data/store/series/gfs_th_2026102612.npz \
        --out data/store/figures/th_2026102612_60.png
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from render_timeheight import (PLEV, HEIGHT_KM, DOM_LABEL, _clim_lookup,  # noqa: E402
                               C_LINE)


def _domain_mean(prof_lat, lat, domain):
    """The zonal mean over a domain, from a per-latitude profile."""
    if domain in ("60", "70"):
        i = int(np.abs(lat - float(domain)).argmin())
        return float(prof_lat[i])
    if domain == "0":
        sel = lat >= 0
    elif domain == "cap":
        sel = (lat >= 65.0) & (lat <= 90.0)
    else:
        raise ValueError(domain)
    w = np.cos(np.deg2rad(lat[sel]))
    return float((prof_lat[sel] * w).sum() / w.sum())


def _clim_series(clim, domain, level, times):
    """The climatology mean for each time, for one level."""
    cl = _clim_lookup(clim, domain, level)
    out = []
    for t in times:
        mm = cl.get(f"{t.month:02d}-{t.day:02d}")
        out.append(mm[0] if mm else np.nan)
    return np.array(out, dtype=float)


def render(out_png, npz_path, domain="60", cycle=None, clim_path=None,
           title=None, sub=None):
    # the committed slice of the climatology (the levels this figure shows);
    # the full-resolution one is a build-machine artifact
    clim = json.load(open(clim_path or os.path.join(ROOT, "data",
                                                    "era5_clim_th.json")))
    z = np.load(npz_path)
    levels = [int(l) for l in z["levels"]]
    lat = z["lat"]
    steps = z["steps"]
    times = pd.to_datetime([str(t) for t in z["times"]])

    # the model field: (time, level)
    model = np.stack([np.array([_domain_mean(z[f"u_{l}"][i], lat, domain)
                                 for l in levels])
                      for i in range(len(steps))])

    # the climatology for the same calendar days, and the anomalies
    clim_line = np.stack([_clim_series(clim, domain, l, times)
                           for l in levels], axis=1)         # (time, level)
    anom = model - clim_line
    nz = np.full(model.shape, np.nan)
    for j, lev in enumerate(levels):
        cl = _clim_lookup(clim, domain, lev)
        for i, t in enumerate(times):
            mm = cl.get(f"{t.month:02d}-{t.day:02d}")
            if mm and mm[1] and mm[1] == mm[1] and mm[1] > 0:
                nz[i, j] = anom[i, j] / mm[1]

    # --- the figure
    fig = plt.figure(figsize=(14.6, 7.6))
    L, R, T, B = 0.072, 0.958, 0.845, 0.135
    CB_W, CB_PAD, HW, GAP = 0.0115, 0.010, 0.030, 0.085
    pw = (R - L - (2 * CB_W + 2 * CB_PAD + GAP + HW)) / 2.0
    x1 = L
    x2 = x1 + pw + CB_PAD + CB_W + GAP
    ax1 = fig.add_axes([x1, B, pw, T - B])
    ax2 = fig.add_axes([x2, B, pw, T - B])
    cax1 = fig.add_axes([x1 + pw + CB_PAD, B, CB_W, T - B])
    cax2 = fig.add_axes([x2 + pw + CB_PAD, B, CB_W, T - B])
    # the height scale is its own axes: sharing the y would make it overwrite
    # the pressure labels on the left
    hax = fig.add_axes([x2 + pw + CB_PAD + CB_W + 0.012, B, HW, T - B])
    # the cross-section spans the levels we fetched, nothing below them
    ylo, yhi = max(levels), min(levels)
    plev = [p for p in PLEV if ylo <= p <= yhi] or list(levels)

    vmin = float(np.nanpercentile(model, 2))
    vmax = float(np.nanpercentile(model, 98))
    norm = TwoSlopeNorm(vmin=min(vmin, -5), vcenter=0.0, vmax=max(vmax, 5))
    mesh = ax1.contourf(times, levels, model.T, levels=18, cmap="BrBG",
                        norm=norm, extend="both")
    cs = ax1.contour(times, levels, anom.T,
                     levels=[-30, -20, -10, -5, 0, 5, 10, 20, 30],
                     colors=[C_LINE], linewidths=1.0, linestyles="--")
    ax1.clabel(cs, inline=True, fontsize=8, fmt="%d")
    # the climatology: a thin family for every level, and 10 hPa heavy black
    for j, lev in enumerate(levels):
        ax1.plot(times, clim_line[:, j], color="#000000", lw=0.9, alpha=0.45,
                 zorder=6)
    if 10 in levels:
        j = levels.index(10)
        ax1.plot(times, clim_line[:, j], color="#000000", lw=2.4, zorder=8,
                 label="ERA5 1991–2020 climatology")
    ax1.set_yscale("log")
    ax1.set_ylim(ylo, yhi)
    ax1.set_yticks(plev)
    ax1.set_yticklabels([str(p) for p in plev], fontsize=9)
    ax1.set_ylabel("Pressure (hPa)", fontsize=10)
    ax1.set_title("Zonal mean zonal wind (m/s)", fontsize=11,
                  fontweight="bold", loc="left")
    ax1.grid(True, color="#cccccc", lw=0.5, alpha=0.6)
    ax1.tick_params(labelsize=9)

    cb = fig.colorbar(mesh, cax=cax1)
    cb.set_label("Zonal wind (m/s)", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    mesh2 = ax2.contourf(times, levels, nz.T, levels=18, cmap="BrBG_r",
                         norm=TwoSlopeNorm(vmin=-4, vcenter=0, vmax=4),
                         extend="both")
    ax2.set_yscale("log")
    ax2.set_ylim(ylo, yhi)
    ax2.set_yticks(plev)
    ax2.set_yticklabels([str(p) for p in plev], fontsize=9)
    ax2.set_ylabel("Pressure (hPa)", fontsize=10)
    ax2.set_title("Normalized zonal-wind anomaly (sigma)", fontsize=11,
                  fontweight="bold", loc="left")
    ax2.grid(True, color="#cccccc", lw=0.5, alpha=0.6)
    ax2.tick_params(labelsize=9)
    cb2 = fig.colorbar(mesh2, cax=cax2)
    cb2.set_label("Normalized anomaly (sigma)", fontsize=9)
    cb2.ax.tick_params(labelsize=8)

    for ax in (ax1, ax2):
        ax.set_xlim(times[0], times[-1])
        ax.tick_params(axis="x", rotation=45, labelsize=8.5)
    hax.set_yscale("log")
    hax.set_ylim(ylo, yhi)
    hp = [p for p in PLEV if ylo <= p <= yhi and p in HEIGHT_KM]
    hax.set_yticks(hp)
    hax.set_yticklabels([f"{HEIGHT_KM[p]:.0f}" for p in hp], fontsize=8)
    hax.yaxis.tick_right()
    hax.yaxis.set_label_position("right")
    hax.set_ylabel("Height (km)", fontsize=9, color="#555555")
    hax.tick_params(labelsize=8, colors="#555555")
    hax.set_xticks([])
    hax.tick_params(axis="x", length=0)
    for s in ("left", "bottom"):
        hax.spines[s].set_visible(False)
    hax.spines["right"].set_color("#777777")

    dom_label = DOM_LABEL.get(domain, domain)
    fig.suptitle(title or f"Stratosphere — {dom_label} time-height",
                 x=0.035, ha="left", fontsize=16.5, fontweight="bold", y=0.972)
    fig.text(0.035, 0.918, sub or
             f"{cycle or ''}   •   NOAA GFS forecast   •   "
             f"ERA5 1991–2020 climatology (black) and the anomaly in purple",
             ha="left", va="top", fontsize=10.5, color="#333333")
    fig.text(0.985, 0.018, "@Massachusettswx", ha="right", va="bottom",
             fontsize=12.5, fontweight="bold", color="#111111")
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    return out_png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--domain", default="60")
    ap.add_argument("--cycle", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--sub", default=None)
    a = ap.parse_args()
    render(a.out, a.npz, domain=a.domain, cycle=a.cycle,
           title=a.title, sub=a.sub)
    print("wrote", a.out, os.path.getsize(a.out) // 1024, "KB")


if __name__ == "__main__":
    main()
