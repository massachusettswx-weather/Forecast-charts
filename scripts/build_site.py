#!/usr/bin/env python3
"""
Build a static site for GitHub Pages from the run store.

    python3 scripts/build_site.py --out site_out

Copies the rendered figures and writes a self-contained index.html (same
template the FastAPI app uses) plus latest.json.
"""
import argparse
import json
import os
import shutil
import sys

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "data", "store")
FIGDIR = os.path.join(STORE, "figures")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import site_meta

TABS = [("physics", "Physics-based", "ECMWF IFS + EPS, NOAA GFS + GEFS"),
        ("ai", "AI", "ECMWF AIFS Single + AIFS ENS"),
        ("hybrid", "Hybrid (physics + AI)", "every model on one graph"),
        ("ensembles", "Ensembles (members)", "member spaghetti + mean/control"),
        ("ssw", "SSW watch", "% of members easterly for 24 h or more - the reversal probability")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "site_out"))
    a = ap.parse_args()
    out = a.out
    os.makedirs(os.path.join(out, "fig"), exist_ok=True)

    latest = {}
    f = os.path.join(STORE, "latest.json")
    if os.path.exists(f):
        latest = json.load(open(f))
    figs = latest.get("figures", {})

    cycles = latest.get("cycles", {})
    if not cycles:                      # fall back to the current cycle
        cycles = {latest.get("cycle", ""): {"60": {k: v for k, v in figs.items()}}}
    cyc_list = sorted(cycles.keys(), reverse=True)
    # the menu offers the published views (and only the ones the store has)
    lat_list = [k for k in site_meta.PUBLISHED_KEYS
                if any(k in (cycles.get(c) or {}) for c in cycles)]
    # the site shows ONE view (the default domain and level): the other views
    # are still being checked, so they are not offered yet
    def _default_view(c):
        for l in lat_list:
            if l == site_meta.store_key(site_meta.DEFAULT_LAT,
                                        site_meta.DEFAULT_LEVEL):
                return (cycles.get(c, {}).get(l) or {})
        return {}
    tabs = []
    for key, title, blurb in TABS:
        img = None
        for c in cyc_list:
            f = _default_view(c).get(key)
            if f:
                img = "fig/" + f
                break
        tabs.append(dict(key=key, title=title, blurb=blurb, img=img))

    # Copy only the figures the site actually serves: the default view of
    # every cycle in the picker. The other views (70N, 1 hPa, NH, polar cap)
    # are rendered and kept in the store, but they are not published - that
    # halves the Pages upload.
    need = set()
    for c in cyc_list:
        for key in site_meta.PUBLISHED_KEYS:
            for f in (cycles.get(c, {}).get(key) or {}).values():
                need.add(f)
    names = []
    for name in sorted(need):
        src = os.path.join(FIGDIR, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(out, "fig", name))
            names.append(name)

    from datetime import datetime, timezone
    cyc = latest.get("cycle", "")
    labels = {}
    for c in cyc_list:
        try:
            labels[c] = datetime.strptime(c, "%Y%m%d%H").strftime("%d %b %Y %H%M UTC")
        except Exception:
            labels[c] = c
    when = labels.get(cyc, cyc)
    stf = os.path.join(STORE, "status.json")
    _st = json.load(open(stf)) if os.path.exists(stf) else {}
    partial_badge = " \u2022 PARTIAL" if _st.get("partial") and _st.get("rendered_cycle") == cyc else ""
    try:
        upd = datetime.fromisoformat(latest.get("updated", ""))
        age_h = int((datetime.now(timezone.utc) - upd).total_seconds() // 3600)
    except Exception:
        age_h = None
    try:
        t0 = datetime.strptime(cyc, "%Y%m%d%H")
        valid_from = t0.strftime("%d %b")
        valid_to = (t0 + __import__("datetime").timedelta(hours=360)).strftime("%d %b")
    except Exception:
        valid_from = valid_to = ""

    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "webapp", "templates")),
                      autoescape=select_autoescape(["html"]))
    tpl = env.get_template("index.html")
    import json as _json
    html = tpl.render(tabs=tabs, cycle=cyc, when=when,
                      updated=latest.get("updated", ""),
                      level=latest.get("level", 10), lat=latest.get("lat", 60),
                      lat_list=lat_list, key_labels={k: site_meta.key_label(k) for k in lat_list},
                      default_key=site_meta.store_key(site_meta.DEFAULT_LAT,
                                                      site_meta.DEFAULT_LEVEL),
                      cycle_list=cyc_list, cycle_labels=labels,
                      cycles_json=_json.dumps(cycles),
                      age_hours=age_h, valid_from=valid_from, valid_to=valid_to,
                      sources=site_meta.SOURCES, partial_badge=partial_badge)
    with open(os.path.join(out, "index.html"), "w") as fh:
        fh.write(html)
    json.dump(latest, open(os.path.join(out, "latest.json"), "w"), indent=2)
    open(os.path.join(out, ".nojekyll"), "w").close()
    print(f"static site built -> {out}  ({len(names)} figures, cycle {cyc or 'none'})")


if __name__ == "__main__":
    main()
