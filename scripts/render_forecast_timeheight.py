#!/usr/bin/env python3
"""
The forecast stratosphere products - TWO separate graphics.

The polar vortex is a vertical structure, so a single level is not enough:
the reversal has to be seen descending through the stratosphere. The wind
field and the anomaly are read differently and consulted separately, so
they are two products, each its own graphic:

    stratosphere_<cycle>_u10_<domain>.png    the zonal-mean wind (m/s)
                                             through the stratosphere, with
                                             the ERA5 1991-2020 climatology
    anomaly_<cycle>_u10_<domain>.png         the anomaly against that
                                             climatology (m/s)

The colour scheme is the same in both, and it is the standard anomaly
convention: BLUE is negative (easterly wind, or weaker than normal) and
RED is positive (westerly wind, or stronger than normal). So the reversal
reads as blue against the red vortex, in every stratosphere graphic.

    python3 scripts/render_forecast_timeheight.py \
        --npz data/store/series/gfs_th_2026102918.npz \
        --out data/store/figures/stratosphere_2026102918_u10_cap.png \
        --domain cap --cycle "18z 29 Oct 2026"
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
from matplotlib.ticker import MaxNLocator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from render_timeheight import (PLEV, HEIGHT_KM, DOM_LABEL, _clim_lookup,  # noqa: E402
                               C_WIND, C_ANOM, frame, axes, titles, quantize)

C_CLIM = "#111111"
C_ZERO = "#333333"


# ---------------------------------------------------------------- the data

def _domain_mean(prof, lat, domain):
    """The zonal mean over a domain, from a per-latitude profile."""
    if domain in ("60", "70"):
        i = int(np.abs(lat - float(domain)).argmin())
        return float(prof[i])
    sel = (lat >= 0.0) if domain == "0" else ((lat >= 65.0) & (lat <= 90.0))
    w = np.cos(np.deg2rad(lat[sel]))
    return float((prof[sel] * w).sum() / w.sum())


def _clim_series(clim, domain, level, times):
    """The climatology mean for each time, for one level."""
    cl = _clim_lookup(clim, domain, level)
    out = []
    for t in times:
        mm = cl.get(f"{t.month:02d}-{t.day:02d}")
        out.append(mm[0] if mm else np.nan)
    return np.array(out, dtype=float)


def load(npz_path, domain, clim_path=None):
    """The model field, the climatology and the anomaly for one domain."""
    clim = json.load(open(clim_path or os.path.join(ROOT, "data",
                                                    "era5_clim_th.json")))
    z = np.load(npz_path)
    levels = [int(l) for l in z["levels"]]
    lat = z["lat"]
    times = pd.to_datetime([str(t) for t in z["times"]])

    model = np.stack([np.array([_domain_mean(z[f"u_{l}"][i], lat, domain)
                                 for l in levels])
                      for i in range(len(times))])
    clim_line = np.stack([_clim_series(clim, domain, l, times)
                           for l in levels], axis=1)
    anom = model - clim_line
    sd = np.stack([np.array([(_clim_lookup(clim, domain, l)
                              .get(f"{t.month:02d}-{t.day:02d}") or
                              [np.nan, np.nan])[1] for t in times])
                   for l in levels], axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        nz = np.where(sd > 0, anom / sd, np.nan)
    return dict(times=times, levels=levels, model=model, clim=clim_line,
                anom=anom, nz=nz)


# ---------------------------------------------------------------- the products

def render_wind(out_png, npz_path, domain="60", cycle=None, clim_path=None,
                title=None, sub=None):
    """Product 1: the zonal-mean wind through the stratosphere."""
    d = load(npz_path, domain, clim_path)
    times, levels = d["times"], d["levels"]
    ylo, yhi = float(max(levels)), float(min(levels))

    fig, ax, cax, hax = frame()
    lo = float(np.nanpercentile(d["model"], 2))
    hi = float(np.nanpercentile(d["model"], 98))
    vmin = min(-8.0, lo)                  # keep the easterly side readable
    vmax = max(12.0, hi)
    norm = TwoSlopeNorm(vmin=vmin, vcenter=0.0, vmax=vmax)
    mesh = ax.contourf(times, levels, d["model"].T, levels=21, cmap=C_WIND,
                       norm=norm, extend="both")
    # the ERA5 climatology for the same calendar days
    for j in range(len(levels)):
        ax.plot(times, d["clim"][:, j], color=C_CLIM, lw=0.8, alpha=0.35,
                zorder=6)
    if 10 in levels:
        ax.plot(times, d["clim"][:, levels.index(10)], color=C_CLIM, lw=2.2,
                zorder=8)
    # the 0 m/s line: where the wind turns easterly - the edge of the vortex
    cs0 = ax.contour(times, levels, d["model"].T, levels=[0.0],
                     colors=["#222222"], linewidths=1.8, alpha=0.9, zorder=9,
                     linestyles="dashed")
    ax.clabel(cs0, inline=True, fontsize=8.5, fmt="0 m/s")
    axes(ax, hax, ylo, yhi, times)
    ax.grid(True, color="#ffffff", lw=0.6, alpha=0.55)
    from matplotlib.lines import Line2D
    handles = [Line2D([], [], color=C_CLIM, lw=2.2,
                      label="ERA5 1991–2020 climatology (10 hPa)"),
               Line2D([], [], color="#222222", lw=1.8, linestyle="--",
                      label="0 m/s - the edge of the vortex")]
    leg = ax.legend(handles=handles, loc="lower left", fontsize=9.5,
                    framealpha=0.92)
    leg.get_frame().set_edgecolor("#cccccc")
    cb = fig.colorbar(mesh, cax=cax)
    cb.locator = MaxNLocator(nbins=8, integer=True)
    cb.update_ticks()
    cb.set_label("Zonal wind (m/s)", fontsize=9.5)
    cb.ax.tick_params(labelsize=8.5)
    dom = DOM_LABEL.get(domain, domain)
    titles(fig, title or f"Stratosphere — {dom} zonal-mean zonal wind",
            sub or f"{cycle or ''}   •   NOAA GFS forecast   •   "
                   f"blue is easterly, red is westerly   •   "
                   f"the ERA5 1991–2020 climatology in black")
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    quantize(out_png)
    return out_png


def render_anom(out_png, npz_path, domain="60", cycle=None, clim_path=None,
                title=None, sub=None):
    """Product 2: the anomaly against the ERA5 climatology, in m/s."""
    d = load(npz_path, domain, clim_path)
    times, levels = d["times"], d["levels"]
    ylo, yhi = float(max(levels)), float(min(levels))

    fig, ax, cax, hax = frame()
    a = d["anom"]
    lim = float(np.nanpercentile(np.abs(a), 99))
    lim = max(8.0, min(lim, 45.0))
    norm = TwoSlopeNorm(vmin=-lim, vcenter=0.0, vmax=lim)
    mesh = ax.contourf(times, levels, a.T, levels=21, cmap=C_ANOM, norm=norm,
                       extend="both")
    # the contours give the field some structure
    step = 5.0 if lim <= 12 else (10.0 if lim <= 25 else 20.0)
    k = int(lim // step)
    cs = ax.contour(times, levels, a.T,
                    levels=[i * step for i in range(-k, k + 1)
                            if 0 < abs(i * step) < lim],
                    colors=["#333333"], linewidths=0.9, alpha=0.75)
    ax.clabel(cs, inline=True, fontsize=8, fmt="%d")
    axes(ax, hax, ylo, yhi, times)
    ax.grid(True, color="#ffffff", lw=0.6, alpha=0.45)
    cb = fig.colorbar(mesh, cax=cax,
                      ticks=np.arange(-(k - 1) * step, k * step, step))
    cb.set_label("Anomaly (m/s)", fontsize=9.5)
    cb.ax.tick_params(labelsize=8.5)
    dom = DOM_LABEL.get(domain, domain)
    titles(fig, title or f"Stratosphere — {dom} zonal-wind anomaly",
            sub or f"{cycle or ''}   •   NOAA GFS forecast   •   "
                   f"against the ERA5 1991–2020 climatology   •   "
                   f"blue is weaker than normal, red is stronger")
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    quantize(out_png)
    return out_png


def render(out_png, npz_path, domain="60", cycle=None, clim_path=None,
           title=None, sub=None):
    """The wind product, at the path the pipeline expects."""
    return render_wind(out_png, npz_path, domain=domain, cycle=cycle,
                       clim_path=clim_path, title=title, sub=sub)


def anomaly_path(wind_path):
    """The anomaly graphic that goes with a wind graphic."""
    d, f = os.path.split(wind_path)
    return os.path.join(d, f.replace("stratosphere_", "anomaly_", 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--domain", default="60")
    ap.add_argument("--cycle", default=None)
    ap.add_argument("--which", default="both", choices=["both", "wind", "anom"])
    ap.add_argument("--title", default=None)
    ap.add_argument("--sub", default=None)
    a = ap.parse_args()
    if a.which in ("both", "wind"):
        render_wind(a.out, a.npz, domain=a.domain, cycle=a.cycle,
                    title=a.title, sub=a.sub)
        print("wrote", a.out, os.path.getsize(a.out) // 1024, "KB")
    if a.which in ("both", "anom"):
        out = anomaly_path(a.out)
        render_anom(out, a.npz, domain=a.domain, cycle=a.cycle,
                    title=a.title, sub=a.sub)
        print("wrote", out, os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    main()
