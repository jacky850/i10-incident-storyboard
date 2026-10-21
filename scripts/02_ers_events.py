"""Collapse AZ511 event snapshots (every ~5 min) into one row per ERS event.

Each snapshot lists the events active at collection time, so an event's observed span is
[first snapshot that lists it, last snapshot that lists it]. The true end lies between the
last snapshot that lists it and the next snapshot, about 5 min later.

Usage: python scripts/02_ers_events.py <az511_dir> <out.csv>
"""
import glob
import json
import sys

import pandas as pd

TZ = "America/Phoenix"


def local(ts):
    return ts.tz_convert(TZ).tz_localize(None)


def main(src_dir, out_path):
    snaps = sorted(glob.glob(f"{src_dir}/events/*/*.json"))
    times = []
    rows = {}
    for f in snaps:
        snap = json.load(open(f))
        t = pd.Timestamp(snap["collected_at_utc"])
        times.append(t)
        for r in snap["records"]:
            e = rows.setdefault(r["ID"], {
                "ers_id": r["ID"], "source_id": r["SourceId"], "first_seen": t, "last_seen": t,
                "type": r["EventType"], "subtype": r["EventSubType"], "severity": r["Severity"],
                "direction": r["DirectionOfTravel"], "lat": r["Latitude"], "lon": r["Longitude"],
                "reported": pd.Timestamp(r["Reported"], unit="s", tz="UTC"),
                "description": r["Description"], "lanes_affected": r["LanesAffected"],
                "lane_count_max": r["LaneCount"], "full_closure": bool(r["IsFullClosure"]), "n_snapshots": 0,
            })
            e["last_seen"] = max(e["last_seen"], t)
            e["n_snapshots"] += 1
            e["description"] = r["Description"]  # keep the latest wording
            e["severity"] = r["Severity"]
            e["lane_count_max"] = max(e["lane_count_max"] or 0, r["LaneCount"] or 0)
            e["full_closure"] |= bool(r["IsFullClosure"])
    df = pd.DataFrame(rows.values())
    times = pd.Series(sorted(times))
    # end bound: the first snapshot after last_seen in which the event is gone
    nxt = times.searchsorted(df["last_seen"], side="right")
    df["gone_by"] = [times[i] if i < len(times) else pd.NaT for i in nxt]
    for c in ("first_seen", "last_seen", "reported", "gone_by"):
        df[c] = df[c].map(lambda ts: local(ts) if pd.notna(ts) else pd.NaT)
    df["duration_min"] = (df["gone_by"].fillna(df["last_seen"]) - df["reported"]).dt.total_seconds() / 60
    df = df.sort_values("reported").reset_index(drop=True)
    df.to_csv(out_path, index=False)
    print(f"{len(snaps)} snapshots, {len(df)} ERS events")
    print(df.groupby("type").size().to_string())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
