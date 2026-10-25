"""Place INRIX XD segments, ERS events and AZ511 cameras on the GMNS links of 15_osm_network.py.

Every placement is a link ID, a position on that link in metres from its start, and a position along the
I-10 mainline path of its direction (path_m, see gmns.py), with the evidence for it and a status:
"ok", or "flag" with the reason. A placement the rules cannot settle is flagged, not guessed.

Writes data/network/
  i10_path.csv        the mainline links of each direction in order, with path_from_m / path_to_m
  xd_to_link.csv      one row per (XD segment, link) overlap
  ers_to_link.csv     one row per ERS event
  camera_to_link.csv  one row per (camera view, direction): the nearest mainline point; the view itself
                      is registered to links in phase 2 step 2
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from gmns import NET, build_paths, load_network, polyline, project, to_xy  # noqa: E402
from paths import DATA  # noqa: E402

XD_OFFSET_MAX_M = 40.0
XD_LEN_RATIO = (0.85, 1.15)
ERS_SEARCH_M = 100.0
ERS_OFFSET_MAX_M = 40.0
RAMP_NEAR_M = 30.0
CAM_RAMP_SEARCH_M = 150.0
DIRS = {"East": "E", "West": "W"}


def write_paths(paths):
    rows = []
    for d, p in paths.items():
        for seq, r in enumerate(p.links.itertuples()):
            rows.append({"dir": d, "seq": seq, "link_id": r.link_id, "path_from_m": round(r.path_from_m, 1),
                         "path_to_m": round(r.path_to_m, 1), "length_m": round(r.len_m, 1), "lanes": r.lanes})
    pd.DataFrame(rows).to_csv(NET / "i10_path.csv", index=False)


def place_xd(paths, chains):
    rows = []
    for d, ch in chains.items():
        p = paths[d]
        for r in ch.itertuples():
            m0, off0, *_ = p.locate(r.lon0, r.lat0)
            m1, off1, *_ = p.locate(r.lon1, r.lat1)
            ratio = (m1 - m0) / (r.len_km * 1000)
            why = []
            if max(off0, off1) > XD_OFFSET_MAX_M:
                why.append(f"end point {max(off0, off1):.0f} m from the path")
            if not XD_LEN_RATIO[0] <= ratio <= XD_LEN_RATIO[1]:
                why.append(f"length on path {ratio:.2f} x INRIX length")
            for lid, a, b in p.pieces(m0, m1):
                rows.append({"xd": r.xd, "dir": d, "link_id": lid, "link_from_m": round(a, 1),
                             "link_to_m": round(b, 1), "share_of_xd": round((b - a) / max(m1 - m0, 1e-6), 3),
                             "path_from_m": round(m0, 1), "path_to_m": round(m1, 1), "xd_len_m": round(r.len_km * 1000, 1),
                             "offset_start_m": round(off0, 1), "offset_end_m": round(off1, 1),
                             "len_ratio": round(ratio, 3), "status": "flag" if why else "ok", "note": "; ".join(why)})
    return pd.DataFrame(rows)


def near_links(link, x, y, radius):
    """[(distance m, link index, metres along the link)] for links within radius, nearest first."""
    out = []
    for i, pts in link.xy.items():
        if (abs(pts[:, 0] - x).min() > radius + 2000) or (abs(pts[:, 1] - y).min() > radius + 2000):
            continue
        s, dist = project(*polyline(pts), x, y)
        if dist <= radius:
            out.append((dist, i, s))
    return sorted(out)


def place_ers(link, paths):
    ers = pd.read_csv(DATA / "ers_events.csv")
    on_path = {d: set(p.links.link_id) for d, p in paths.items()}
    rows = []
    for e in ers.itertuples():
        d = DIRS.get(e.direction)
        x, y = to_xy(e.lon, e.lat)
        near = near_links(link, x, y, ERS_SEARCH_M)
        names_ramp = bool(re.search(r"\bramp\b", e.description, re.I))
        ramps = [(dist, i, s) for dist, i, s in near if str(link.highway[i]).endswith("_link")]
        other = [(dist, i, s) for dist, i, s in near if d and link.link_id[i] in on_path["W" if d == "E" else "E"]]
        pm, off, lid, s = paths[d].locate(e.lon, e.lat) if d else (np.nan, np.nan, None, np.nan)
        kind, why = "mainline", []
        if names_ramp and ramps and ramps[0][0] <= RAMP_NEAR_M:
            _, i, s = ramps[0]
            lid, kind = int(link.link_id[i]), "ramp"
        elif names_ramp:
            why.append("the record names a ramp but no ramp link is within "
                       f"{RAMP_NEAR_M:.0f} m; placed on the mainline")
        if not d:
            why.append(f"direction '{e.direction}' is not East or West")
        elif off > ERS_OFFSET_MAX_M:
            why.append(f"{off:.0f} m from the {d}B mainline")
        if kind == "mainline" and not names_ramp and ramps and ramps[0][0] < off - 10 and ramps[0][0] <= RAMP_NEAR_M:
            why.append(f"a ramp link is nearer ({ramps[0][0]:.0f} m) than the mainline ({off:.0f} m)")
        if kind == "mainline" and other and other[0][0] < off - 10:
            why.append(f"the other direction's mainline is nearer ({other[0][0]:.0f} m)")
        alts = [f"{int(link.link_id[i])}:{dist:.0f}m" for dist, i, _ in near[:5] if int(link.link_id[i]) != lid]
        mp = re.search(r"\(MP (\d+)\)", e.description)
        rows.append({"ers_id": e.ers_id, "dir": d, "kind": kind, "link_id": lid, "s_on_link_m": round(s, 1),
                     "path_m": round(pm, 1), "offset_m": round(off, 1), "mp_text": int(mp.group(1)) if mp else None,
                     "alternatives": ";".join(alts), "status": "flag" if why else "ok", "note": "; ".join(why),
                     "type": e.subtype, "description": e.description})
    return pd.DataFrame(rows)


def camera_views():
    views = {}
    for f in sorted((DATA / "az511").glob("camera_manifest_*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            views[(r["camera_id"], r["view_id"])] = (r["latitude"], r["longitude"], r["location"], r["view_description"])
    rows = []
    for (cam, view), (lat, lon, loc, desc) in sorted(views.items()):
        m = re.match(r"I-10 (\w+) ([\d.]+) @", desc)
        rows.append({"camera_id": cam, "view_id": view, "lat": lat, "lon": lon, "location": loc,
                     "view_dir": m.group(1), "view_mp": float(m.group(2))})
    return pd.DataFrame(rows)


def place_cameras(link, paths):
    rows = []
    for c in camera_views().itertuples():
        x, y = to_xy(c.lon, c.lat)
        ramps = [f"{int(link.link_id[i])}:{dist:.0f}m" for dist, i, _ in near_links(link, x, y, CAM_RAMP_SEARCH_M)
                 if str(link.highway[i]).endswith("_link")]
        for d, p in paths.items():
            pm, off, lid, s = p.locate(c.lon, c.lat)
            rows.append({"camera_id": c.camera_id, "view_id": c.view_id, "location": c.location,
                         "view_dir": c.view_dir, "view_mp": c.view_mp, "dir": d, "link_id": lid,
                         "s_on_link_m": round(s, 1), "path_m": round(pm, 1), "offset_m": round(off, 1),
                         "ramps_within_150m": ";".join(ramps[:6])})
    return pd.DataFrame(rows)


def main():
    link, _ = load_network()
    paths, chains = build_paths(link)
    write_paths(paths)
    xd = place_xd(paths, chains)
    ers = place_ers(link, paths)
    cam = place_cameras(link, paths)
    xd.to_csv(NET / "xd_to_link.csv", index=False)
    ers.to_csv(NET / "ers_to_link.csv", index=False)
    cam.to_csv(NET / "camera_to_link.csv", index=False)

    seg = xd.drop_duplicates("xd")
    print(f"I-10 path: EB {paths['E'].length_m / 1000:.2f} km, {len(paths['E'].links)} links; "
          f"WB {paths['W'].length_m / 1000:.2f} km, {len(paths['W'].links)} links")
    print(f"XD: {len(seg)} segments on {xd.link_id.nunique()} links; flagged {int((seg.status == 'flag').sum())}; "
          f"median end offset {np.median(np.r_[seg.offset_start_m, seg.offset_end_m]):.0f} m, "
          f"length ratio {seg.len_ratio.min():.2f}-{seg.len_ratio.max():.2f}")
    print(f"ERS: {len(ers)} events, {int((ers.kind == 'ramp').sum())} on ramps, flagged {int((ers.status == 'flag').sum())}")
    print(f"cameras: {cam.camera_id.nunique()} views placed in both directions")


if __name__ == "__main__":
    main()
