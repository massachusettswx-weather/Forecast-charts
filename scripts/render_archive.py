#!/usr/bin/env python3
"""
The ERA5 stratosphere archive - the figures.

Two things, both drawn from the local ERA5 record (no downloads):

  catalogue.png     every reversal event ERA5 saw between 1959 and 2020, on a
                    time axis, sized by how deep the reversal got. The record
                    minimum is marked.
  event_<date>.png  the time-height cross-section of one event: the zonal-mean
                    zonal wind through the stratosphere, with the anomaly
                    against the 1991-2020 climatology in purple contours and
                    the normalized anomaly in the second panel.

    python3 scripts/render_archive.py --out-dir data/store/archive
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

from render_timeheight import (LEVELS37, PLEV, HEIGHT_KM, DOM_LABEL,   # noqa: E402
                               _levels_for, _clim_lookup, _panel, C_LINE)

SERIES = os.path.join(ROOT, "data", "era5_series.npz")
CLIM = os.path.join(ROOT, "data", "era5_clim_full.json")
ARCHIVE = os.path.join(ROOT, "data", "ssw_archive.json")

C_U_EDGE = "#f2f7ff"


def load():
    z = np.load(SERIES)
    clim = json.load(open(CLIM))
    arch = json.load(open(ARCHIVE))
    dates = [str(d) for d in z["dates"]]
    return z, clim, arch, dates


def _quantize(path):
    """Shrink the PNG.

    These figures have few colours, so a 128-colour palette cuts the file
    by about two thirds - the site carries them, and every kilobyte counts.
    """
    try:
        from PIL import Image
    except ImportError:
        return path
    try:
        im = Image.open(path).convert("RGB")
        q = im.quantize(colors=128, method=Image.MEDIANCUT,
                        dither=Image.FLOYDSTEINBERG)
        q.save(path, optimize=True)
    except Exception:
        pass
    return path


def _catalogue(z, arch, dates, out_png, domain="60"):
    """Every reversal event on a time axis."""
    levs = list(z["levels"])
    li = levs.index(arch["level"])
    series = z[f"u_{domain}"]
    vals = series[li]

    fig, ax = plt.subplots(figsize=(16.5, 6.4))
    fig.subplots_adjust(left=0.055, right=0.985, top=0.845, bottom=0.135)

    # the ERA5 zonal-mean wind through the record
    t = pd.to_datetime(dates)
    ax.plot(t, vals, lw=0.55, color="#8a9199", alpha=0.85, zorder=2,
            label=f"ERA5 zonal-mean u @ {arch['level']} hPa")
    ax.axhline(0, color="#111111", lw=1.0, zorder=3)

    evs = arch["domains"][domain]["events"]
    rec = min(evs, key=lambda e: e["min"])
    for e in evs:
        x = pd.Timestamp(e["start"])
        depth = e["depth"]
        col = plt.cm.Reds(0.25 + 0.55 * min(depth / 30.0, 1.0))
        is_rec = e is rec
        # a stem from zero down to the depth of the reversal, so the events
        # read as a set and the series stays visible
        ax.plot([x, x], [0, e["min"]], "-", color=col,
                lw=2.6 if is_rec else 1.5, zorder=5, alpha=0.95)
        ax.plot([x], [e["min"]], "o", ms=5.5 if is_rec else 4.0, color=col,
                markeredgecolor="#3d0909" if is_rec else "#5a1010",
                markeredgewidth=1.0 if is_rec else 0.5, zorder=6)

    # the record minimum
    ax.annotate(f"record minimum  {rec['min']:.1f} m/s  ({rec['min_date']})",
                xy=(pd.Timestamp(rec["min_date"]), rec["min"]),
                xytext=(pd.Timestamp("1998-01-01"), -38),
                fontsize=10.5, color="#7a1414",
                arrowprops=dict(arrowstyle="->", color="#7a1414", lw=1.2,
                                connectionstyle="arc3,rad=0.2"))

    ax.set_ylabel(f"Zonal-mean zonal wind (m/s), {DOM_LABEL.get(domain, domain)}",
                  fontsize=11)
    ax.set_xlabel("", fontsize=10)
    ax.grid(True, color="#d8dcdf", lw=0.6, alpha=0.8)
    ax.set_xlim(pd.Timestamp(dates[0]), pd.Timestamp(dates[-1]))
    ax.set_ylim(min(-42, float(np.nanmin(vals)) - 4),
                float(np.nanpercentile(vals, 99.5)) + 4)

    n_ev = len(evs)
    fig.suptitle(f"ERA5 stratosphere archive — {DOM_LABEL.get(domain, domain)}",
                 x=0.055, ha="left", fontsize=16.5, fontweight="bold", y=0.972)
    fig.text(0.055, 0.918,
             f"every reversal event ERA5 recorded, 1959–2020   •   "
             f"{n_ev} events   •   easterly for 4 days or more, "
             f"{arch['level']} hPa   •   stem length and shade = depth",
             ha="left", va="top", fontsize=10.5, color="#333333")
    fig.text(0.985, 0.018, "@Massachusettswx", ha="right", va="bottom",
             fontsize=12.5, fontweight="bold", color="#111111")
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    _quantize(out_png)
    return out_png


def _event_timeheight(z, clim, dates, event, out_png, domain="cap",
                      pad=20):
    """The time-height cross-section of one event."""
    levs_all = list(z["levels"])
    levs = _levels_for(clim.get("levels_hPa") or LEVELS37)
    series = z[f"u_{domain}"]

    i0 = dates.index(event["start"])
    i1 = dates.index(event["end"])
    a = max(0, i0 - pad)
    b = min(len(dates), i1 + pad)
    times = pd.to_datetime(dates[a:b])
    idx = {l: levs_all.index(l) for l in levs}
    vals = np.stack([series[idx[l]][a:b] for l in levs], axis=1)

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

    fig = plt.figure(figsize=(14.6, 7.4))
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

    vmin = float(np.nanpercentile(vals, 2))
    vmax = float(np.nanpercentile(vals, 98))
    mesh = _panel(axes[0], times, levs, vals, "BrBG",
                  TwoSlopeNorm(vmin=vmin, vcenter=0.0, vmax=vmax),
                  "Zonal mean zonal wind (m/s)", contour=anom)
    cb = fig.colorbar(mesh, cax=cax1)
    cb.set_label("Zonal wind (m/s)", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    mesh2 = _panel(axes[1], times, levs, norm_anom, "BrBG_r",
                   TwoSlopeNorm(vmin=-4, vcenter=0, vmax=4),
                   "Normalized zonal-wind anomaly (sigma)")
    cb2 = fig.colorbar(mesh2, cax=cax2)
    cb2.set_label("Normalized anomaly (sigma)", fontsize=9)
    cb2.ax.tick_params(labelsize=8)

    for ax in axes:
        ax.set_xlim(times[0], times[-1])
        ax.tick_params(axis="x", rotation=45, labelsize=8.5)
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

    fig.suptitle(f"ERA5 — {event['start']} reversal, {DOM_LABEL.get(domain, domain)}",
                 x=0.035, ha="left", fontsize=16.5, fontweight="bold", y=0.972)
    fig.text(0.035, 0.918,
             f"easterly {event['days']} days, deepest {event['min']:.1f} m/s "
             f"on {event['min_date']}   •   anomaly against the 1991–2020 "
             f"climatology",
             ha="left", va="top", fontsize=10.5, color="#333333")
    fig.text(0.985, 0.018, "@Massachusettswx", ha="right", va="bottom",
             fontsize=12.5, fontweight="bold", color="#111111")
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    _quantize(out_png)
    return out_png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "data", "store",
                                                     "archive"))
    ap.add_argument("--domain", default="60")
    ap.add_argument("--events", type=int, default=6,
                    help="how many of the deepest events to draw")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    z, clim, arch, dates = load()
    cat = _catalogue(z, arch, dates,
                     os.path.join(a.out_dir, "catalogue.png"), a.domain)
    print("wrote", cat, os.path.getsize(cat) // 1024, "KB", flush=True)

    evs = sorted(arch["domains"][a.domain]["events"],
                 key=lambda e: e["depth"], reverse=True)[:a.events]
    for e in evs:
        out = os.path.join(a.out_dir, f"event_{e['start']}.png")
        # the same domain as the catalogue, so the two agree
        _event_timeheight(z, clim, dates, e, out, domain=a.domain)
        print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
