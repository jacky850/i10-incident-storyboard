"""Check and show the GMNS network: an interactive map page and a GeoPackage for QGIS.

Runs topology checks on the network of 15_osm_network.py, then draws every layer of 16 and 17 on one
Leaflet page, with each layer switchable, a popup on every object, a search box, a jump to each
storyboard case and a list of everything flagged.

  Topology checks (data/network/network_checks.csv)
    isolated     links outside the largest connected piece of the freeway network
    dead end     a mainline node with no way in or no way out, more than EDGE_M inside the downloaded box
    lanes        I-10 path links with no lane count or fewer than 2 lanes

Writes
  out/network/network_map.html   one file; needs the internet only for Leaflet and the base map tiles
  out/network/i10_network.gpkg   the same layers for QGIS (only when geopandas is installed)
Both carry INRIX segment geometry and IDs, which are licensed: keep them private.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from gmns import NET, build_paths, load_network, to_lonlat  # noqa: E402
from paths import DATA, INRIX_SPEED, OUT, REPO  # noqa: E402

EDGE_M = 500.0
TEMPLATE = Path(__file__).parent / "network_map_template.html"
DST = OUT / "network"


def lonlat(xy):
    lon, lat = to_lonlat(xy[:, 0], xy[:, 1])
    return [[round(float(a), 6), round(float(b), 6)] for a, b in zip(lat, lon)]


def topology_checks(link, node, paths):
    rows = []
    parent = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for r in link.itertuples():
        parent[find(r.from_node_id)] = find(r.to_node_id)
    comp = link.from_node_id.map(find)
    main = comp.value_counts().idxmax()
    for r in link[comp != main].itertuples():
        rows.append({"check": "isolated", "object": "link", "id": r.link_id,
                     "note": f"not connected to the main freeway network ({r.highway}, {r.len_m:.0f} m)"})

    x0, x1 = node.x_coord.min(), node.x_coord.max()
    y0, y1 = node.y_coord.min(), node.y_coord.max()
    xy = dict(zip(node.node_id, zip(node.x_coord, node.y_coord)))
    outs, ins = set(link.from_node_id), set(link.to_node_id)
    mainline = link[link.highway == "motorway"]
    for nid in set(mainline.from_node_id) | set(mainline.to_node_id):
        lon, lat = xy[nid]
        edge = min((lon - x0) * 92_700, (x1 - lon) * 92_700, (lat - y0) * 111_200, (y1 - lat) * 111_200)
        if edge > EDGE_M and (nid not in outs or nid not in ins):
            rows.append({"check": "dead end", "object": "node", "id": nid,
                         "note": f"mainline node with no link {'out' if nid not in outs else 'in'}, {edge / 1000:.1f} km inside the box"})

    for d, p in paths.items():
        for r in p.links.itertuples():
            if pd.isna(r.lanes) or r.lanes < 2:
                rows.append({"check": "lanes", "object": "link", "id": r.link_id,
                             "note": f"I-10 {d}B link with {'no lane count' if pd.isna(r.lanes) else f'{r.lanes:.0f} lane'} in OSM"})
    return pd.DataFrame(rows, columns=["check", "object", "id", "note"])


def collect():
    link, node = load_network()
    paths, _ = build_paths(link)
    xd = pd.read_csv(NET / "xd_to_link.csv")
    ers = pd.read_csv(NET / "ers_to_link.csv")
    chk = pd.read_csv(NET / "ers_mp_check.csv").set_index("ers_id")
    cam = pd.read_csv(NET / "camera_to_link.csv", dtype={"camera_id": str, "view_id": str})
    gaz = pd.read_csv(NET / "gazetteer.csv")
    src = pd.read_csv(DATA / "ers_events.csv").set_index("ers_id")
    meta = pd.read_csv(Path(INRIX_SPEED) / "metadata.csv").set_index("Segment ID")
    checks = topology_checks(link, node, paths)
    checks.to_csv(NET / "network_checks.csv", index=False)

    on_path = {lid: d for d, p in paths.items() for lid in p.links.link_id}
    km_on = {d: dict(zip(p.links.link_id, p.links.path_from_m / 1000)) for d, p in paths.items()}
    xd_of = xd.groupby("link_id").xd.apply(lambda s: [str(v) for v in s]).to_dict()
    D = {"links": [], "nodes": [], "paths": {}, "xd": [], "ers": [], "cams": [], "gaz": [], "problems": [], "cases": []}

    for r in link.itertuples():
        D["links"].append({
            "id": int(r.link_id), "hw": r.highway, "ln": None if pd.isna(r.lanes) else int(r.lanes),
            "len": round(r.len_m), "way": int(r.osm_way_id), "ref": "" if pd.isna(r.ref) else r.ref,
            "dest": "" if pd.isna(r.destination) else r.destination.replace(";", " / "),
            "jref": "" if pd.isna(r._19) else str(r._19), "path": on_path.get(r.link_id, ""),
            "km": round(km_on[on_path[r.link_id]][r.link_id], 2) if r.link_id in on_path else None,
            "from": int(r.from_node_id), "to": int(r.to_node_id), "xd": xd_of.get(r.link_id, []), "c": lonlat(r.xy)})
    for r in node.itertuples():
        D["nodes"].append({"id": int(r.node_id), "osm": int(r.osm_node_id), "jn": r.highway == "motorway_junction",
                           "ref": "" if pd.isna(r.ref) else str(r.ref), "c": [round(r.y_coord, 6), round(r.x_coord, 6)]})
    for d, p in paths.items():
        D["paths"][d] = {"c": lonlat(p.poly), "km": round(p.length_m / 1000, 2), "n": len(p.links)}

    for x, g in xd.groupby("xd", sort=False):
        r, m = g.iloc[0], meta.loc[x]
        D["xd"].append({"id": str(x), "dir": r.dir, "st": r.status, "note": "" if pd.isna(r.note) else r.note,
                        "links": [int(v) for v in g.link_id], "km": [round(r.path_from_m / 1000, 2), round(r.path_to_m / 1000, 2)],
                        "off": [r.offset_start_m, r.offset_end_m], "ratio": r.len_ratio, "len": r.xd_len_m,
                        "name": "" if pd.isna(m.Intersection) else m.Intersection.replace("  ", " "),
                        "c": [[m["Start Latitude"], m["Start Longitude"]], [m["End Latitude"], m["End Longitude"]]]})

    for r in ers.itertuples():
        s, c = src.loc[r.ers_id], chk.loc[r.ers_id] if r.ers_id in chk.index else None
        e = {"id": int(r.ers_id), "dir": r.dir, "kind": r.kind, "link": None if pd.isna(r.link_id) else int(r.link_id),
             "off": r.offset_m, "km": round(r.path_m / 1000, 2), "st": r.status, "note": "" if pd.isna(r.note) else r.note,
             "type": r.type, "desc": r.description, "first": s.first_seen[:16], "last": s.last_seen[:16],
             "c": [round(s.lat, 6), round(s.lon, 6)]}
        if c is not None:
            mpc = paths[r.dir].point(c.mp_centre_path_m)
            e.update({"mp": int(c.mp_text), "mpev": c.mp_at_event, "err": c.error_m, "wrong": bool(c.wrong_side),
                      "between": "" if pd.isna(c["between"]) else c["between"], "suspect": bool(c.mp_text_suspect),
                      "mpc": [round(float(mpc[1]), 6), round(float(mpc[0]), 6)]})
        D["ers"].append(e)

    for (cid, vid), g in cam.groupby(["camera_id", "view_id"], sort=False):
        r = g.iloc[0]
        D["cams"].append({"id": cid, "view": vid, "loc": r.location, "mp": r.view_mp, "vdir": r.view_dir,
                          "c": [round(r.lat, 6), round(r.lon, 6)],
                          "near": {q.dir: {"link": int(q.link_id), "off": q.offset_m, "km": round(q.path_m / 1000, 2)}
                                   for q in g.itertuples()},
                          "ramps": "" if pd.isna(r.ramps_within_150m) else r.ramps_within_150m})

    for r in gaz[gaz.ref_type != "camera"].itertuples():
        D["gaz"].append({"t": r.ref_type, "label": r.label, "dir": r.dir, "km": round(r.path_m / 1000, 2),
                         "link": int(r.link_id), "c": [r.lat, r.lon]})

    P = D["problems"]
    for x in D["xd"]:
        if x["st"] == "flag":
            P.append({"cat": "XD match", "layer": "xd", "id": x["id"], "text": f"XD {x['id']}: {x['note']}"})
    for e in D["ers"]:
        if e["st"] == "flag":
            P.append({"cat": "ERS placement", "layer": "ers", "id": e["id"], "text": f"ERS {e['id']}: {e['note']}"})
        if e.get("suspect"):
            P.append({"cat": "ERS milepost text", "layer": "ers", "id": e["id"],
                      "text": f"ERS {e['id']}: says MP {e['mp']}, event at MP {e['mpev']}"})
    for r in checks.itertuples():
        P.append({"cat": f"Topology: {r.check}", "layer": "links" if r.object == "link" else "nodes", "id": int(r.id),
                  "text": f"{r.object} {r.id}: {r.note}"})

    cfg = json.loads((REPO / "demo_cases.json").read_text())
    for c in cfg["cases"] + cfg.get("optional", []):
        D["cases"].append({"id": c["id"][0], "title": c["title"], "c": [c["record"]["lat"], c["record"]["lon"]]})
    return D, link, node, paths, checks


def geopackage(D, dst):
    try:
        import geopandas as gpd
        from shapely.geometry import LineString, Point
    except ImportError:
        print("geopandas not installed: GeoPackage skipped")
        return False
    if dst.exists():
        dst.unlink()

    def line(c):
        return LineString([(b, a) for a, b in c])

    def save(rows, geom, name):
        df = pd.DataFrame([{k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items() if k not in ("c", "mpc")}
                           for r in rows])
        gpd.GeoDataFrame(df, geometry=[geom(r) for r in rows], crs="EPSG:4326").to_file(dst, layer=name, driver="GPKG")

    save(D["links"], lambda r: line(r["c"]), "links")
    save(D["nodes"], lambda r: Point(r["c"][1], r["c"][0]), "nodes")
    save([{"dir": d, "km": p["km"], "c": p["c"]} for d, p in D["paths"].items()], lambda r: line(r["c"]), "i10_path")
    save(D["xd"], lambda r: line(r["c"]), "inrix_xd")
    save(D["ers"], lambda r: Point(r["c"][1], r["c"][0]), "ers_events")
    save([e for e in D["ers"] if "mpc" in e], lambda r: line([r["c"], r["mpc"]]), "ers_mp_check")
    save(D["cams"], lambda r: Point(r["c"][1], r["c"][0]), "cameras")
    save(D["gaz"], lambda r: Point(r["c"][1], r["c"][0]), "gazetteer")
    return True


def main():
    D, link, node, paths, checks = collect()
    DST.mkdir(parents=True, exist_ok=True)
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(D, separators=(",", ":")))
    (DST / "network_map.html").write_text(html)
    print(f"network_map.html: {len(D['links'])} links, {len(D['nodes'])} nodes, {len(D['xd'])} XD, {len(D['ers'])} ERS, "
          f"{len(D['cams'])} cameras, {len(D['gaz'])} gazetteer entries; {len(D['problems'])} items to check; "
          f"{(DST / 'network_map.html').stat().st_size / 1e6:.1f} MB")
    print("topology checks:", checks.check.value_counts().to_dict() or "none")
    if geopackage(D, DST / "i10_network.gpkg"):
        print("i10_network.gpkg written")


if __name__ == "__main__":
    main()
