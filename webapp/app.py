#!/usr/bin/env python3
"""
Forecast site: serves the four tabs straight off the run store.

    uvicorn site.app:app --host 0.0.0.0 --port 8000

Routes
    GET /                 tabbed page (Physics | AI | Hybrid | Ensembles)
    GET /api/latest       what is currently published
    GET /fig/<name>.png   rendered figure
    GET /api/series/<model>   raw series as JSON
    GET /healthz          liveness + last sync
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STORE = os.path.join(ROOT, "data", "store")
FIGDIR = os.path.join(STORE, "figures")
SELDIR = os.path.join(STORE, "series")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import site_meta

app = FastAPI(title="Stratosphere forecast charts", docs_url="/api/docs")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

TABS = [("physics", "Physics-based", "ECMWF IFS + EPS, NOAA GFS + GEFS"),
        ("ai", "AI", "ECMWF AIFS Single + AIFS ENS"),
        ("hybrid", "Hybrid (physics + AI)", "every model on one graph"),
        ("ensembles", "Ensembles (members)", "member spaghetti + mean/control"),
        ("ssw", "SSW watch", "% of members easterly for 24 h or more - the reversal probability")]


def latest():
    f = os.path.join(STORE, "latest.json")
    if not os.path.exists(f):
        return {"cycle": None, "figures": {}, "models": {}}
    try:
        return json.load(open(f))
    except Exception:
        return {"cycle": None, "figures": {}, "models": {}}


@app.get("/")
def index(request: Request):
    d = latest()
    cycles = d.get("cycles", {}) or {}
    cyc_list = sorted(cycles.keys(), reverse=True)
    # the menu offers the published views (and only the ones the store has)
    lat_list = [k for k in site_meta.PUBLISHED_KEYS
                if any(k in (cycles.get(c) or {}) for c in cycles)]
    # the site shows ONE view (the default domain and level): the other views
    # are still being checked, so they are not offered yet
    default_view = site_meta.store_key(site_meta.DEFAULT_LAT,
                                       site_meta.DEFAULT_LEVEL)
    figs = []
    for key, title, blurb in TABS:
        img = None
        for c in cyc_list:
            f = (cycles.get(c, {}).get(default_view) or {}).get(key)
            if f:
                img = "/fig/" + f
                break
        figs.append(dict(key=key, title=title, blurb=blurb, img=img))
    cyc = d.get("cycle")
    cycles = d.get("cycles", {}) or {cyc: {"60": {}}}
    cyc_list = sorted(cycles.keys(), reverse=True)
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
    age_h = None
    try:
        upd = datetime.fromisoformat(d.get("updated", ""))
        age_h = int((datetime.now(timezone.utc) - upd).total_seconds() // 3600)
    except Exception:
        pass
    valid_from = valid_to = ""
    try:
        t0 = datetime.strptime(cyc, "%Y%m%d%H")
        valid_from = t0.strftime("%d %b")
        valid_to = (t0 + timedelta(hours=360)).strftime("%d %b")
    except Exception:
        pass
    return templates.TemplateResponse(request, "index.html", {
        "tabs": figs, "cycle": cyc, "when": when,
        "updated": d.get("updated", ""), "level": d.get("level", 10),
        "lat": d.get("lat", 60),
        "lat_list": lat_list,
        "key_labels": {k: site_meta.key_label(k) for k in lat_list},
        "default_key": site_meta.store_key(site_meta.DEFAULT_LAT,
                                            site_meta.DEFAULT_LEVEL),
        "cycle_list": cyc_list, "cycle_labels": labels,
        "cycles_json": json.dumps(cycles),
        "age_hours": age_h, "valid_from": valid_from, "valid_to": valid_to,
        "sources": site_meta.SOURCES, "partial_badge": partial_badge,
    })


@app.get("/api/latest")
def api_latest():
    return JSONResponse(latest())


@app.get("/healthz")
def healthz():
    st = os.path.join(STORE, "status.json")
    s = json.load(open(st)) if os.path.exists(st) else {}
    return {"ok": True, "now": datetime.now(timezone.utc).isoformat(),
            "last_sync": s.get("last_sync"), "rendered": s.get("rendered")}


@app.get("/fig/{name}")
def fig(name: str):
    p = os.path.join(FIGDIR, os.path.basename(name))
    if not os.path.exists(p):
        raise HTTPException(404, "no such figure")
    return FileResponse(p, media_type="image/png" if name.endswith(".png") else "application/pdf")


@app.get("/api/series/{model}")
def api_series(model: str, level: int = 10):
    d = latest()
    cyc = d.get("cycle")
    if not cyc:
        raise HTTPException(404, "no cycle published yet")
    p = os.path.join(SELDIR, f"{model}_{cyc}_u{level}.npz")
    if not os.path.exists(p):
        raise HTTPException(404, "no series for that model")
    z = np.load(p, allow_pickle=True)
    out = {"model": model, "cycle": cyc, "level": level,
           "steps": [int(s) for s in z["steps"]]}
    if "u60" in z:
        out["u60"] = [float(v) for v in z["u60"]]
    if "nh_mean" in z:
        out["nh_mean"] = [float(v) for v in z["nh_mean"]]
    if "zonal" in z:
        out["zonal_shape"] = list(z["zonal"].shape)
    return JSONResponse(out)


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
