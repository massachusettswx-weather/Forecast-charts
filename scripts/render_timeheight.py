#!/usr/bin/env python3
"""
The stratosphere time-height products.

A time-height cross-section: pressure (log) against time, filled with the
zonal-mean zonal wind and contoured with its anomaly against the 1991-2020
climatology. Two panels:

  1. zonal-mean zonal wind (m/s), the ERA5 reanalysis
  2. the normalized anomaly (sigma) - the same field against its own
     1991-2020 climatology, in standard deviations

When the model forecast is given it is drawn in a lighter shading in front of
the ERA5 analysis, and the ERA5/forecast boundary is marked.

The house style: bold left title, a descriptor line under it, @Massachusettswx
bottom right.
"""
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

LEVELS37 = [1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 125, 150, 175, 200, 225,
             250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 775, 800,
             825, 850, 875, 900, 925, 950, 975, 1000]
# the pressure axis: a log scale, 1000 hPa at the bottom, 1 hPa at the top
PLEV = [1000, 700, 500, 300, 200, 100, 70, 50, 30, 20, 10, 7, 5, 3, 2, 1]
HEIGHT_KM = {1000: 0.0, 700: 3.0, 500: 5.5, 300: 9.0, 200: 12.0,
             100: 16.0, 70: 18.5, 50: 20.5, 30: 24.0, 20: 26.0,
             10: 31.0, 7: 34.0, 5: 36.0, 3: 39.5, 2: 43.0, 1: 48.0}

C_U = "#1f4fd8"          # the zonal-wind diverging map
C_ANOM = "#b03030"       # the anomaly map
C_LINE = "#7a4fd0"       # the purple anomaly contours
DOM_LABEL = {"60": "60˚N", "70": "70˚N", "0": "NH (0-90N)",
             "cap": "Polar cap (65-90N)"}


def _levels_for(clim_levels):
    """The levels of the climatology we have, in pressure order (ascending).

    contourf needs an increasing y axis; the axis itself is inverted below
    (1000 hPa at the bottom, 1 hPa at the top), the way a cross-section reads.
    """
    levs = [l for l in clim_levels if l in LEVELS37]
    return sorted(levs)


def _clim_lookup(clim, domain, level):
    """{month-day: (mean, sigma)} for one level of one domain.

    The climatology is a 30-year mean per CALENDAR day, so it is keyed by
    month and day - the year of the analysis day does not matter.
    """
    d = (clim.get("domains") or {}).get(domain) or {}
    e = d.get(str(int(level))) or {}
    if not e:
        return {}
    dates = e.get("dates") or []
    mean = e.get("mean") or []
    sd = e.get("sigma") or []
    out = {}
    for dd, m, s in zip(dates, mean, sd):
        try:
            # the dates are either 'YYYY-MM-DD' or already 'MM-DD'
            key = dd[5:] if len(dd) > 5 else dd
            out[key] = (m, s)
        except Exception:
            continue
    return out


def _panel(ax, times, levs, values, cmap, norm, title, contour=None,
           cbar_label="", extend="both"):
    """One time-height panel.

    The field arrives as (time, level); contourf wants (level, time).
    """
    mesh = ax.contourf(times, levs, values.T, levels=18, cmap=cmap, norm=norm,
                       extend=extend)
    if contour is not None:
        # contour() wants (levels, times) - the field is stored (time, level)
        cs = ax.contour(times, levs, contour.T,
                        levels=[-20, -10, 0, 10, 20],
                        colors=[C_LINE], linewidths=1.2, linestyles="--")
        ax.clabel(cs, inline=True, fontsize=8, fmt="%d")
    ax.set_yscale("log")
    ax.set_ylim(1000, 1)
    ax.set_yticks(PLEV)
    ax.set_yticklabels([str(p) for p in PLEV], fontsize=9)
    ax.set_ylabel("Pressure (hPa)", fontsize=10)
    ax.set_title(title, fontsize=11, fontweight="bold", loc="left")
    ax.grid(True, color="#cccccc", lw=0.5, alpha=0.6)
    ax.tick_params(labelsize=9)
    return mesh


def render_timeheight(out_png, cycle, domain="60", days=60, title=None,
                      sub=None, model=None, model_times=None, model_values=None,
                      clim_path=None, series_path=None, var="u"):
    """Render the time-height figure.

    model_* are optional: the forecast part is drawn in a lighter shading
    when it is given.
    """
    clim = json.load(open(clim_path or os.path.join(ROOT, "data",
                                                    "era5_clim_full.json")))
    levs = _levels_for(clim.get("levels_hPa") or LEVELS37)

    # the ERA5 analysis days: the tail of the ERA5 record
    rec = clim.get("recent") or {}
    dates = rec.get("dates") or []
    vals_by_dom = (rec.get("domains") or {}).get(domain) or {}
    cols = [np.asarray(vals_by_dom.get(str(int(l)), []), dtype=float)
            for l in levs]
    if not dates or any(len(c) != len(dates) for c in cols):
        raise RuntimeError(f"no recent ERA5 series for {domain} in the artifact")
    times = pd.to_datetime(dates)
    vals = np.stack(cols, axis=1)              # (ntime, nlev)
    if days and len(times) > days:
        times = times.iloc[-days:]
        vals = vals[-days:]

    # the anomaly of every level against its own 1991-2020 climatology
    anom = np.full(vals.shape, np.nan)
    norm_anom = np.full(vals.shape, np.nan)
    for j, lev in enumerate(levs):
        cl = _clim_lookup(clim, domain, lev)
        for i, t in enumerate(times):
            mm = cl.get(f"{t.month:02d}-{t.day:02d}")
            if not mm:
                continue
            m, s = mm
            anom[i, j] = vals[i, j] - m
            if s and s == s and s > 0:
                norm_anom[i, j] = (vals[i, j] - m) / s

    npanel = 2
    fig = plt.figure(figsize=(14.6, 7.6))
    # explicit placement: two panels, each with its own colour bar, and the
    # height scale on the far right - a twin axis per panel would sit on top
    # of the colour bars
    L, R, T, B = 0.072, 0.958, 0.845, 0.135
    CB_W, CB_PAD, HW, GAP = 0.0115, 0.010, 0.030, 0.085
    usable = R - L
    pw = (usable - (2 * CB_W + 2 * CB_PAD + GAP + HW)) / 2.0
    x1 = L
    x2 = x1 + pw + CB_PAD + CB_W + GAP
    ax1 = fig.add_axes([x1, B, pw, T - B])
    ax2 = fig.add_axes([x2, B, pw, T - B])
    axes = [ax1, ax2]
    cax1 = fig.add_axes([x1 + pw + CB_PAD, B, CB_W, T - B])
    cax2 = fig.add_axes([x2 + pw + CB_PAD, B, CB_W, T - B])
    hax = fig.add_axes([x2 + pw + CB_PAD + CB_W + 0.012, B, HW, T - B],
                      sharey=axes[0])

    # --- panel 1: the zonal wind, with the anomaly in purple contours
    vmin = float(np.nanpercentile(vals, 2))
    vmax = float(np.nanpercentile(vals, 98))
    norm = TwoSlopeNorm(vmin=vmin, vcenter=0.0, vmax=vmax)
    mesh = _panel(axes[0], times, levs, vals, "BrBG", norm,
                  "Zonal mean zonal wind (m/s)", contour=anom,
                  cbar_label="Zonal wind (m/s)")
    cb = fig.colorbar(mesh, cax=cax1)
    cb.set_label("Zonal wind (m/s)", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    # --- panel 2: the normalized anomaly
    mesh2 = _panel(axes[1], times, levs, norm_anom, "BrBG_r",
                   TwoSlopeNorm(vmin=-4, vcenter=0, vmax=4),
                   "Normalized zonal-wind anomaly (sigma)")
    cb2 = fig.colorbar(mesh2, cax=cax2)
    cb2.set_label("Normalized anomaly (sigma)", fontsize=9)
    cb2.ax.tick_params(labelsize=8)

    for ax in axes:
        ax.set_xlim(times[0], times[-1])
        ax.tick_params(axis="x", rotation=45, labelsize=8.5)
    # one height scale on the far right, for both panels
    hax.set_yscale("log")
    hax.set_ylim(1000, 1)
    hax.set_yticks(PLEV)
    hax.set_yticklabels([f"{HEIGHT_KM[p]:.0f}" for p in PLEV], fontsize=8)
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
                 x=0.035, ha="left", fontsize=16.5, fontweight="bold", y=0.975)
    fig.text(0.035, 0.925, sub or
             f"{cycle}   •   ERA5 reanalysis   •   climatology 1991-2020",
             ha="left", va="top", fontsize=10.5, color="#333333")
    fig.text(0.985, 0.015, "@Massachusettswx", ha="right", va="bottom",
             fontsize=12.5, fontweight="bold", color="#111111")
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    return out_png
