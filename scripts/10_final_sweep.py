"""Final sweep: score every episode for the demo.

Adds one signal to the earlier screens: whether an operator moved a camera near the incident. In
every scene we found by eye, the site was seen because an operator turned or zoomed a PTZ camera,
so a move within 1 km, from 20 min before to 40 min after the record, flags a likely scene view.
A move is a drop in correlation between consecutive frames (64 x 36 grey, standardised) below 0.5.

Usage: python scripts/10_final_sweep.py
"""
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
screen = __import__("03_screen_incidents")
from tarindex import FrameStore  # noqa: E402

NEAR_KM = 1.0
BEFORE, AFTER = 20, 40
MOVE_CORR = 0.5


def thumb(store, path):
    img = Image.open(io.BytesIO(store.read(path)))
    img.draft("L", (240, 135))          # decode at reduced size; much faster than a full decode
    a = np.asarray(img.convert("L").resize((64, 36)), dtype=float)
    return (a - a.mean()) / (a.std() + 1e-6)


def ptz_moves(store, frames):
    moves, prev = [], None
    for r in frames.itertuples():
        cur = thumb(store, r.path)
        if prev is not None and float((cur * prev).mean()) < MOVE_CORR:
            moves.append(r.t)
        prev = cur
    return moves


def main():
    eps = pd.read_csv(screen.OUT / "incident_screen.csv", parse_dates=["start", "end"])
    story = pd.read_csv(screen.OUT / "story_screen.csv").set_index("episode")
    chains = screen.load_chains(screen.INRIX_SPEED / "metadata.csv")
    cams = screen.camera_table(chains)
    rows = []
    for f in sorted((screen.DATA / "az511").glob("camera_manifest_*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r["download_status"] == "saved":
                rows.append((r["camera_id"], r["collected_at_utc"], r["relative_path"]))
    frames = pd.DataFrame(rows, columns=["camera_id", "t_utc", "path"])
    frames["t"] = pd.to_datetime(frames.t_utc).dt.tz_convert("America/Phoenix").dt.tz_localize(None)
    frames = frames.sort_values(["camera_id", "t"])
    store = FrameStore()

    out = []
    for i, ep in eps.iterrows():
        near = cams[(cams.dir == ep.dir) & ((cams.s_km - ep.s_km).abs() <= NEAR_KM)
                    & ~cams.camera_id.isin(screen.DEAD_CAMERAS)]
        t0, t1 = ep.start - pd.Timedelta(minutes=BEFORE), ep.start + pd.Timedelta(minutes=AFTER)
        moved = []
        for c in near.itertuples():
            f = frames[(frames.camera_id == c.camera_id) & frames.t.between(t0, t1)]
            if len(f) < 10:
                continue
            m = ptz_moves(store, f)
            if m:
                moved.append(f"{c.camera_id}@{m[0]:%H:%M}({len(m)})")
        st = story.loc[i] if i in story.index else None
        out.append({
            "episode": i, "start": ep.start, "dir": ep.dir, "daylight": ep.daylight, "source": ep.source,
            "description": ep.description.split(" / ")[0], "duration_min": ep.duration_min,
            "pre_queue_km": ep.pre_queue_km, "max_queue_km": ep.max_queue_km, "peak_extra_tt_min": ep.peak_extra_tt_min,
            "site": int(st.site) if st is not None else 0,
            "queue_hit": int(st.queue_hit) if st is not None else 0,
            "clear": int(st.clear) if st is not None else 0,
            "ptz_moves": " ".join(moved),
        })
        print(f"{i:3d} {ep.start:%m-%d %H:%M} {' '.join(moved)}", flush=True)
    s = pd.DataFrame(out)
    s["score"] = (
        2.0 * (s.peak_extra_tt_min.clip(upper=20) / 20)
        + 2.0 * (s.queue_hit.clip(upper=8) / 8)
        + 1.0 * (s.site > 0) + 1.0 * (s.clear > 0)
        + 1.5 * (s.ptz_moves != "")
        + 1.0 * s.daylight
        + 1.0 * (s.pre_queue_km < 0.5)
        + 0.5 * s.source.str.contains("ERS") * s.source.str.contains("INRIX")
    ).round(2)
    s = s.sort_values("score", ascending=False)
    s.to_csv(screen.OUT / "final_sweep.csv", index=False)
    print(f"\n{(s.ptz_moves != '').sum()} of {len(s)} episodes have a camera move within {NEAR_KM} km")


if __name__ == "__main__":
    main()
