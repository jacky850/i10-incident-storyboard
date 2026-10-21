"""Story sheets: one row per camera from downstream (top) to upstream (bottom), one column per moment.

Row labels give each camera's role in the story (downstream clear, site, queue) and the minutes the
connected queue arrived and left (out/story_cameras.csv). Tile borders show INRIX speed over the historic
average on the camera's segment: red < 0.5, orange < 0.7, blue otherwise. Frames come straight from the tars.

Usage: python scripts/09_story_sheets.py
"""
import io
import json
import sys
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
screen = __import__("03_screen_incidents")
from tarindex import FrameStore  # noqa: E402

TILE_W, TILE_H, PAD, LABEL_W, HEAD_H = 400, 225, 5, 300, 80
ROLE = {"clear": "DOWNSTREAM", "site": "SITE", "queue_hit": "QUEUE"}

STORIES = {
    "s1_0916_0538_EB_tunnel": {
        "episode": 9, "dir": "E", "s_km": 36.72,
        "title": "Wed Sep 16, I-10 EB: crash blocking several lanes near 7th/16th St, ERS 05:38 (sunrise 06:07)",
        "cameras": ["787", "786", "785", "784", "782", "780", "778", "776", "774", "772", "771", "770"],
        "times": ["05:30", "05:38", "05:42", "05:46", "05:50", "06:00", "06:15", "06:30", "06:45", "07:00"],
    },
    "s2_0915_1328_EB_tunnel": {
        "episode": 13, "dir": "E", "s_km": 35.59,
        "title": "Tue Sep 15, I-10 EB tunnel: queue from 13:28 while a disabled vehicle near 7th Ave is cleared (ERS 12:52-13:42)",
        "cameras": ["787", "786", "785", "784", "782", "780", "778", "776", "774", "772", "771", "770", "769"],
        "times": ["13:15", "13:28", "13:34", "13:40", "13:46", "13:52", "13:58", "14:04", "14:12", "14:25"],
    },
    "s3_0913_1153_EB_3rdst": {
        "episode": 6, "dir": "E", "s_km": 36.05,
        "title": "Sun Sep 13, I-10 EB near 3rd St: infrastructure damage, ERS 11:53",
        "cameras": ["787", "786", "785", "784", "782", "780", "778", "776", "774", "772"],
        "times": ["11:45", "11:53", "11:58", "12:03", "12:10", "12:18", "12:25", "12:35", "12:50", "13:05"],
    },
}


def border(ratio):
    if pd.isna(ratio):
        return (150, 150, 150)
    return (213, 94, 0) if ratio < 0.5 else (230, 159, 0) if ratio < 0.7 else (0, 114, 178)


def main():
    chains = screen.load_chains(screen.INRIX_SPEED / "metadata.csv")
    speed = pd.read_parquet(screen.DATA / "speed_0909_0917.parquet")
    speed["ratio"] = speed.speed_kmh / speed.hist_kmh
    speed = speed.set_index(["xd", "t_local"]).ratio
    cams = screen.camera_table(chains)
    names = pd.read_csv(screen.CAMS, dtype={"ID": str}).set_index("ID").Location
    roles = pd.read_csv(screen.OUT / "story_cameras.csv", dtype={"camera_id": str},
                        parse_dates=["queue_arrives", "queue_leaves"])
    rows = []
    for f in sorted((screen.DATA / "az511").glob("camera_manifest_*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r["download_status"] == "saved":
                rows.append((r["camera_id"], r["collected_at_utc"], r["relative_path"]))
    frames = pd.DataFrame(rows, columns=["camera_id", "t_utc", "path"])
    frames["t"] = pd.to_datetime(frames.t_utc).dt.tz_convert("America/Phoenix").dt.tz_localize(None)
    store = FrameStore()
    font, small = ImageFont.load_default(size=22), ImageFont.load_default(size=16)
    out = screen.OUT / "story_sheets"
    out.mkdir(exist_ok=True)

    for name, st in STORIES.items():
        chain = chains[st["dir"]]
        day = frames[frames.camera_id == st["cameras"][0]].t.dt.date
        ep_roles = roles[roles.episode == st["episode"]].set_index("camera_id")
        times = [pd.Timestamp(f"{pd.Timestamp(st['title'].split(',')[0][4:] + ' 2026'):%Y-%m-%d} {t}") for t in st["times"]]
        W = LABEL_W + len(times) * (TILE_W + PAD)
        sheet = Image.new("RGB", (W, HEAD_H + len(st["cameras"]) * (TILE_H + PAD)), "white")
        d = ImageDraw.Draw(sheet)
        d.text((10, 8), st["title"], fill="black", font=font)
        for j, t in enumerate(times):
            d.text((LABEL_W + j * (TILE_W + PAD) + 8, 48), f"{t:%H:%M}", fill="black", font=font)
        for i, cam in enumerate(st["cameras"]):
            y = HEAD_H + i * (TILE_H + PAD)
            s = cams[(cams.camera_id == cam) & (cams.dir == st["dir"])].s_km.iloc[0]
            xd = chain[(chain.s0_km <= s) & (chain.s1_km > s)].xd.iloc[0]
            role = ROLE.get(ep_roles.role.get(cam, ""), "") if cam in ep_roles.index else ""
            timeline = ""
            if cam in ep_roles.index and pd.notna(ep_roles.queue_arrives.get(cam)):
                timeline = f"queue {ep_roles.queue_arrives[cam]:%H:%M}-{ep_roles.queue_leaves[cam]:%H:%M}"
            label = f"{cam} {names[cam].replace('I-10 @', '')}\n{s - st['s_km']:+.2f} km\n{role}\n{timeline}"
            d.multiline_text((8, y + 8), label, fill="black", font=small, spacing=5)
            f = frames[frames.camera_id == cam]
            for j, t in enumerate(times):
                x = LABEL_W + j * (TILE_W + PAD)
                gap = (f.t - t).abs()
                if f.empty or gap.min() > pd.Timedelta(minutes=3):
                    d.rectangle([x, y, x + TILE_W, y + TILE_H], fill=(220, 220, 220))
                    continue
                fr = f.loc[gap.idxmin()]
                ratio = speed.get((xd, fr.t.floor("min")), float("nan"))
                img = Image.open(io.BytesIO(store.read(fr.path))).convert("RGB").resize((TILE_W - 8, TILE_H - 8))
                d.rectangle([x, y, x + TILE_W, y + TILE_H], fill=border(ratio))
                sheet.paste(img, (x + 4, y + 4))
                tag = f"{fr.t:%H:%M:%S}  {ratio:.2f}" if pd.notna(ratio) else f"{fr.t:%H:%M:%S}"
                d.rectangle([x + 4, y + TILE_H - 26, x + 170, y + TILE_H - 4], fill="black")
                d.text((x + 8, y + TILE_H - 25), tag, fill="white", font=small)
        path = out / f"{name}.jpg"
        sheet.save(path, quality=82)
        print(path, sheet.size)


if __name__ == "__main__":
    main()
