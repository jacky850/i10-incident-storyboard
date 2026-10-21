"""Screen incidents in the camera window: speed drop, queue extent, and nearby cameras.

Inputs: ERS events (02_ers_events.py), INRIX incidents, INRIX 1-min speed slice (01_slice_speed.py),
XD metadata, camera list and camera manifests.

Speed drop uses INRIX's historic average speed for the same hour and weekday as the baseline.
A segment-minute is congested when speed < (1 - DROP) x historic speed. The queue extent at a
minute is the congested length running upstream from the incident without a free segment
in between. This is a screen, not the Track 1 counterfactual.

Usage: python scripts/03_screen_incidents.py
"""
import glob
import json

import numpy as np
import pandas as pd

from corridor import load_chains, locate
from paths import CAMS, DATA, INRIX_INC, INRIX_SPEED, OUT

WINDOW = (pd.Timestamp("2026-09-09 18:08"), pd.Timestamp("2026-09-17 15:45"))
UPSTREAM_KM, DOWNSTREAM_KM = 5.0, 0.5
DROP = 0.30           # congested: at least 30% below historic speed
MAX_SPAN_MIN = 240    # look at most 4 h after the start
PRE_MIN, POST_MIN = 30, 30
MATCH_KM, MATCH_MIN = 1.5, 30
DAYLIGHT = (6.25, 18.25)   # mid-September Phoenix: sunrise about 06:05, sunset about 18:40
DEAD_CAMERAS = {"1257"}    # placeholder image on all 8 days


def load_inrix_incidents(chains):
    df = pd.read_csv(INRIX_INC)
    df = df[df["Category Ids"].str.contains("Incident")].copy()
    df["start"] = pd.to_datetime(df["Earliest Start Time(local)"].str[:19])
    df["end"] = pd.to_datetime(df["Latest End Time(local)"].str[:19])
    df = df[(df.end >= WINDOW[0]) & (df.start <= WINDOW[1])]
    df["dir"] = df["Direction"].str[0]
    lat_lon = df["Location Lat/Lon"].str.split(" ", expand=True).astype(float)
    df["lat"], df["lon"] = lat_lon[0], lat_lon[1]
    df = df[df["dir"].isin(["E", "W"])]
    return pd.DataFrame({
        "source": "INRIX", "id": df["Incident Id"], "start": df.start, "end": df.end, "dir": df.dir,
        "lat": df.lat, "lon": df.lon, "text": df["Event Text"], "description": df["Description"],
        "severity": df["Severity"].astype(str),
    })


def load_ers():
    df = pd.read_csv(DATA / "ers_events.csv", parse_dates=["reported", "first_seen", "last_seen", "gone_by"])
    df = df[df.type == "accidentsAndIncidents"]
    df = df[(df.gone_by.fillna(df.last_seen) >= WINDOW[0]) & (df.reported <= WINDOW[1])]
    return pd.DataFrame({
        "source": "ERS", "id": df.ers_id.astype(str), "start": df.reported, "end": df.gone_by.fillna(df.last_seen),
        "dir": df.direction.str[0], "lat": df.lat, "lon": df.lon, "text": df.subtype,
        "description": df.description, "severity": df.severity,
    })


def place(events, chains):
    pos = [locate(chains[r.dir], r.lat, r.lon) for r in events.itertuples()]
    events["xd"] = [p[0] for p in pos]
    events["s_km"] = [p[1] for p in pos]
    events["offset_m"] = [p[2] for p in pos]
    return events[events.offset_m < 300].reset_index(drop=True)


def merge_sources(ers, inrix):
    """Pair each ERS event with INRIX incidents on the same direction, near in place and time."""
    ers = ers.copy()
    ers["inrix_ids"] = ""
    used = set()
    for i, e in ers.iterrows():
        near = inrix[(inrix.dir == e.dir) & ((inrix.s_km - e.s_km).abs() <= MATCH_KM)
                     & ((inrix.start - e.start).abs() <= pd.Timedelta(minutes=MATCH_MIN))]
        if len(near):
            ers.at[i, "inrix_ids"] = "|".join(near.id)
            ers.at[i, "start"] = min(e.start, near.start.min())
            used.update(near.id)
    ers["source"] = np.where(ers.inrix_ids != "", "ERS+INRIX", "ERS")
    rest = inrix[~inrix.id.isin(used)].copy()
    rest["inrix_ids"] = rest.id
    return pd.concat([ers, rest], ignore_index=True).sort_values("start").reset_index(drop=True)


def cluster_episodes(ev):
    """Merge records of one episode: same direction, within MATCH_KM, overlapping in time (15 min slack)."""
    ev = ev.sort_values("start").reset_index(drop=True)
    parent = list(range(len(ev)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    slack = pd.Timedelta(minutes=15)
    for i in range(len(ev)):
        for j in range(i + 1, len(ev)):
            a, b = ev.iloc[i], ev.iloc[j]
            if b.start > a.end + slack:
                continue
            if a.dir == b.dir and abs(a.s_km - b.s_km) <= MATCH_KM:
                parent[find(j)] = find(i)
    ev["episode"] = [find(i) for i in range(len(ev))]
    out = []
    for _, g in ev.groupby("episode"):
        # the most downstream ERS record is the likely head of the queue; fall back to INRIX
        lead = g[g.source != "INRIX"] if (g.source != "INRIX").any() else g
        lead = lead.loc[lead.s_km.idxmax()]
        row = lead.copy()
        row["start"], row["end"] = g.start.min(), g.end.max()
        row["n_records"] = len(g)
        row["ers_ids"] = "|".join(g[g.source != "INRIX"].id)
        row["inrix_ids"] = "|".join(i for i in g.inrix_ids if i)
        row["source"] = "+".join(sorted({p for s in g.source for p in s.split("+")}))
        row["description"] = " / ".join(dict.fromkeys(g.description))
        out.append(row)
    return pd.DataFrame(out).sort_values("start").reset_index(drop=True)


def speed_impact(ev, chain, speed):
    t0 = ev.start - pd.Timedelta(minutes=PRE_MIN)
    t1 = min(ev.end, ev.start + pd.Timedelta(minutes=MAX_SPAN_MIN)) + pd.Timedelta(minutes=POST_MIN)
    seg = chain[(chain.s1_km > ev.s_km - UPSTREAM_KM) & (chain.s0_km < ev.s_km + DOWNSTREAM_KM)]
    sp = speed[speed.xd.isin(seg.xd) & speed.t_local.between(t0, t1)]
    if sp.empty:
        return {}
    grid = sp.pivot_table(index="t_local", columns="xd", values="ratio")[seg.xd]
    tt = sp.pivot_table(index="t_local", columns="xd", values="extra_min")[seg.xd]
    congested = grid < (1 - DROP)
    # queue extent: walk upstream from the incident segment while congested
    lens = seg.set_index("xd").len_km
    head = seg[seg.s0_km <= ev.s_km].xd.iloc[-1] if (seg.s0_km <= ev.s_km).any() else seg.xd.iloc[0]
    up_order = list(seg[seg.s0_km <= ev.s_km].xd)[::-1]
    extent = []
    for _, row in congested.iterrows():
        km = 0.0
        for x in up_order:
            if not row.get(x, False):
                break
            km += lens[x]
        extent.append(km)
    extent = pd.Series(extent, index=congested.index)
    pre = extent[extent.index < ev.start - pd.Timedelta(minutes=5)]
    after = extent[extent.index >= ev.start - pd.Timedelta(minutes=5)]
    queued = after > 0
    onset = queued[queued].index.min() if queued.any() else pd.NaT
    reach = up_order and seg.set_index("xd").loc[up_order, "len_km"].sum()
    return {
        "min_ratio_at_site": round(float(grid[head].min()), 2),
        "min_ratio_upstream": round(float(grid.min().min()), 2),
        "pre_queue_km": round(float(pre.max()), 2) if len(pre) else 0.0,
        "queue_onset_min": round((onset - ev.start).total_seconds() / 60) if pd.notna(onset) else np.nan,
        "queue_minutes": int(queued.sum()),
        "max_queue_km": round(float(after.max()), 2),
        "queue_capped": bool(after.max() >= reach - 1e-6) and after.max() > 0,
        "queue_peak_time": after.idxmax() if after.max() > 0 else pd.NaT,
        "peak_extra_tt_min": round(float(tt.sum(axis=1).max()), 1),
    }


def camera_table(chains):
    cams = pd.read_csv(CAMS, dtype={"ID": str})
    rows = []
    for c in cams.itertuples():
        for d in ("E", "W"):
            _, s, off = locate(chains[d], c.Latitude, c.Longitude)
            rows.append({"camera_id": c.ID, "location": c.Location, "dir": d, "s_km": s, "offset_m": off})
    return pd.DataFrame(rows)


def load_manifests():
    rows = []
    for f in sorted(glob.glob(str(DATA / "az511/camera_manifest_*.jsonl"))):
        for line in open(f):
            r = json.loads(line)
            rows.append((r["camera_id"], r["view_id"], r["collected_at_utc"], r["download_status"]))
    m = pd.DataFrame(rows, columns=["camera_id", "view_id", "t_utc", "status"])
    m["t_local"] = pd.to_datetime(m.t_utc).dt.tz_convert("America/Phoenix").dt.tz_localize(None)
    return m


def nearby_cameras(ev, cams, manifests):
    c = cams[(cams.dir == ev.dir) & (cams.s_km > ev.s_km - UPSTREAM_KM) & (cams.s_km < ev.s_km + 1.0)].copy()
    c["dist_km"] = c.s_km - ev.s_km      # negative: upstream of the incident
    c = c.sort_values("dist_km", key=abs)
    t0, t1 = ev.start - pd.Timedelta(minutes=PRE_MIN), ev.end + pd.Timedelta(minutes=POST_MIN)
    m = manifests[manifests.t_local.between(t0, t1)]
    saved = m[m.status == "saved"].groupby("camera_id").size()
    expected = (t1 - t0).total_seconds() / 60 / 1.02     # median capture interval is 1.02 min
    out = []
    for r in c.itertuples():
        n = int(saved.get(r.camera_id, 0))
        # half the expected frames still means about 1 image every 2 min; the 09-10 morning outage gives ~2
        ok = n >= 0.5 * expected and r.camera_id not in DEAD_CAMERAS
        tag = f"{r.camera_id}({r.dist_km:+.2f}km,{n}img{'' if ok else ',gap'})"
        out.append((r.camera_id, r.dist_km, ok, tag))
    site = [o for o in out if abs(o[1]) <= 0.5 and o[2]]
    up = [o for o in out if -UPSTREAM_KM <= o[1] < -0.5 and o[2]]
    return {
        "cams_at_site": len(site),
        "cams_upstream": len(up),
        "nearest_cam_km": round(min((abs(o[1]) for o in out), default=np.nan), 2),
        "cameras": " ".join(o[3] for o in out),
    }


def main():
    chains = load_chains(INRIX_SPEED / "metadata.csv")
    speed = pd.read_parquet(DATA / "speed_0909_0917.parquet")
    speed["ratio"] = speed.speed_kmh / speed.hist_kmh
    lens = pd.concat(chains.values()).set_index("xd").len_km
    km = speed.xd.map(lens)
    speed["extra_min"] = (km / speed.speed_kmh - km / speed.hist_kmh).clip(lower=0) * 60

    ers = place(load_ers(), chains)
    inrix = place(load_inrix_incidents(chains), chains)
    ev = cluster_episodes(merge_sources(ers, inrix))
    ev = ev[(ev.start >= WINDOW[0]) & (ev.start <= WINDOW[1])].reset_index(drop=True)

    cams = camera_table(chains)
    manifests = load_manifests()
    rows = []
    for r in ev.itertuples():
        rows.append({**speed_impact(r, chains[r.dir], speed), **nearby_cameras(r, cams, manifests)})
    ev = pd.concat([ev, pd.DataFrame(rows)], axis=1)
    hour = ev.start.dt.hour + ev.start.dt.minute / 60
    ev["daylight"] = hour.between(*DAYLIGHT)
    ev["duration_min"] = ((ev.end - ev.start).dt.total_seconds() / 60).round()

    has_queue = ev.queue_minutes >= 10
    has_cam = ev.cams_at_site + ev.cams_upstream > 0
    caused = ev.pre_queue_km < 0.5            # no queue at the site before the record
    ev["grade"] = np.select(
        [has_queue & has_cam & caused & ev.daylight, has_queue & has_cam & caused, has_queue & has_cam],
        ["A", "B", "C"], default="-")
    ev = ev.sort_values(["grade", "max_queue_km", "queue_minutes"], ascending=[True, False, False])
    ev = pd.concat([ev[ev.grade != "-"], ev[ev.grade == "-"]])
    cols = ["grade", "source", "n_records", "ers_ids", "inrix_ids", "start", "end", "duration_min", "daylight",
            "dir", "s_km", "description", "text", "severity", "min_ratio_at_site", "min_ratio_upstream",
            "pre_queue_km", "queue_onset_min", "queue_minutes", "max_queue_km", "queue_capped", "queue_peak_time",
            "peak_extra_tt_min", "cams_at_site", "cams_upstream", "nearest_cam_km", "cameras", "lat", "lon"]
    OUT.mkdir(exist_ok=True)
    ev[cols].to_csv(OUT / "incident_screen.csv", index=False)
    print(f"{len(ev)} episodes in the window ({ev.n_records.sum()} records; "
          f"{ev.source.str.contains('ERS').sum()} with ERS, {(ev.source == 'INRIX').sum()} INRIX only)")
    print(f"queue 10+ min: {has_queue.sum()}; plus a working camera: {(has_queue & has_cam).sum()}; "
          f"grade A {(ev.grade == 'A').sum()}, B {(ev.grade == 'B').sum()}, C {(ev.grade == 'C').sum()}")


if __name__ == "__main__":
    main()
