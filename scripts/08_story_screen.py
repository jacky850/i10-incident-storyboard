"""Find incidents that tell the full camera story.

The story: a camera at the site sees the incident; cameras upstream see the queue arrive and
leave one after another; cameras downstream see free flow before and after.

For every episode in out/incident_screen.csv and every working camera on the same direction
from 6 km upstream to 3 km downstream, the camera's segment speed (INRIX, over the historic
average) gives a timeline: minutes congested before the record, first and last congested minute
after it. A camera counts as
  site        |d| <= 0.5 km
  queue hit   d < -0.3 km, at least 10 congested minutes after the record, quiet before
  clear       0.3 < d <= 3 km, quiet before and at most 5 congested minutes after
where d is the distance from the incident along the direction of travel (negative = upstream).

Usage: python scripts/08_story_screen.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
screen = __import__("03_screen_incidents")

UP_KM, DOWN_KM, SITE_KM = 6.0, 3.0, 0.5
SLOW = 0.7
PRE_MIN = 30
QUIET_PRE = 0.2   # at most 20% of the pre-record minutes congested


def camera_frames():
    rows = []
    for f in sorted((screen.DATA / "az511").glob("camera_manifest_*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r["download_status"] == "saved":
                rows.append((r["camera_id"], r["collected_at_utc"]))
    m = pd.DataFrame(rows, columns=["camera_id", "t_utc"])
    m["t"] = pd.to_datetime(m.t_utc).dt.tz_convert("America/Phoenix").dt.tz_localize(None)
    return m


def queue_extent(ep, chain, speed, t_pre, t_end):
    """Per minute: km of congestion running upstream from the incident without a free segment."""
    seg = chain[(chain.s0_km <= ep.s_km) & (chain.s1_km > ep.s_km - UP_KM)].iloc[::-1]
    ratio = pd.DataFrame({x: speed[x][t_pre:t_end] for x in seg.xd if x in speed})
    slow = ratio[[x for x in seg.xd if x in ratio]] < SLOW
    start_of = seg.set_index("xd").s0_km
    extent = pd.Series(0.0, index=slow.index)
    reached = pd.Series(True, index=slow.index)
    for x in slow.columns:
        reached &= slow[x]
        extent[reached] = ep.s_km - start_of[x]
    return extent


def timelines(ep, chain, cams, speed, frames):
    t_pre = ep.start - pd.Timedelta(minutes=PRE_MIN)
    t_end = min(ep.end, ep.start + pd.Timedelta(hours=4)) + pd.Timedelta(minutes=60)
    extent = queue_extent(ep, chain, speed, t_pre, t_end)
    c = cams[(cams.dir == ep.dir) & cams.s_km.between(ep.s_km - UP_KM, ep.s_km + DOWN_KM)]
    out = []
    for r in c.itertuples():
        if r.camera_id in screen.DEAD_CAMERAS:
            continue
        n_img = frames[(frames.camera_id == r.camera_id) & frames.t.between(ep.start, t_end)].shape[0]
        if n_img < 0.5 * (t_end - ep.start).total_seconds() / 60 / 1.02:
            continue
        seg = chain[(chain.s0_km <= r.s_km) & (chain.s1_km > r.s_km)]
        if seg.empty:
            continue
        sp = speed.get(seg.xd.iloc[0])
        if sp is None:
            continue
        sp = sp[t_pre:t_end]
        pre, post = sp[:ep.start - pd.Timedelta(minutes=1)], sp[ep.start:]
        d = r.s_km - ep.s_km
        if d < -0.3:
            # upstream: the camera is in this incident's queue when the connected queue reaches past it
            inside = extent[ep.start:] >= -d
            slow_post = post[inside.reindex(post.index, fill_value=False)]
        else:
            slow_post = post[post < SLOW]
        out.append({
            "camera_id": r.camera_id, "location": r.location, "d_km": round(d, 2),
            "pre_slow_share": round(float((pre < SLOW).mean()), 2) if len(pre) else np.nan,
            "post_slow_min": int(len(slow_post)),
            "queue_arrives": slow_post.index.min() if len(slow_post) else pd.NaT,
            "queue_leaves": slow_post.index.max() if len(slow_post) else pd.NaT,
            "min_ratio": round(float(post.min()), 2) if len(post) else np.nan,
        })
    t = pd.DataFrame(out)
    if t.empty:
        return t
    quiet = t.pre_slow_share <= QUIET_PRE
    t["role"] = np.select(
        [t.d_km.abs() <= SITE_KM,
         (t.d_km < -0.3) & quiet & (t.post_slow_min >= 10),
         (t.d_km > 0.3) & quiet & (t.post_slow_min <= 5)],
        ["site", "queue_hit", "clear"], default="")
    return t.sort_values("d_km", ascending=False)


def main():
    chains = screen.load_chains(screen.INRIX_SPEED / "metadata.csv")
    speed = pd.read_parquet(screen.DATA / "speed_0909_0917.parquet")
    speed["ratio"] = speed.speed_kmh / speed.hist_kmh
    speed = {x: g.set_index("t_local").ratio for x, g in speed.groupby("xd")}
    cams = screen.camera_table(chains)
    frames = camera_frames()
    eps = pd.read_csv(screen.OUT / "incident_screen.csv", parse_dates=["start", "end"])

    summary, detail = [], []
    for i, ep in eps.iterrows():
        t = timelines(ep, chains[ep.dir], cams, speed, frames)
        if t.empty:
            continue
        t.insert(0, "episode", i)
        detail.append(t)
        roles = t.role.value_counts()
        # the site camera itself may sit inside the queue; count it as seeing the incident either way
        hits = t[t.role == "queue_hit"]
        summary.append({
            "episode": i, "start": ep.start, "dir": ep.dir, "daylight": ep.daylight,
            "description": ep.description.split(" / ")[0], "source": ep.source,
            "pre_queue_km": ep.pre_queue_km, "max_queue_km": ep.max_queue_km, "peak_extra_tt_min": ep.peak_extra_tt_min,
            "site": int(roles.get("site", 0)), "queue_hit": int(roles.get("queue_hit", 0)), "clear": int(roles.get("clear", 0)),
            "nearest_site_km": t[t.role == "site"].d_km.abs().min() if roles.get("site", 0) else np.nan,
            "hit_span_km": round(float(hits.d_km.max() - hits.d_km.min()), 2) if len(hits) > 1 else 0.0,
        })
    s = pd.DataFrame(summary)
    s["story"] = (s.site >= 1) & (s.queue_hit >= 2) & (s.clear >= 1) & (s.pre_queue_km < 0.5)
    s["story_day"] = s.story & s.daylight
    s = s.sort_values(["story_day", "story", "queue_hit", "clear", "peak_extra_tt_min"], ascending=False)
    s.to_csv(screen.OUT / "story_screen.csv", index=False)
    pd.concat(detail).to_csv(screen.OUT / "story_cameras.csv", index=False)
    print(f"{len(s)} episodes with working cameras nearby")
    print(f"site + 2 queue cameras + 1 clear downstream, no queue before: {s.story.sum()} ({s.story_day.sum()} in daylight)")
    print(f"relaxing the site camera: {((s.queue_hit >= 2) & (s.clear >= 1) & (s.pre_queue_km < 0.5)).sum()}")


if __name__ == "__main__":
    main()
