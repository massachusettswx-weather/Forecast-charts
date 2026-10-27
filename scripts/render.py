#!/usr/bin/env python3
"""
Render the four site tabs from the run store, in the house style.

Tabs
  physics   : ECMWF IFS + EPS, NOAA GFS + GEFS        (physics-based models)
  ai        : ECMWF AIFS Single + AIFS ENS            (AI models)
  hybrid    : all of them on one graph                (physics + AI together)
  ensembles : member spaghetti for the ensembles
"""
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

# house style
C = dict(mean_aifs="#ff6b6b", ctl_aifs="#8b1a1a", single="#6a0dad",
         eps_mean="#1f77b4", ifs_ctl="#08306b",
         gefs_mean="#1b7f3b", gefs_ctl="#0b5c2e", gfs="#0b6e8f",
         clim="#000000", member="#1f4fd8")

TABS = [("physics", "Physics-based"),
        ("ai", "AI"),
        ("hybrid", "Hybrid (physics + AI)"),
        ("ensembles", "Ensembles (members)")]


def cycle_label(cycle):
    """'18z 26 Oct 2026' - the cycle hour, NOT the day of the month."""
    try:
        d = pd.Timestamp(f"{cycle[:4]}-{cycle[4:6]}-{cycle[6:8]} {cycle[8:10]}Z")
        return f"{d.strftime('%H')}z {d.strftime('%d %b %Y')}"
    except Exception:
        return f"{cycle[8:10]}z {cycle}"


def step_times(steps, cycle):
    """step hours -> datetimes for a run cycle (YYYYMMDDHH)."""
    t0 = pd.Timestamp(f"{cycle[:4]}-{cycle[4:6]}-{cycle[6:8]} {cycle[8:10]}:00")
    return t0 + pd.to_timedelta(np.asarray(steps), unit="h")


def load_clim(level, lat, t0, t1):
    mean, std, _ = EC.climatology(level, lat=lat,
                                  start=f"{t0.month:02d}-{t0.day:02d}",
                                  end=f"{t1.month:02d}-{t1.day:02d}")
    def pick(times):
        out = []
        for t in times:
            k = f"{t.month}-{t.day}-12"
            out.append(float(mean.get(k, np.nan)))
        return np.array(out)
    def pick_sd(times):
        out = []
        for t in times:
            k = f"{t.month}-{t.day}-12"
            out.append(float(std.get(k, np.nan)))
        return np.array(out)
    return pick, pick_sd


def _finish(fig, ax, title, sub, tmin, tmax, ylim):
    ax.axhline(0.0, color="#000000", lw=2.0, zorder=5)
    ax.set_xlim(tmin.normalize(), tmax + pd.Timedelta(hours=6))
    ax.set_ylim(*ylim)
    ax.set_ylabel("Zonal Wind (m/s)", labelpad=3)
    ax.yaxis.set_major_locator(MultipleLocator(5))
    ax.grid(True, alpha=0.35, lw=0.7)
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", labelrotation=0, pad=2)
    td = pd.date_range(tmin.normalize(), tmax, freq="2D")
    ax.xaxis.set_major_locator(FixedLocator(mdates.date2num(td)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
    ax.xaxis.set_minor_locator(mdates.DayLocator(interval=1))
    fig.suptitle(title, x=0.062, ha="left", fontsize=16.5, fontweight="bold", y=0.972)
    fig.text(0.062, 0.933, sub, ha="left", va="top", fontsize=10.5, color="#333333")
    fig.text(0.5, 0.005, "Valid Days", ha="center", va="bottom", fontsize=12.5, color="#111111")
    fig.text(0.988, 0.004, "@Massachusettswx", ha="right", va="bottom",
             fontsize=12.5, fontweight="bold", color="#111111")
    fig.subplots_adjust(left=0.062, right=0.988, top=0.905, bottom=0.062)


def render_tab(tab, S, clim_pick, clim_sd, out_png, level, lat, cycle, analysis):
    """S: dict of series -> (times, values)."""
    cyc_h = cycle[8:10]
    fig, ax = plt.subplots(figsize=(13.5, 7.6))
    S = {k: (step_times(v[0], cycle), v[1]) for k, v in S.items()}
    tmin = min(v[0][0] for v in S.values())
    tmax = max(v[0][-1] for v in S.values())
    cts = pd.date_range(tmin, tmax, freq="6h")
    clim = clim_pick(cts)
    csd = clim_sd(cts)
    ax.fill_between(cts, clim - 2 * csd, clim + 2 * csd, color="#d9dce0", alpha=0.75, lw=0, zorder=1)
    ax.fill_between(cts, clim - csd, clim + csd, color="#b4b9be", alpha=0.75, lw=0, zorder=1)
    ax.plot(cts, clim, color=C["clim"], lw=2.6, zorder=6, label="ERA5 1991-2020 climatology")

    order = []
    if tab in ("physics", "hybrid"):
        order += [("gefs_mean", f"NOAA GEFS {cyc_h}z mean", C["gefs_mean"], "s", "-"),
                  ("gefs_ctl", f"NOAA GEFS {cyc_h}z control (gec00)", C["gefs_ctl"], "s", "-"),
                  ("gfs", f"NOAA GFS {cyc_h}z (deterministic)", C["gfs"], "^", "-")]
    if tab in ("physics", "hybrid"):
        order += [("eps_mean", f"ECMWF EPS {cyc_h}z ens mean", C["eps_mean"], "D", "-"),
                  ("ifs_ctl", f"ECMWF IFS {cyc_h}z control (EPS ctl)", C["ifs_ctl"], "D", "-")]
    if tab in ("ai", "hybrid"):
        order += [("aifs_mean", f"ECMWF AIFS ENS {cyc_h}z mean (51)", C["mean_aifs"], "o", "-"),
                  ("aifs_ctl", f"ECMWF AIFS ENS {cyc_h}z control (cf)", C["ctl_aifs"], "o", "-"),
                  ("aifs_single", f"ECMWF AIFS Single {cyc_h}z", C["single"], "o", "-")]
    if tab == "ensembles":
        mem_specs = [("aifs_ens_members", f"ECMWF AIFS ENS {cyc_h}z members (50)", "#1f4fd8"),
                     ("eps_members", f"ECMWF EPS {cyc_h}z members (50)", "#00a0c6"),
                     ("gefs_members", f"NOAA GEFS {cyc_h}z members (30)", "#8fbf00")]
        for key, label, col in mem_specs:
            if key not in S:
                continue
            times, prof = S[key]                 # prof is (nmember, nstep)
            for j in range(prof.shape[0]):
                ax.plot(times, prof[j], color=col, lw=0.6, alpha=0.35, zorder=2,
                        label=(label if j == 0 else None))
        for key, label, col, mk, ls in [("aifs_mean", f"ECMWF AIFS ENS {cyc_h}z mean", C["mean_aifs"], "o", "-"),
                                       ("aifs_ctl", f"ECMWF AIFS ENS {cyc_h}z control", C["ctl_aifs"], "o", "-"),
                                       ("eps_mean", f"ECMWF EPS {cyc_h}z ens mean", C["eps_mean"], "D", "-"),
                                       ("gefs_mean", f"NOAA GEFS {cyc_h}z ens mean", C["gefs_mean"], "s", "-")]:
            if key in S:
                t, v = S[key]
                ax.plot(t, v, color=col, lw=2.8, marker=mk, markersize=4.2,
                        markerfacecolor=col, markeredgecolor="none", zorder=8,
                        label=label)
    else:
        for key, label, col, mk, ls in order:
            if key not in S:
                continue
            t, v = S[key]
            ax.plot(t, v, color=col, lw=2.8, marker=mk, markersize=4.2,
                    markerfacecolor=col, markeredgecolor="none", zorder=8, label=label)

    if tab == "ensembles":
        handles, labels = ax.get_legend_handles_labels()
        seen, h2, l2 = set(), [], []
        for h, l in zip(handles, labels):
            if l not in seen:
                seen.add(l); h2.append(h); l2.append(l)
        ax.legend(h2, l2, loc="lower left", frameon=True, framealpha=0.95, fontsize=9.6)
    else:
        ax.legend(loc="lower left", frameon=True, framealpha=0.95, fontsize=9.6)

    dom = f"{lat:.0f}˚N"
    # title reflects only the models that are actually in this figure
    has = lambda k: k in S
    parts = []
    if tab in ("physics", "hybrid"):
        if has("ifs_ctl") or has("eps_mean"):
            parts.append("ECMWF IFS + EPS")
        if has("gefs_mean") or has("gfs"):
            parts.append("NOAA GFS + GEFS")
    if tab in ("ai", "hybrid"):
        if has("aifs_mean") or has("aifs_single"):
            parts.append("ECMWF AIFS ENS + Single")
    if tab == "ensembles":
        bits = []
        if has("aifs_mean"): bits.append("AIFS ENS")
        if has("eps_mean"): bits.append("ECMWF EPS")
        if has("gefs_mean"): bits.append("NOAA GEFS")
        parts.append("Ensemble members: " + " / ".join(bits) if bits else "Ensemble members")
    titles = {
        "physics": f"{'  •  '.join(parts)}  {dom} Zonal Mean Zonal Wind",
        "ai": f"{'  •  '.join(parts)}  {dom} Zonal Mean Zonal Wind",
        "hybrid": f"{'  •  '.join(parts)}  {dom} Zonal Mean Zonal Wind",
        "ensembles": f"{'  •  '.join(parts)}  {dom} Zonal Mean Zonal Wind",
    }
    try:
        when = cycle_label(cycle)
    except Exception:
        when = cycle
    sub = (f"{when}   •   ERA5 1991–2020 climatology   •   "
           f"{level} hPa   •   analysis {analysis:.1f} m/s")
    _finish(fig, ax, titles[tab], sub, tmin, tmax, (-45, 45))
    fig.savefig(out_png, dpi=200)
    fig.savefig(out_png.replace(".png", ".pdf"))
    plt.close(fig)
    return out_png


# --------------------------------------------------------------- SSW product
SSW_SPECS = [("aifs_ens_members", "ECMWF AIFS ENS", "#1f4fd8"),
             ("eps_members", "ECMWF EPS", "#00a0c6"),
             ("gefs_members", "NOAA GEFS", "#8fbf00")]
SSW_MIN_HOURS = 24


def render_ssw(S, clim_pick, clim_sd, out_png, level, lat, cycle, analysis):
    """Reversal probability: % of members easterly for >= 24 h at 60N/10 hPa.

    S: the ensembles-tab series dict (member series + means).
    """
    import ssw as SSW

    present = [(k, lab, col) for k, lab, col in SSW_SPECS if k in S]
    if not present:
        return None
    # the store keeps the step hours; turn them into valid times
    S = {k: (step_times(np.asarray(v[0]), cycle), v[1]) for k, v in S.items()}

    fig, (ax, axp) = plt.subplots(2, 1, figsize=(13.5, 8.2), sharex=True,
                                   gridspec_kw=dict(height_ratios=[2.3, 1], hspace=0.10))
    tmin = min(pd.Timestamp(S[k][0][0]) for k, _, _ in present)
    tmax = max(pd.Timestamp(S[k][0][-1]) for k, _, _ in present)
    cts = pd.date_range(tmin, tmax, freq="6h")
    clim = clim_pick(cts)
    csd = clim_sd(cts)

    # ---- top panel: the members and their reversals -----------------------
    ax.fill_between(cts, clim - 2 * csd, clim + 2 * csd, color="#d9dce0", alpha=0.75,
                    lw=0, zorder=1)
    ax.fill_between(cts, clim - csd, clim + csd, color="#b4b9be", alpha=0.75,
                    lw=0, zorder=1)
    ax.plot(cts, clim, color=C["clim"], lw=2.6, zorder=6, label="ERA5 1991-2020 climatology")

    summary = []
    for key, label, col in present:
        times, prof = S[key]
        times = pd.DatetimeIndex(np.asarray(times))
        u = np.asarray(prof, float).T                       # (nstep, nmember)
        prob, state, onsets = SSW.reversal_probability(u, SSW_MIN_HOURS)
        # members
        for j in range(u.shape[1]):
            ax.plot(times, u[:, j], color=col, lw=0.7, alpha=0.30, zorder=2)
        # the 24 h-easterly stretches, drawn heavy
        for m, i, j in SSW.segments(state):
            ax.plot(times[i:j + 1], u[i:j + 1, m], color=col, lw=2.4, alpha=0.95,
                    zorder=5, solid_capstyle="butt")
        # ensemble mean
        ax.plot(times, u.mean(axis=1), color=col, lw=2.8, zorder=7,
                label=f"{label} mean")
        # probability curve in the lower panel
        nrev = int((onsets >= 0).sum())
        first_txt = ""
        if nrev:
            on = onsets[onsets >= 0]
            t_on = pd.DatetimeIndex(times)[int(on.min())]
            first_txt = f"\n{nrev}/{u.shape[1]} reversed by {t_on:%d %b}"
        axp.plot(times, prob, color=col, lw=2.6, marker="o", markersize=3.4,
                 markerfacecolor=col, markeredgecolor="none", zorder=8,
                 label=f"{label} ({u.shape[1]} members){first_txt}")
        on = onsets[onsets >= 0]
        first = int(on.min()) if len(on) else None
        summary.append((label, col, prob, first, len(on), u.shape[1]))

    ax.axhline(0.0, color="#000000", lw=2.0, zorder=5)
    ax.set_ylabel("Zonal Wind (m/s)", labelpad=3)
    ax.yaxis.set_major_locator(MultipleLocator(5))
    ax.grid(True, alpha=0.35, lw=0.7)
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", frameon=True, framealpha=0.95, fontsize=9.6)
    # the range that matters: the members, the zero line and the climatology
    d_lo = min(float(np.nanmin(np.asarray(S[k][1], float))) for k, _, _ in present)
    d_hi = max(float(np.nanmax(np.asarray(S[k][1], float))) for k, _, _ in present)
    lo = min(d_lo, -6.0) - 1.0
    hi = max(d_hi, float(np.nanmax(clim))) + 4.0
    ax.set_ylim(lo, hi)

    # ---- lower panel: % of members reversed -------------------------------
    axp.axhline(0.0, color="#000000", lw=1.2, zorder=5)
    axp.set_ylabel("% reversed", labelpad=3)
    axp.set_ylim(-3, 103)
    axp.yaxis.set_major_locator(MultipleLocator(25))
    axp.grid(True, alpha=0.35, lw=0.7)
    axp.set_axisbelow(True)
    axp.legend(loc="upper left", frameon=True, framealpha=0.95, fontsize=9.6)

    td = pd.date_range(tmin.normalize(), tmax, freq="2D")
    axp.xaxis.set_major_locator(FixedLocator(mdates.date2num(td)))
    axp.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
    axp.xaxis.set_minor_locator(mdates.DayLocator(interval=1))
    axp.tick_params(axis="x", labelrotation=0, pad=2)
    ax.set_xlim(tmin.normalize(), tmax + pd.Timedelta(hours=6))

    dom = f"{lat:.0f}˚N"
    title = f"SSW watch  •  {dom} @ {level} hPa easterly (≥ {SSW_MIN_HOURS} h) probability"
    fig.suptitle(title, x=0.062, ha="left", fontsize=16.5, fontweight="bold", y=0.972)
    fig.text(0.062, 0.933,
             f"{cycle_label(cycle)}   •   ERA5 1991–2020 climatology   •   "
             f"criterion: u < 0 m/s for {SSW_MIN_HOURS} h or more",
             ha="left", va="top", fontsize=10.5, color="#333333")
    fig.text(0.5, 0.012, "Valid Days", ha="center", va="bottom", fontsize=12.5, color="#111111")
    fig.text(0.988, 0.010, "@Massachusettswx", ha="right", va="bottom",
             fontsize=12.5, fontweight="bold", color="#111111")
    fig.subplots_adjust(left=0.062, right=0.988, top=0.905, bottom=0.075)
    fig.savefig(out_png, dpi=200)
    fig.savefig(out_png.replace(".png", ".pdf"))
    plt.close(fig)
    return out_png
