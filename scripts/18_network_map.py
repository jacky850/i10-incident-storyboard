"""Check and show the I-10 network along the camera stretch: an interactive map page and a GeoPackage.

The INRIX export and the GMNS network cover 63 km of I-10; the cameras cover about 21 km (13 mi) of it, from
27th Ave to US-60. The page and the checks keep only that camera stretch, padded by FOCUS_PAD_M along
I-10 at each end, and only I-10: its mainline links and the ramps that lead to or from them. Other
freeways, their ramps and ramps that only pass near I-10 are left out.

  Topology checks (data/network/network_checks.csv), on the kept links only
    isolated     a kept link outside the largest connected piece of the whole freeway network
    dead end     an I-10 mainline node with no link in or no link out
    lanes        I-10 path links with no lane count or fewer than 2 lanes

Every layer of 16 and 17 inside the stretch goes on one Leaflet page, with each layer switchable, a
popup on every object, a search box, a jump to each storyboard case and a list of everything flagged.

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

FOCUS_PAD_M = 1500.0
TEMPLATE = Path(__file__).parent / "network_map_template.html"
DST = OUT / "network"


def lonlat(xy):
    lon, lat = to_lonlat(xy[:, 0], xy[:, 1])
    return [[round(float(a), 6), round(float(b), 6)] for a, b in zip(lat, lon)]


def focus(paths, cam):
    """Per direction: (start m, end m, polyline) of the I-10 path from the first camera to the last, padded."""
    out = {}
    for d, p in paths.items():
        m = cam[cam.dir == d].path_m
        a, b = max(m.min() - FOCUS_PAD_M, 0.0), min(m.max() + FOCUS_PAD_M, p.length_m)
        inside = (p.cum > a) & (p.cum < b)
        ends = [np.array([np.interp(v, p.cum, p.poly[:, 0]), np.interp(v, p.cum, p.poly[:, 1])]) for v in (a, b)]
        out[d] = (a, b, np.vstack([ends[0], p.poly[inside], ends[1]]))
    return out


def keep_links(link, paths, foc):
    """The I-10 path links inside the stretch, and every ramp that leads to or from them.

    Exit ramps are followed forward from the I-10 nodes and entrance ramps backward, along ramp links
    only; the walk stops where a ramp reaches another freeway's mainline, so that freeway's own ramps,
    and ramps that merely pass near I-10 (airport, frontage connectors), are left out.
    """
    path_ids = set()
    for d, p in paths.items():
        a, b, _ = foc[d]
        path_ids |= set(p.links[(p.links.path_to_m > a) & (p.links.path_from_m < b)].link_id)
    main = link[link.link_id.isin(path_ids)]
    i10_nodes = set(main.from_node_id) | set(main.to_node_id)
    all_path = {lid for p in paths.values() for lid in p.links.link_id}
    other = link[(link.highway == "motorway") & ~link.link_id.isin(all_path)]
    stop = (set(other.from_node_id) | set(other.to_node_id)) - i10_nodes
    ramps = link[link.highway.str.endswith("_link")]
    by_from, by_to = ramps.groupby("from_node_id").groups, ramps.groupby("to_node_id").groups
    keep = set()
    for adj, nxt in ((by_from, "to_node_id"), (by_to, "from_node_id")):
        todo, seen = list(i10_nodes), set(i10_nodes)
        while todo:
            n = todo.pop()
            for i in adj.get(n, []):
                keep.add(i)
                m = ramps.at[i, nxt]
                if m not in seen and m not in stop:
                    seen.add(m)
                    todo.append(m)
    return pd.concat([main, ramps.loc[sorted(keep)]])


def neighbour_colours(kept, k=6):
    """Colour index per link: links sharing a node never share a colour, and the k colours are used about equally."""
    by_node = {}
    for r in kept.itertuples():
        for n in (r.from_node_id, r.to_node_id):
            by_node.setdefault(n, []).append(r.link_id)
    nb = {lid: set() for lid in kept.link_id}
    for ids in by_node.values():
        for a in ids:
            nb[a].update(x for x in ids if x != a)
    col, count = {}, [0] * (k + 8)
    for lid in sorted(nb, key=lambda x: (-len(nb[x]), x)):
        used = {col[n] for n in nb[lid] if n in col}
        free = [c for c in range(k) if c not in used] or [c for c in range(k, k + 8) if c not in used]
        col[lid] = min(free, key=lambda c: count[c])
        count[col[lid]] += 1
    return col


def topology_checks(link, kept, paths, foc):
    rows = []
    parent = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for r in link.itertuples():
        parent[find(r.from_node_id)] = find(r.to_node_id)
    main = link.from_node_id.map(find).value_counts().idxmax()
    for r in kept.itertuples():
        if find(r.from_node_id) != main:
            rows.append({"check": "isolated", "object": "link", "id": r.link_id,
                         "note": f"not connected to the rest of the freeway network ({r.highway}, {r.len_m:.0f} m)"})

    outs, ins = set(link.from_node_id), set(link.to_node_id)
    mainline = kept[kept.is_i10]
    for nid in sorted(set(mainline.from_node_id) | set(mainline.to_node_id)):
        if nid not in outs or nid not in ins:
            rows.append({"check": "dead end", "object": "node", "id": nid,
                         "note": f"I-10 mainline node with no link {'out' if nid not in outs else 'in'}"})

    for d, p in paths.items():
        a, b, _ = foc[d]
        for r in p.links[(p.links.path_to_m > a) & (p.links.path_from_m < b)].itertuples():
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

    foc = focus(paths, cam)
    kept = keep_links(link, paths, foc)
    checks = topology_checks(link, kept, paths, foc)
    checks.to_csv(NET / "network_checks.csv", index=False)

    def inside(d, m):
        return d in foc and foc[d][0] <= m <= foc[d][1]

    on_path = {lid: d for d, p in paths.items() for lid in p.links.link_id}
    km_on = {d: dict(zip(p.links.link_id, p.links.path_from_m / 1000)) for d, p in paths.items()}
    xd_of = xd.groupby("link_id").xd.apply(lambda s: [str(v) for v in s]).to_dict()
    mp_fit = {d: np.polyfit(g.path_m, g.view_mp, 1) for d, g in cam.groupby("dir")}
    colour = neighbour_colours(kept)
    D = {"links": [], "nodes": [], "paths": {}, "xd": [], "ers": [], "cams": [], "gaz": [], "problems": [], "cases": []}

    for r in kept.itertuples():
        D["links"].append({
            "id": int(r.link_id), "hw": r.highway, "ln": None if pd.isna(r.lanes) else int(r.lanes),
            "len": round(r.len_m), "way": int(r.osm_way_id), "ref": "" if pd.isna(r.ref) else r.ref,
            "dest": "" if pd.isna(r.destination) else r.destination.replace(";", " / "),
            "jref": "" if pd.isna(r._19) else str(r._19), "path": on_path.get(r.link_id, ""),
            "km": round(km_on[on_path[r.link_id]][r.link_id], 2) if r.link_id in on_path else None,
            "from": int(r.from_node_id), "to": int(r.to_node_id), "xd": xd_of.get(r.link_id, []),
            "col": colour[r.link_id], "c": lonlat(r.xy)})
    used = set(kept.from_node_id) | set(kept.to_node_id)
    for r in node[node.node_id.isin(used)].itertuples():
        D["nodes"].append({"id": int(r.node_id), "osm": int(r.osm_node_id), "jn": r.highway == "motorway_junction",
                           "ref": "" if pd.isna(r.ref) else str(r.ref), "c": [round(r.y_coord, 6), round(r.x_coord, 6)]})
    for d, (a, b, poly) in foc.items():
        D["paths"][d] = {"c": lonlat(poly), "km": round((b - a) / 1000, 2), "from_km": round(a / 1000, 2),
                         "to_km": round(b / 1000, 2), "mp": [round(float(np.polyval(mp_fit[d], v)), 1) for v in (a, b)]}

    for x, g in xd.groupby("xd", sort=False):
        r, m = g.iloc[0], meta.loc[x]
        a, b = foc[r.dir][:2]
        if r.path_to_m < a or r.path_from_m > b:
            continue
        D["xd"].append({"id": str(x), "dir": r.dir, "st": r.status, "note": "" if pd.isna(r.note) else r.note,
                        "links": [int(v) for v in g.link_id], "km": [round(r.path_from_m / 1000, 2), round(r.path_to_m / 1000, 2)],
                        "off": [r.offset_start_m, r.offset_end_m], "ratio": r.len_ratio, "len": r.xd_len_m,
                        "name": "" if pd.isna(m.Intersection) else m.Intersection.replace("  ", " "),
                        "c": [[m["Start Latitude"], m["Start Longitude"]], [m["End Latitude"], m["End Longitude"]]]})

    for r in ers.itertuples():
        if not inside(r.dir, r.path_m):
            continue
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
        if inside(r.dir, r.path_m):
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
    return D, checks


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
    save([{"dir": d, **{k: v for k, v in p.items() if k != "mp"}, "mp_from": p["mp"][0], "mp_to": p["mp"][1]}
          for d, p in D["paths"].items()], lambda r: line(r["c"]), "i10_path")
    save(D["xd"], lambda r: line(r["c"]), "inrix_xd")
    save(D["ers"], lambda r: Point(r["c"][1], r["c"][0]), "ers_events")
    save([e for e in D["ers"] if "mpc" in e], lambda r: line([r["c"], r["mpc"]]), "ers_mp_check")
    save(D["cams"], lambda r: Point(r["c"][1], r["c"][0]), "cameras")
    save(D["gaz"], lambda r: Point(r["c"][1], r["c"][0]), "gazetteer")
    return True


def main():
    D, checks = collect()
    DST.mkdir(parents=True, exist_ok=True)
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(D, separators=(",", ":")))
    (DST / "network_map.html").write_text(html)
    e, w = D["paths"]["E"], D["paths"]["W"]
    print(f"camera stretch: EB km {e['from_km']}-{e['to_km']} (MP {e['mp'][0]}-{e['mp'][1]}, {e['km']} km), "
          f"WB km {w['from_km']}-{w['to_km']} ({w['km']} km)")
    print(f"network_map.html: {len(D['links'])} links, {len(D['nodes'])} nodes, {len(D['xd'])} XD, {len(D['ers'])} ERS, "
          f"{len(D['cams'])} cameras, {len(D['gaz'])} gazetteer entries; {len(D['problems'])} items to check; "
          f"{(DST / 'network_map.html').stat().st_size / 1e6:.1f} MB")
    print("topology checks:", checks.check.value_counts().to_dict() or "none")
    if geopackage(D, DST / "i10_network.gpkg"):
        print("i10_network.gpkg written")


if __name__ == "__main__":
    main()
