"""I-10 gazetteer: mileposts, exits, cross streets and cameras as positions on the GMNS mainline path.

The same place is named four ways in the records (the advisor's case book, case 5): an integer milepost
("MP 146"), a cross street ("near N 3rd St"), an exit ("Exit 145") and a camera's decimal milepost
("I-10 145.60 @7th St"). Each becomes an interval or a point on the path of each direction (path_m,
see gmns.py), so a record can be checked against where the physics and the cameras put the event.

  milepost      MP = a + b * path_km per direction, fitted to the 37 camera views' decimal mileposts;
                integer MP n is the interval [n - 0.5, n + 0.5), since ERS rounds to the nearest mile
  exit          the gore of every off-ramp that leaves the path: ramp junction:ref and destination tags
  cross_street  where a named OSM road crosses the path at more than 25 degrees
  camera        each camera view's decimal milepost at its nearest point on the path

Writes data/network/gazetteer.csv and data/network/ers_mp_check.csv: for every ERS record with
"(MP n)", the distance from the centre of MP n to the event's own position, and the exits and cross
streets that lie between them ("wrong side" when there is at least one).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from gmns import NET, build_paths, load_network, to_xy  # noqa: E402
from paths import DATA  # noqa: E402

KM_PER_MI = 1.609344
CROSS_MIN_DEG = 25.0
CROSS_DEDUP_M = 150.0
MP_TEXT_SUSPECT_MI = 1.0
CASE_A_ERS = 1005221


def milepost_fits(cams):
    """Per direction, least squares MP = a + b * path_km over the camera views; returns {dir: (a, b, rms)}."""
    fits = {}
    for d, g in cams.groupby("dir"):
        b, a = np.polyfit(g.path_m / 1000, g.view_mp, 1)
        rms = float(np.sqrt(np.mean((a + b * g.path_m / 1000 - g.view_mp) ** 2)))
        fits[d] = (a, b, rms)
    return fits


def mp_to_path_m(fit, mp):
    a, b, _ = fit
    return (mp - a) / b * 1000


def exits(link, paths):
    rows = []
    ramps = link[link.highway == "motorway_link"]
    for d, p in paths.items():
        at = dict(zip(p.links.from_node_id, p.links.path_from_m))
        for r in ramps[ramps.from_node_id.isin(at)].itertuples():
            ref = getattr(r, "_19") if pd.notna(getattr(r, "_19", np.nan)) else None  # junction:ref
            dest = r.destination if pd.notna(r.destination) else (r._18 if pd.notna(r._18) else "")  # destination:ref
            pm = at[r.from_node_id]
            lid, s = p.at(pm + 0.01)
            lon, lat = p.point(pm)
            rows.append({"ref_type": "exit", "label": (f"Exit {ref}" if ref else "Exit") + (f" · {dest.replace(';', ' / ')}" if dest else ""),
                         "dir": d, "path_m": pm, "link_id": lid, "s_on_link_m": s, "lon": lon, "lat": lat,
                         "source": f"OSM ramp link {r.link_id}"})
    return rows


def crossings(seg_a, seg_b):
    """Intersections of every segment of polyline a with segment b: list of (index in a, t along a, angle)."""
    p, r = seg_a[:-1], np.diff(seg_a, axis=0)
    q, s = seg_b[0], seg_b[1] - seg_b[0]
    rxs = r[:, 0] * s[1] - r[:, 1] * s[0]
    qp = q - p
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[:, 0] * s[1] - qp[:, 1] * s[0]) / rxs
        u = (qp[:, 0] * r[:, 1] - qp[:, 1] * r[:, 0]) / rxs
    hit = np.where((np.abs(rxs) > 1e-9) & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1))[0]
    out = []
    for i in hit:
        cos = abs(r[i] @ s) / (np.linalg.norm(r[i]) * np.linalg.norm(s))
        out.append((i, t[i], float(np.degrees(np.arccos(min(cos, 1.0))))))
    return out


def cross_streets(paths):
    ways = json.load(open(DATA / "osm" / "cross_streets.json"))["elements"]
    ways = [w for w in ways if w["tags"].get("highway") != "motorway" and "geometry" in w]
    rows = []
    for d, p in paths.items():
        seglen = np.hypot(*np.diff(p.poly, axis=0).T)
        found = []
        for w in ways:
            g = np.column_stack(to_xy([pt["lon"] for pt in w["geometry"]], [pt["lat"] for pt in w["geometry"]]))
            for k in range(len(g) - 1):
                for i, t, ang in crossings(p.poly, g[k:k + 2]):
                    if ang >= CROSS_MIN_DEG:
                        found.append((p.cum[i] + t * seglen[i], w["tags"]["name"], w["tags"]["highway"], w["id"]))
        found.sort()
        kept = []
        for pm, name, hw, wid in found:
            if kept and kept[-1][1] == name and pm - kept[-1][0] < CROSS_DEDUP_M:
                continue
            kept.append((pm, name, hw, wid))
        for pm, name, hw, wid in kept:
            lid, s = p.at(pm)
            lon, lat = p.point(pm)
            rows.append({"ref_type": "cross_street", "label": name, "dir": d, "path_m": pm, "link_id": lid,
                         "s_on_link_m": s, "lon": lon, "lat": lat, "source": f"OSM way {wid} ({hw})"})
    return rows


def mileposts(paths, fits):
    rows = []
    for d, p in paths.items():
        a, b, _ = fits[d]
        mp_ends = sorted([a, a + b * p.length_m / 1000])
        for n in range(int(np.ceil(mp_ends[0])), int(np.floor(mp_ends[1])) + 1):
            lo, hi = sorted(mp_to_path_m(fits[d], np.array([n - 0.5, n + 0.5])))
            pm = mp_to_path_m(fits[d], n)
            lid, s = p.at(min(max(pm, 0), p.length_m))
            lon, lat = p.point(pm)
            rows.append({"ref_type": "milepost", "label": f"MP {n}", "dir": d, "path_m": pm,
                         "path_from_m": max(lo, 0), "path_to_m": min(hi, p.length_m), "link_id": lid,
                         "s_on_link_m": s, "lon": lon, "lat": lat, "source": "fit to camera mileposts"})
    return rows


def ers_check(gaz, fits):
    ers = pd.read_csv(NET / "ers_to_link.csv").dropna(subset=["mp_text", "dir"])
    marks = gaz[gaz.ref_type.isin(["exit", "cross_street"])]
    rows = []
    for e in ers.itertuples():
        centre = mp_to_path_m(fits[e.dir], e.mp_text)
        lo, hi = sorted([centre, e.path_m])
        between = marks[(marks.dir == e.dir) & (marks.path_m > lo) & (marks.path_m < hi)]
        a, b, _ = fits[e.dir]
        mp_event = a + b * e.path_m / 1000
        suspect = abs(e.mp_text - mp_event) > MP_TEXT_SUSPECT_MI
        rows.append({"ers_id": e.ers_id, "dir": e.dir, "mp_text": int(e.mp_text),
                     "mp_at_event": round(mp_event, 2), "event_path_m": e.path_m,
                     "mp_centre_path_m": round(centre, 1), "error_m": round(centre - e.path_m, 1),
                     "mp_text_suspect": suspect, "between": " | ".join(between.label.drop_duplicates()),
                     "cross_street_between": bool((between.ref_type == "cross_street").any()) and not suspect,
                     "wrong_side": len(between) > 0 and not suspect, "description": e.description})
    return pd.DataFrame(rows)


def main():
    link, _ = load_network()
    paths, _ = build_paths(link)
    cams = pd.read_csv(NET / "camera_to_link.csv")
    fits = milepost_fits(cams)

    cam_rows = [{"ref_type": "camera", "label": f"{c.view_mp:.2f} @{c.location.split('@')[-1]} (camera {c.camera_id})",
                 "dir": c.dir, "path_m": c.path_m, "link_id": c.link_id, "s_on_link_m": c.s_on_link_m,
                 "lon": paths[c.dir].point(c.path_m)[0], "lat": paths[c.dir].point(c.path_m)[1],
                 "source": f"AZ511 view {c.view_id}"} for c in cams.itertuples()]
    gaz = pd.DataFrame(mileposts(paths, fits) + exits(link, paths) + cross_streets(paths) + cam_rows)
    gaz = gaz.sort_values(["dir", "path_m"]).reset_index(drop=True)
    for c in ("path_m", "path_from_m", "path_to_m", "s_on_link_m"):
        gaz[c] = gaz[c].round(1)
    gaz["lon"], gaz["lat"] = gaz.lon.round(6), gaz.lat.round(6)
    gaz.to_csv(NET / "gazetteer.csv", index=False)

    chk = ers_check(gaz, fits)
    chk.to_csv(NET / "ers_mp_check.csv", index=False)

    for d, (a, b, rms) in fits.items():
        print(f"{d}B milepost fit: MP = {a:.2f} {'+' if b > 0 else '-'} {abs(b):.4f} x path_km "
              f"(1 mi = {abs(1 / b):.3f} km on the path; camera rms {rms:.2f} mi)")
    print("gazetteer:", gaz.groupby(["ref_type", "dir"]).size().to_dict())
    ok = chk[~chk.mp_text_suspect]
    print(f"ERS with '(MP n)': {len(chk)}; MP text more than {MP_TEXT_SUSPECT_MI:.0f} mi from the event: "
          f"{int(chk.mp_text_suspect.sum())} ({', '.join(map(str, chk[chk.mp_text_suspect].ers_id))})")
    print(f"  the other {len(ok)}: centre of MP n vs event median |error| {ok.error_m.abs().median():.0f} m, "
          f"max {ok.error_m.abs().max():.0f} m; a cross street between them in {int(ok.cross_street_between.sum())} "
          f"({ok.cross_street_between.mean():.0%}), an exit gore or a cross street in {int(ok.wrong_side.sum())} "
          f"({ok.wrong_side.mean():.0%})")
    a = chk[chk.ers_id == CASE_A_ERS]
    if len(a):
        r = a.iloc[0]
        print(f"  case A (ERS {CASE_A_ERS}): event at MP {r.mp_at_event}, text says MP {r.mp_text}, "
              f"centre {r.error_m:+.0f} m away, past: {r['between']}")


if __name__ == "__main__":
    main()
