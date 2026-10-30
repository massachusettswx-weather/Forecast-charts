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
from matplotlib.ticker import MaxNLocator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from render_timeheight import (LEVELS37, PLEV, HEIGHT_KM, DOM_LABEL,   # noqa: E402
                               _levels_for, _clim_lookup, _panel, C_LINE,
                               C_WIND, C_ANOM, frame, axes, titles, quantize)

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


def _event_data(z, clim, dates, event, domain="cap", pad=20):
    """The time-height window of one event: the wind and the anomaly."""
    levs_all = list(z["levels"])
    # the stratosphere, not the troposphere: the same range as the forecast
    # product (300-1 hPa), and it keeps the tropospheric noise out of the
    # 0 m/s line
    levs = [l for l in _levels_for(clim.get("levels_hPa") or LEVELS37)
            if l <= 300]
    series = z[f"u_{domain}"]

    i0 = dates.index(event["start"])
    i1 = dates.index(event["end"])
    a = max(0, i0 - pad)
    b = min(len(dates), i1 + pad)
    times = pd.to_datetime(dates[a:b])
    idx = {l: levs_all.index(l) for l in levs}
    vals = np.stack([series[idx[l]][a:b] for l in levs], axis=1)

    anom = np.full(vals.shape, np.nan)
    for j, lev in enumerate(levs):
        cl = _clim_lookup(clim, domain, lev)
        for i, t in enumerate(times):
            mm = cl.get(f"{t.month:02d}-{t.day:02d}")
            if mm:
                anom[i, j] = vals[i, j] - mm[0]
    return times, levs, vals, anom


def _event_wind(z, clim, dates, event, out_png, domain="cap", pad=20):
    """Product 1 of the event: the zonal-mean wind (m/s)."""
    times, levs, vals, anom = _event_data(z, clim, dates, event, domain, pad)
    ylo, yhi = float(max(levs)), float(min(levs))

    fig, ax, cax, hax = frame()
    lo = float(np.nanpercentile(vals, 2))
    hi = float(np.nanpercentile(vals, 98))
    vmin = min(-8.0, lo)
    vmax = max(12.0, hi)
    mesh = ax.contourf(times, levs, vals.T, levels=21, cmap=C_WIND,
                       norm=TwoSlopeNorm(vmin=vmin, vcenter=0.0, vmax=vmax),
                       extend="both")
    cs0 = ax.contour(times, levs, vals.T, levels=[0.0], colors=["#222222"],
                     linewidths=1.8, alpha=0.9, zorder=9, linestyles="dashed")
    ax.clabel(cs0, inline=True, fontsize=8.5, fmt="0 m/s")
    axes(ax, hax, ylo, yhi, times)
    ax.grid(True, color="#ffffff", lw=0.6, alpha=0.55)
    cb = fig.colorbar(mesh, cax=cax)
    cb.locator = MaxNLocator(nbins=8, integer=True)
    cb.update_ticks()
    cb.set_label("Zonal wind (m/s)", fontsize=9.5)
    cb.ax.tick_params(labelsize=8.5)
    dom = DOM_LABEL.get(domain, domain)
    titles(fig, f"ERA5 — {event['start']} reversal, {dom}",
           f"easterly {event['days']} days, deepest {event['min']:.1f} m/s "
           f"on {event['min_date']}   •   blue is easterly, red is westerly")
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    quantize(out_png)
    return out_png


def _event_anom(z, clim, dates, event, out_png, domain="cap", pad=20):
    """Product 2 of the event: the anomaly against the 1991-2020 climatology."""
    times, levs, vals, anom = _event_data(z, clim, dates, event, domain, pad)
    ylo, yhi = float(max(levs)), float(min(levs))

    fig, ax, cax, hax = frame()
    lim = float(np.nanpercentile(np.abs(anom), 99))
    lim = max(8.0, min(lim, 45.0))
    step = 5.0 if lim <= 12 else (10.0 if lim <= 25 else 20.0)
    k = int(lim // step)
    mesh = ax.contourf(times, levs, anom.T, levels=21, cmap=C_ANOM,
                       norm=TwoSlopeNorm(vmin=-lim, vcenter=0.0, vmax=lim),
                       extend="both")
    cs = ax.contour(times, levs, anom.T,
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
    titles(fig, f"ERA5 — {event['start']} reversal, {dom} anomaly",
           f"against the 1991–2020 climatology   •   blue is weaker than "
           f"normal, red is stronger")
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    quantize(out_png)
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
        # the same domain as the catalogue, so the two agree
        out = os.path.join(a.out_dir, f"event_{e['start']}.png")
        _event_wind(z, clim, dates, e, out, domain=a.domain)
        out2 = os.path.join(a.out_dir, f"eventanom_{e['start']}.png")
        _event_anom(z, clim, dates, e, out2, domain=a.domain)
        print("wrote", out, flush=True)
        print("wrote", out2, flush=True)


if __name__ == "__main__":
    main()
