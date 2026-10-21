"""Contact sheets of the incident site: daylight crashes with a working camera within 300 m.

Each row is one (incident, camera); columns run from 5 min before the record to 40 min after.
Frames are read straight from the tars through the byte-offset index (scripts/tarindex.py).

Usage: python scripts/07_site_sheets.py
"""
import io
import json
import re
import sys
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from tarindex import FrameStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OFFSETS_MIN = [-5, 0, 5, 10, 20, 40]
SITE_KM = 0.3
ROWS_PER_SHEET = 8
TILE_W, TILE_H, PAD, LABEL_W, HEAD_H = 480, 270, 6, 330, 50
KINDS = re.compile(r"crash|fire|medical|lane", re.I)


def close_cameras(cameras):
    found = []
    for cam, dist, gap in re.findall(r"(\d+)\(([+-][\d.]+)km,\d+img(,gap)?\)", str(cameras)):
        if not gap and cam != "1257" and abs(float(dist)) <= SITE_KM:
            found.append((abs(float(dist)), cam, float(dist)))
    return [(cam, dist) for _, cam, dist in sorted(found)[:2]]


def load_frames():
    rows = []
    for f in sorted((ROOT / "data/az511").glob("camera_manifest_*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r["download_status"] == "saved":
                rows.append((r["camera_id"], r["collected_at_utc"], r["relative_path"]))
    m = pd.DataFrame(rows, columns=["camera_id", "t_utc", "path"])
    m["t"] = pd.to_datetime(m.t_utc).dt.tz_convert("America/Phoenix").dt.tz_localize(None)
    return m


def main():
    ev = pd.read_csv(ROOT / "out/incident_screen.csv", parse_dates=["start"])
    ev = ev[ev.daylight & ev.description.str.contains(KINDS)].sort_values("start")
    rows = [(r, cam, dist) for r in ev.itertuples() for cam, dist in close_cameras(r.cameras)]
    frames = load_frames()
    store = FrameStore()
    font, small = ImageFont.load_default(size=22), ImageFont.load_default(size=17)
    out = ROOT / "out/site_sheets"
    out.mkdir(exist_ok=True)
    listing = []
    for k in range(0, len(rows), ROWS_PER_SHEET):
        chunk = rows[k:k + ROWS_PER_SHEET]
        W = LABEL_W + len(OFFSETS_MIN) * (TILE_W + PAD)
        sheet = Image.new("RGB", (W, HEAD_H + len(chunk) * (TILE_H + PAD)), "white")
        d = ImageDraw.Draw(sheet)
        for j, off in enumerate(OFFSETS_MIN):
            d.text((LABEL_W + j * (TILE_W + PAD) + 8, 14), f"record {off:+d} min", fill="black", font=font)
        for i, (r, cam, dist) in enumerate(chunk):
            y = HEAD_H + i * (TILE_H + PAD)
            n = k + i + 1
            desc = re.sub(r" on I-10 (East|West)bound", "", r.description.split(" / ")[0])
            wrapped = "\n".join(desc[p:p + 30] for p in range(0, min(len(desc), 120), 30))
            d.multiline_text((8, y + 6), f"#{n}  {r.start:%m-%d %H:%M} {r.dir}B\ncam {cam} ({dist:+.2f} km)\n{wrapped}",
                             fill="black", font=small, spacing=4)
            listing.append({"row": n, "start": r.start, "dir": r.dir, "camera": cam, "dist_km": dist,
                            "description": r.description, "sheet": f"site_{k // ROWS_PER_SHEET + 1}.jpg"})
            f = frames[frames.camera_id == cam]
            for j, off in enumerate(OFFSETS_MIN):
                x = LABEL_W + j * (TILE_W + PAD)
                t = r.start + pd.Timedelta(minutes=off)
                gap = (f.t - t).abs()
                if f.empty or gap.min() > pd.Timedelta(minutes=3):
                    d.rectangle([x, y, x + TILE_W, y + TILE_H], fill=(220, 220, 220))
                    continue
                fr = f.loc[gap.idxmin()]
                img = Image.open(io.BytesIO(store.read(fr.path))).convert("RGB").resize((TILE_W, TILE_H))
                sheet.paste(img, (x, y))
                d.rectangle([x, y + TILE_H - 26, x + 110, y + TILE_H], fill="black")
                d.text((x + 6, y + TILE_H - 24), f"{fr.t:%H:%M:%S}", fill="white", font=small)
        path = out / f"site_{k // ROWS_PER_SHEET + 1}.jpg"
        sheet.save(path, quality=82)
        print(path, len(chunk), "rows")
    pd.DataFrame(listing).to_csv(out / "rows.csv", index=False)


if __name__ == "__main__":
    main()
