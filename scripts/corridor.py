"""I-10 XD segments ordered along each direction, with a distance axis in km.

s is measured in the direction of travel: for EB, s grows eastward; for WB, westward.
"Upstream" of a point therefore always means smaller s.
"""
import numpy as np
import pandas as pd

R_EARTH_M = 6_371_000.0


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R_EARTH_M * np.arcsin(np.sqrt(a))


def load_chains(metadata_csv, link_tol_m=60.0):
    """Return {"E": DataFrame, "W": DataFrame} of I-10 segments in travel order with s0_km, s1_km."""
    m = pd.read_csv(metadata_csv)
    m = m.rename(columns={
        "Segment ID": "xd", "Direction": "dir", "Start Latitude": "lat0", "End Latitude": "lat1",
        "Start Longitude": "lon0", "End Longitude": "lon1", "Segment Length(Kilometers)": "len_km",
    })
    chains = {}
    for d in ("E", "W"):
        seg = m[m.Road.str.contains("I-10") & (m.dir == d)].reset_index(drop=True)
        # successor: the segment whose start is nearest this segment's end
        succ = {}
        for i, r in seg.iterrows():
            gap = haversine_m(r.lat1, r.lon1, seg.lat0.values, seg.lon0.values)
            gap[i] = np.inf
            j = int(np.argmin(gap))
            if gap[j] < link_tol_m:
                succ[i] = j
        heads = set(range(len(seg))) - set(succ.values())
        if len(heads) != 1:
            raise ValueError(f"I-10 {d}: expected 1 chain head, found {len(heads)}")
        order, i = [], heads.pop()
        while i is not None:
            order.append(i)
            i = succ.get(i)
        if len(order) != len(seg):
            raise ValueError(f"I-10 {d}: chain covers {len(order)} of {len(seg)} segments")
        seg = seg.loc[order].reset_index(drop=True)
        seg["s1_km"] = seg.len_km.cumsum()
        seg["s0_km"] = seg.s1_km - seg.len_km
        chains[d] = seg[["xd", "dir", "lat0", "lon0", "lat1", "lon1", "len_km", "s0_km", "s1_km"]]
    return chains


def locate(chain, lat, lon, n=20):
    """Project a point onto a chain. Returns (xd, s_km, offset_m) of the nearest sample point."""
    best = (None, None, np.inf)
    for r in chain.itertuples():
        f = np.linspace(0, 1, n)
        la, lo = r.lat0 + f * (r.lat1 - r.lat0), r.lon0 + f * (r.lon1 - r.lon0)
        dist = haversine_m(lat, lon, la, lo)
        k = int(np.argmin(dist))
        if dist[k] < best[2]:
            best = (r.xd, r.s0_km + f[k] * r.len_km, float(dist[k]))
    return best
