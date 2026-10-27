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

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "data", "store")
FIGDIR = os.path.join(STORE, "figures")

SOURCES = {
    "aifs-single": "ECMWF open data (0.25°)",
    "aifs-ens": "ECMWF open data (0.25°)",
    "ifs": "ECMWF open data (0.25°)",
    "eps": "ECMWF open data (0.25°)",
    "gfs": "NOAA open data (0.25°)",
    "gefs": "NOAA open data (0.5°)",
    "gefs_members": "NOAA open data (0.5°)",
}

TABS = [("physics", "Physics-based", "ECMWF IFS + EPS, NOAA GFS + GEFS"),
        ("ai", "AI", "ECMWF AIFS Single + AIFS ENS"),
        ("hybrid", "Hybrid (physics + AI)", "every model on one graph"),
        ("ensembles", "Ensembles (members)", "member spaghetti + mean/control")]


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

    # copy every figure we keep (all cycles)
    names = []
    for name in os.listdir(FIGDIR):
        if name.endswith(".png"):
            shutil.copy(os.path.join(FIGDIR, name), os.path.join(out, "fig", name))
            names.append(name)

    cycles = latest.get("cycles", {})
    if not cycles:                      # fall back to the current cycle
        cycles = {latest.get("cycle", ""): {k: v for k, v in figs.items()}}
    cyc_list = sorted(cycles.keys(), reverse=True)
    tabs = []
    for key, title, blurb in TABS:
        cand = [cycles[c][key] for c in cyc_list if key in cycles.get(c, {})]
        tabs.append(dict(key=key, title=title, blurb=blurb,
                         img=("fig/" + cand[0]) if cand else None))

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
                      models=latest.get("models", {}),
                      cycle_list=cyc_list, cycle_labels=labels,
                      cycles_json=_json.dumps(cycles),
                      age_hours=age_h, valid_from=valid_from, valid_to=valid_to,
                      sources=SOURCES, partial_badge=partial_badge)
    with open(os.path.join(out, "index.html"), "w") as fh:
        fh.write(html)
    json.dump(latest, open(os.path.join(out, "latest.json"), "w"), indent=2)
    open(os.path.join(out, ".nojekyll"), "w").close()
    print(f"static site built -> {out}  ({len(names)} figures, cycle {cyc or 'none'})")


if __name__ == "__main__":
    main()
