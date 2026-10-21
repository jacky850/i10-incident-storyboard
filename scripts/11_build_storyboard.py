"""Build the phase-1 storyboard of each case in demo_cases.json.

Phase 1 uses INRIX speeds as the traffic state and the AZ511 frames as evidence; camera registration
and camera-measured density come in phase 2. For each case it writes out/storyboard/<id>/index.html
(data inline, opens from disk) and frames/<camera>/<HHMMSS>.jpg at 480 x 270.

Each segment's speed over the historic average is first smoothed with a trailing 3-minute median
(past data only), then given a state with hysteresis so that values hovering at a threshold do not
flip it every minute:
  slowed:  enters below 0.60, leaves above 0.75   (queue plus slow traffic)
  queued:  enters below 0.45, leaves above 0.55   (traffic under about half the usual speed)
The slowdown and the queue at each minute are the runs of slowed and of queued segments connected to
the incident: upstream without limit inside the plotted range, downstream at most HEAD_KM, so a
bottleneck just past the recorded location is still found.

The slowdown's tail is where drivers first meet slow traffic, so the operator line, Tier 1 and the
forecast use the slowdown; the queue length is reported beside it. The live forecast at minute t uses
only slowdown lengths up to t: a straight-line fit over the last FIT_MIN minutes, extended 5, 15 and
30 min, with a band that widens with the horizon. It is scored against the length observed later.

Usage: python scripts/11_build_storyboard.py [case id ...]
"""
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from corridor import load_chains, locate  # noqa: E402
from paths import CAMS, DATA, INRIX_INC, INRIX_SPEED, OUT, REPO  # noqa: E402
from tarindex import FrameStore  # noqa: E402

SMOOTH_MIN = 3
SLOWED = (0.60, 0.75)      # enter below, leave above
QUEUED = (0.45, 0.55)
HEAD_KM = 1.5
MIN_AHEAD_KM = 0.1         # a camera closer than this to the tail is already at it
SEED_KM = 0.5
FIT_MIN = 10
HORIZONS = (5, 15, 30)
TIER1_KM = 0.4             # 0.25 mi: the Tier 1 queue length proposed in the camnet prototype
NEAR_RECORD_KM = 3.0
MOVE_CORR = 0.5
DEAD_CAMERAS = {"1257"}
KM_PER_MI = 1.609344
TEMPLATE = Path(__file__).parent / "storyboard_template.html"


def cases():
    cfg = json.loads((REPO / "demo_cases.json").read_text())
    return cfg["cases"] + cfg.get("optional", [])


def load_manifest():
    rows = []
    for f in sorted((DATA / "az511").glob("camera_manifest_*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r["download_status"] == "saved":
                rows.append((r["camera_id"], r["view_description"], r["collected_at_utc"], r["relative_path"]))
    m = pd.DataFrame(rows, columns=["camera_id", "view", "t_utc", "path"])
    m["t"] = pd.to_datetime(m.t_utc).dt.tz_convert("America/Phoenix").dt.tz_localize(None)
    return m.sort_values(["camera_id", "t"])


def smooth(grid):
    """Trailing median over SMOOTH_MIN minutes, so no minute uses later data."""
    return grid.rolling(SMOOTH_MIN, min_periods=1).median()


def hysteresis(grid, enter, leave):
    """Per segment: on when the ratio drops below enter, off when it rises above leave; gaps keep the state."""
    r = grid.to_numpy()
    state = np.zeros_like(r, dtype=bool)
    on = np.zeros(r.shape[1], dtype=bool)
    for i, row in enumerate(r):
        on = np.where(row < enter, True, np.where(row > leave, False, on))
        state[i] = on
    return state


def run_series(state, seg, s_inc):
    """Per minute: (head_s, tail_s) of the run of segments in state connected to the incident, or NaN."""
    slow = state
    s0, s1 = seg.s0_km.to_numpy(), seg.s1_km.to_numpy()
    mid = (s0 + s1) / 2
    inc = int(np.clip(np.searchsorted(s1, s_inc), 0, len(seg) - 1))
    seeds = [inc] + [j for j in np.argsort(np.abs(mid - s_inc)) if j != inc and abs(mid[j] - s_inc) <= SEED_KM]
    head, tail = np.full(len(slow), np.nan), np.full(len(slow), np.nan)
    for i, row in enumerate(slow):
        seed = next((j for j in seeds if row[j]), None)
        if seed is None:
            continue
        hi = seed
        while hi + 1 < len(seg) and row[hi + 1] and s1[hi + 1] - s_inc <= HEAD_KM:
            hi += 1
        lo = seed
        while lo - 1 >= 0 and row[lo - 1]:
            lo -= 1
        head[i], tail[i] = s1[hi], s0[lo]
    return head, tail


def forecast(length, times):
    """Live forecasts from past lengths only; returns per-minute dicts and coverage scores."""
    out, hits = [], {h: [] for h in HORIZONS}
    L = pd.Series(length, index=times).fillna(0.0)
    for k, t in enumerate(times):
        past = L.iloc[max(0, k - FIT_MIN):k + 1]
        if past.iloc[-1] <= 0 or len(past) < 3:
            out.append(None)
            continue
        x = np.arange(len(past)) - (len(past) - 1)
        rate, now = np.polyfit(x, past.to_numpy(), 1)          # km per min, km at t
        resid = float(np.std(past.to_numpy() - (rate * x + now)))
        f = {"rate_kmpm": round(float(rate), 3), "now_km": round(float(past.iloc[-1]), 2)}
        for h in HORIZONS:
            mid = max(0.0, past.iloc[-1] + rate * h)
            half = 0.2 + resid * 2 + abs(rate) * h * 0.5 + 0.02 * h
            lo, hi = max(0.0, mid - half), mid + half
            f[str(h)] = [round(lo, 2), round(mid, 2), round(hi, 2)]
            if k + h < len(L):
                hits[h].append(lo <= L.iloc[k + h] <= hi)
        out.append(f)
    score = {str(h): {"n": len(v), "inside": round(float(np.mean(v)), 2) if v else None} for h, v in hits.items()}
    return out, score


def message(f, tail_s, queue_km, cams_up):
    """One plain-English line for the operator at this minute."""
    if f is None:
        return "No slow traffic at the incident."
    mi = f["now_km"] / KM_PER_MI
    mph = f["rate_kmpm"] * 60 / KM_PER_MI
    trend = ("growing about {:.0f} mph".format(abs(mph)) if mph > 0.5
             else "shrinking about {:.0f} mph".format(abs(mph)) if mph < -0.5 else "steady")
    q_mi = queue_km / KM_PER_MI
    if q_mi >= 0.05:
        text = f"Queue about {q_mi:.1f} mi, slow traffic {mi:.1f} mi in all, {trend}."
    else:
        text = f"No queue; slow traffic for {mi:.1f} mi past the scene, {trend}."
    ahead = [c for c in cams_up if c["s_km"] <= tail_s - MIN_AHEAD_KM]
    if ahead and f["rate_kmpm"] > 0.02:
        c = max(ahead, key=lambda c: c["s_km"])
        eta = (tail_s - c["s_km"]) / f["rate_kmpm"]
        when = "within a minute" if eta < 1 else f"in about {eta:.0f} min"
        text += f" At this rate slow traffic reaches camera {c['id']} ({c['location']}) {when}."
    return text


def ptz_moves(store, frames):
    moves, prev = [], None
    for r in frames.itertuples():
        img = Image.open(io.BytesIO(store.read(r.path)))
        img.draft("L", (240, 135))
        a = np.asarray(img.convert("L").resize((64, 36)), dtype=float)
        a = (a - a.mean()) / (a.std() + 1e-6)
        if prev is not None and float((a * prev).mean()) < MOVE_CORR:
            moves.append(f"{r.t:%Y-%m-%dT%H:%M:%S}")
        prev = a
    return moves


def nearby_records(case, chain, s_inc, t0, t1):
    d = case["direction"][0]
    recs = []
    ers = pd.read_csv(DATA / "ers_events.csv", parse_dates=["reported", "first_seen", "last_seen", "gone_by"])
    ers = ers[(ers.direction.str[0] == d) & (ers.reported <= t1) & (ers.gone_by.fillna(ers.last_seen) >= t0)]
    for r in ers.itertuples():
        _, s, off = locate(chain, r.lat, r.lon)
        if off > 300 or abs(s - s_inc) > NEAR_RECORD_KM:
            continue
        recs.append({"t": f"{r.reported:%Y-%m-%dT%H:%M}", "kind": "ERS", "what": "reported",
                     "text": f"ERS {r.ers_id}: {r.description}", "d_km": round(s - s_inc, 2)})
        if pd.notna(r.gone_by) and r.gone_by <= t1:
            recs.append({"t": f"{r.gone_by:%Y-%m-%dT%H:%M}", "kind": "ERS", "what": "cleared",
                         "text": f"ERS {r.ers_id} no longer listed on AZ511", "d_km": round(s - s_inc, 2)})
    inc = pd.read_csv(INRIX_INC)
    inc["start"] = pd.to_datetime(inc["Earliest Start Time(local)"].str[:19])
    inc["end"] = pd.to_datetime(inc["Latest End Time(local)"].str[:19])
    inc = inc[(inc.Direction.str[0] == d) & (inc.start <= t1) & (inc.end >= t0)].copy()
    lat_lon = inc["Location Lat/Lon"].str.split(" ", expand=True).astype(float)
    inc["lat"], inc["lon"] = lat_lon[0], lat_lon[1]
    for r in inc.itertuples():
        _, s, off = locate(chain, r.lat, r.lon)
        if off > 300 or abs(s - s_inc) > NEAR_RECORD_KM:
            continue
        recs.append({"t": f"{r.start:%Y-%m-%dT%H:%M}", "kind": "INRIX", "what": "start",
                     "text": f"INRIX: {r.Description}", "d_km": round(s - s_inc, 2)})
        if r.end <= t1:
            recs.append({"t": f"{r.end:%Y-%m-%dT%H:%M}", "kind": "INRIX", "what": "end",
                         "text": "INRIX incident ends", "d_km": round(s - s_inc, 2)})
    for f in sorted((DATA / "az511" / "weather").glob("*/*.json")):
        w = json.loads(f.read_text())
        t = pd.Timestamp(w["observation_timestamp"]).tz_convert("America/Phoenix").tz_localize(None)
        if t0 <= t <= t1 and w.get("text_description"):
            recs.append({"t": f"{t:%Y-%m-%dT%H:%M}", "kind": "Weather", "what": "KPHX",
                         "text": f"KPHX: {w['text_description']}", "d_km": None})
    seen, out = set(), []
    for r in sorted(recs, key=lambda r: r["t"]):
        key = (r["t"], r["kind"], r["text"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def chapters(times, length, queue_length, start):
    L = pd.Series(length, index=times).fillna(0.0)
    after = L[L.index >= start - pd.Timedelta(minutes=15)]
    on = after[after > 0]
    if on.empty:
        return []
    onset = on.index[0]
    peak = after.idxmax()
    rec = after[(after.index > peak) & (after <= after.max() / 2)]
    out = [("Onset", onset), ("Peak", peak)]
    Q = pd.Series(queue_length, index=times).fillna(0.0)
    q_after = Q[Q.index >= onset]
    if (q_after > 0).any():
        out.append(("Queue gone", q_after[q_after > 0].index[-1] + pd.Timedelta(minutes=1)))
    if len(rec):
        out.append(("Recovery", rec.index[0]))
        zero = (after[after.index > peak] == 0).rolling(5).sum() == 5
        if zero.any():
            out.append(("Cleared", zero[zero].index[0] - pd.Timedelta(minutes=4)))
    out = [(a, t) for a, t in out if t <= times[-1]]
    return [{"label": a, "t": f"{t:%Y-%m-%dT%H:%M}"} for a, t in sorted(out, key=lambda x: x[1])]


def build(case, chains, speed, cams_all, manifest, store):
    d = case["direction"][0]
    chain = chains[d]
    t0, t1 = (pd.Timestamp(x) for x in case["window"])
    start = pd.Timestamp(case["record"]["start"])
    _, s_inc, _ = locate(chain, case["record"]["lat"], case["record"]["lon"])
    names = cams_all.set_index("ID").Location

    cams = []
    for c in case["cameras"]:
        cid = c["id"]
        row = cams_all[cams_all.ID == cid].iloc[0]
        _, s, _ = locate(chain, row.Latitude, row.Longitude)
        cams.append({"id": cid, "location": names[cid].replace("I-10 @", ""), "s_km": round(s, 3),
                     "d_km": round(s - s_inc, 2), "role": c.get("role", ""), "note": c.get("note", ""),
                     "dead": cid in DEAD_CAMERAS})
    s_lo = min([c["s_km"] for c in cams] + [s_inc]) - 1.0
    s_hi = max([c["s_km"] for c in cams] + [s_inc]) + 1.0
    seg = chain[(chain.s1_km > s_lo) & (chain.s0_km < s_hi)].reset_index(drop=True)

    times = pd.date_range(t0, t1, freq="min")
    sp = speed[speed.xd.isin(seg.xd) & speed.t_local.between(t0, t1)]
    grid = sp.pivot_table(index="t_local", columns="xd", values="ratio").reindex(index=times, columns=seg.xd)
    smoothed = smooth(grid)
    head, tail = run_series(hysteresis(smoothed, *SLOWED), seg, s_inc)
    q_head, q_tail = run_series(hysteresis(smoothed, *QUEUED), seg, s_inc)
    length = np.where(np.isnan(tail), 0.0, head - tail)
    q_length = np.where(np.isnan(q_tail), 0.0, q_head - q_tail)
    fc, score = forecast(length, times)

    up_cams = [c for c in cams if not c["dead"]]
    msgs, tier1 = [], None
    for k, t in enumerate(times):
        msgs.append(message(fc[k], tail[k], q_length[k], up_cams))
        if tier1 is None and t >= start - pd.Timedelta(minutes=15) and length[k] >= TIER1_KM:
            tier1 = t

    def span(inside):
        inside = inside & (times >= start - pd.Timedelta(minutes=5))
        return [f"{times[inside][0]:%H:%M}", f"{times[inside][-1]:%H:%M}"] if inside.any() else None

    out_dir = OUT / "storyboard" / case["id"]
    for c in cams:
        x = seg[(seg.s0_km <= c["s_km"]) & (seg.s1_km > c["s_km"])].xd
        ratio = smoothed[x.iloc[0]] if len(x) else pd.Series(np.nan, index=times)
        c["ratio"] = [None if pd.isna(v) else round(float(v), 2) for v in ratio]
        in_zone = (~np.isnan(tail)) & (tail <= c["s_km"]) & (head >= c["s_km"])
        in_queue = (~np.isnan(q_tail)) & (q_tail <= c["s_km"]) & (q_head >= c["s_km"])
        c["state"] = np.where(in_queue, 2, np.where(in_zone, 1, 0)).tolist()   # 2 queue, 1 slow traffic, 0 neither
        c["queue"], c["slowed"] = span(in_queue), span(in_zone)
        fr = manifest[(manifest.camera_id == c["id"]) & manifest.t.between(t0, t1)]
        c["view"] = fr.view.iloc[0] if len(fr) else "no frames"
        c["frames"] = []
        (out_dir / "frames" / c["id"]).mkdir(parents=True, exist_ok=True)
        for r in fr.itertuples():
            rel = f"frames/{c['id']}/{r.t:%H%M%S}.jpg"
            dst = out_dir / rel
            if not dst.exists():
                img = Image.open(io.BytesIO(store.read(r.path)))
                img.draft("RGB", (480, 270))
                img.convert("RGB").resize((480, 270)).save(dst, quality=72)
            c["frames"].append([f"{r.t:%Y-%m-%dT%H:%M:%S}", rel])
        c["ptz_moves"] = ptz_moves(store, fr) if len(fr) > 1 else []

    records = nearby_records(case, chain, s_inc, t0, t1)
    first_move = min((m for c in cams for m in c["ptz_moves"] if m >= f"{start - pd.Timedelta(minutes=20):%Y-%m-%dT%H:%M}"),
                     default=None)
    data = {
        "case": {k: case[k] for k in ("id", "title", "shows", "direction") if k in case} | {"record": case["record"]},
        "caveats": case.get("caveats", []) + ([case["use"]] if "use" in case else []),
        "window": [f"{t0:%Y-%m-%dT%H:%M}", f"{t1:%Y-%m-%dT%H:%M}"],
        "start": f"{start:%Y-%m-%dT%H:%M}",
        "s_inc": round(s_inc, 3), "s_range": [round(s_lo, 3), round(s_hi, 3)],
        "segments": [[round(a, 3), round(b, 3)] for a, b in zip(seg.s0_km, seg.s1_km)],
        "times": [f"{t:%Y-%m-%dT%H:%M}" for t in times],
        "grid": [[None if pd.isna(v) else round(float(v), 2) for v in row] for row in grid.to_numpy()],
        "head": [None if np.isnan(v) else round(float(v), 3) for v in head],
        "tail": [None if np.isnan(v) else round(float(v), 3) for v in tail],
        "q_head": [None if np.isnan(v) else round(float(v), 3) for v in q_head],
        "q_tail": [None if np.isnan(v) else round(float(v), 3) for v in q_tail],
        "forecast": fc, "messages": msgs, "forecast_score": score,
        "tier1": f"{tier1:%Y-%m-%dT%H:%M}" if tier1 is not None else None,
        "first_ptz_move": first_move,
        "chapters": chapters(times, length, q_length, start),
        "cameras": sorted(cams, key=lambda c: -c["s_km"]),
        "records": records,
        "summary": {"max_slow_km": round(float(length.max()), 2), "max_queue_km": round(float(q_length.max()), 2)},
    }
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    (out_dir / "index.html").write_text(html)
    n = sum(len(c["frames"]) for c in cams)
    jumps = int(np.sum(np.abs(np.diff(np.nan_to_num(tail, nan=s_inc))) > 0.05))
    print(f"{case['id']}: {len(cams)} cameras, {n} frames, slow {data['summary']['max_slow_km']} km, "
          f"queue {data['summary']['max_queue_km']} km, tail moves {jumps}, "
          f"tier1 {data['tier1']}, forecast inside 5/15/30 min: "
          + ", ".join(f"{score[str(h)]['inside']}" for h in HORIZONS))
    return data


def index_page(built):
    rows = "".join(
        f'<li><a href="{d["case"]["id"]}/index.html"><b>{d["case"]["id"][0]}</b> {d["case"]["title"]}</a>'
        f'<span>{d["case"]["shows"]}</span></li>' for d in built)
    html = (TEMPLATE.parent / "storyboard_index.html").read_text().replace("<!--CASES-->", rows)
    (OUT / "storyboard" / "index.html").write_text(html)


def main(ids):
    chains = load_chains(INRIX_SPEED / "metadata.csv")
    speed = pd.read_parquet(DATA / "speed_0909_0917.parquet")
    speed["ratio"] = speed.speed_kmh / speed.hist_kmh
    cams_all = pd.read_csv(CAMS, dtype={"ID": str})
    manifest = load_manifest()
    store = FrameStore()
    built = [build(c, chains, speed, cams_all, manifest, store) for c in cases() if not ids or c["id"] in ids]
    if not ids:
        index_page(built)


if __name__ == "__main__":
    main(sys.argv[1:])
