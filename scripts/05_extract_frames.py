"""Extract the camera frames of selected episodes from the daily tar archives.

Only the listed cameras and time windows are extracted; each tar is read once.

Usage: python scripts/05_extract_frames.py
"""
import json
import subprocess
import tempfile

import pandas as pd

from paths import ARCHIVE, DATA

FRAMES = DATA / "frames"

# local time windows; cameras ordered from downstream to upstream of the incident
EPISODES = {
    "e1_0915_fire_EB": {
        "window": ("2026-09-15 17:15", "2026-09-15 18:55"),
        "cameras": ["1261", "1260", "1259", "1258", "1256", "1195"],
    },
    "e2_0916_7thst_EB": {
        "window": ("2026-09-16 05:20", "2026-09-16 07:20"),
        "cameras": ["785", "784", "782", "780", "778", "776", "774", "772", "771", "770", "769"],
    },
    "e3_0917_exit138_WB": {
        "window": ("2026-09-17 10:10", "2026-09-17 13:15"),
        "cameras": ["769", "770", "771"],
    },
}


def load_manifests():
    rows = []
    for f in sorted(DATA.glob("az511/camera_manifest_*.jsonl")):
        day = f.stem.split("_")[-1]
        for line in open(f):
            r = json.loads(line)
            if r["download_status"] == "saved":
                rows.append((day, r["camera_id"], r["view_id"], r["collected_at_utc"], r["relative_path"]))
    m = pd.DataFrame(rows, columns=["tar_day", "camera_id", "view_id", "t_utc", "relative_path"])
    m["t_local"] = pd.to_datetime(m.t_utc).dt.tz_convert("America/Phoenix").dt.tz_localize(None)
    return m


def main():
    m = load_manifests()
    picks = []
    for name, ep in EPISODES.items():
        sel = m[m.camera_id.isin(ep["cameras"]) & m.t_local.between(*ep["window"])].copy()
        sel["episode"] = name
        picks.append(sel)
    picks = pd.concat(picks)
    FRAMES.mkdir(parents=True, exist_ok=True)
    picks.to_csv(FRAMES / "frames_index.csv", index=False)
    for day, g in picks.groupby("tar_day"):
        members = sorted(set("camera/" + g.relative_path))
        tar = ARCHIVE / day / f"tits_i10_{day}.tar"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("\n".join(members) + "\n")
        print(f"{tar.name}: extracting {len(members)} frames", flush=True)
        subprocess.run(["tar", "-xf", str(tar), "-C", str(FRAMES), "-T", fh.name], check=True)
    print(picks.groupby("episode").size().to_string())


if __name__ == "__main__":
    main()
