"""The GMNS network of 15_osm_network.py, and the I-10 mainline path along it in each direction.

Positions on a path are metres from the path's first point in the direction of travel ("path_m"),
the GMNS counterpart of the INRIX chain's s_km in corridor.py. Geometry uses a local flat projection
(metres east and north of LAT0, LON0), accurate to well under 0.1% across the study area.
"""
import numpy as np
import pandas as pd

from corridor import load_chains
from paths import DATA, INRIX_SPEED

NET = DATA / "network"
LAT0, LON0 = 33.40, -112.15
M_PER_DEG = 6_371_000.0 * np.pi / 180
COS0 = np.cos(np.radians(LAT0))
GUIDE_MAX_M = 120.0


def to_xy(lon, lat):
    return (np.asarray(lon) - LON0) * M_PER_DEG * COS0, (np.asarray(lat) - LAT0) * M_PER_DEG


def to_lonlat(x, y):
    return np.asarray(x) / (M_PER_DEG * COS0) + LON0, np.asarray(y) / M_PER_DEG + LAT0


def wkt_xy(wkt):
    pts = np.array([p.split() for p in wkt[wkt.index("(") + 1:-1].split(",")], dtype=float)
    x, y = to_xy(pts[:, 0], pts[:, 1])
    return np.column_stack([x, y])


def load_network():
    link = pd.read_csv(NET / "link.csv")
    node = pd.read_csv(NET / "node.csv")
    link["xy"] = link.geometry.map(wkt_xy)
    link["len_m"] = link.xy.map(lambda p: float(np.hypot(*np.diff(p, axis=0).T).sum()))
    return link, node


def project(poly, cum, x, y):
    """Nearest point of polyline poly (n x 2, cumulative length cum) to (x, y): (position m, distance m)."""
    a, b = poly[:-1], poly[1:]
    d = b - a
    seg2 = (d ** 2).sum(1)
    seg2[seg2 == 0] = 1e-9
    t = np.clip(((x - a[:, 0]) * d[:, 0] + (y - a[:, 1]) * d[:, 1]) / seg2, 0, 1)
    px, py = a[:, 0] + t * d[:, 0], a[:, 1] + t * d[:, 1]
    dist = np.hypot(px - x, py - y)
    i = int(np.argmin(dist))
    return float(cum[i] + t[i] * np.sqrt(seg2[i])), float(dist[i])


def polyline(points):
    cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(points, axis=0).T))])
    return points, cum


class Path:
    """The I-10 mainline in one direction, as an ordered list of GMNS links."""

    def __init__(self, direction, links):
        self.dir = direction
        self.links = links.reset_index(drop=True)
        self.links["path_from_m"] = np.concatenate([[0], np.cumsum(self.links.len_m)[:-1]])
        self.links["path_to_m"] = self.links.path_from_m + self.links.len_m
        pts = [self.links.xy[0]] + [p[1:] for p in self.links.xy[1:]]
        self.poly, self.cum = polyline(np.vstack(pts))
        self.length_m = float(self.cum[-1])

    def locate(self, lon, lat):
        """(path_m, offset_m, link_id, s_on_link_m) of the nearest point of the path."""
        x, y = to_xy(lon, lat)
        pm, off = project(self.poly, self.cum, x, y)
        return (pm, off) + self.at(pm)

    def at(self, path_m):
        i = int(np.searchsorted(self.links.path_to_m.values, path_m, side="left"))
        i = min(i, len(self.links) - 1)
        r = self.links.iloc[i]
        return int(r.link_id), float(path_m - r.path_from_m)

    def point(self, path_m):
        x = np.interp(path_m, self.cum, self.poly[:, 0])
        y = np.interp(path_m, self.cum, self.poly[:, 1])
        return to_lonlat(x, y)

    def pieces(self, m0, m1):
        """Links overlapping path interval [m0, m1], with the overlap in each link's own metres."""
        L = self.links
        hit = L[(L.path_to_m > m0) & (L.path_from_m < m1)]
        return [(int(r.link_id), max(m0, r.path_from_m) - r.path_from_m, min(m1, r.path_to_m) - r.path_from_m)
                for r in hit.itertuples()]


def build_paths(link):
    """Walk the I-10 mainline links in each direction, steered by the INRIX chain of that direction.

    The walk starts on the I-10 link nearest the chain's first point that runs the same way as the chain.
    At every node it follows the I-10 link; where I-10 splits (collector-distributor roads also carry the
    I 10 ref) it takes the branch whose points lie closest on average to the INRIX chain. Where no I-10 link
    continues, a non-ramp motorway link close to the chain is accepted. The INRIX chain joins segment end
    points with straight lines, so it cuts corners by up to ~300 m and is used only to choose, not to reject.
    """
    chains = load_chains(INRIX_SPEED / "metadata.csv")
    by_from = link.groupby("from_node_id").groups
    paths = {}
    for d, ch in chains.items():
        lon = np.r_[ch.lon0.values, ch.lon1.values[-1]]
        lat = np.r_[ch.lat0.values, ch.lat1.values[-1]]
        guide, gcum = polyline(np.column_stack(to_xy(lon, lat)))

        def fit(i):
            return float(np.mean([project(guide, gcum, x, y)[1] for x, y in link.xy[i]]))

        def progress(i, end=-1):
            return project(guide, gcum, *link.xy[i][end])[0]

        gx, gy = guide[0]
        forward = [i for i in link.index[link.is_i10] if progress(i) - progress(i, 0) > 0.5 * link.len_m[i]]
        start = min(forward, key=lambda i: project(*polyline(link.xy[i]), gx, gy)[1])
        order, seen = [start], {start}
        while progress(order[-1]) < gcum[-1] - 50:
            nxt = [j for j in by_from.get(link.to_node_id[order[-1]], []) if j not in seen
                   and link.highway[j] == "motorway"]
            i10 = [j for j in nxt if link.is_i10[j]]
            cands = i10 or [j for j in nxt if fit(j) <= GUIDE_MAX_M]
            if not cands:
                break
            j = min(cands, key=fit)
            order.append(j)
            seen.add(j)
        paths[d] = Path(d, link.loc[order, ["link_id", "from_node_id", "to_node_id", "lanes", "ref", "xy", "len_m"]])
    return paths, chains
