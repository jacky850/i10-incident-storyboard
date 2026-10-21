"""Contact sheets: cameras (rows, downstream at top) by moments in time (columns).

Each tile is the frame nearest the column time (within 3 min). Its border shows the INRIX speed over
historic average on the camera's segment at that minute: red < 0.5, orange < 0.7, blue otherwise.

Usage: python scripts/06_contact_sheets.py
"""
import sys
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
screen = __import__("03_screen_incidents")
frames_mod = __import__("05_extract_frames")

TILE_W, TILE_H, PAD, LABEL_W, HEAD_H = 480, 270, 6, 260, 70
FRAMES = frames_mod.FRAMES

SHEETS = {
    "e1_0915_fire_EB": {
        "dir": "E", "s_km": 53.41, "title": "Sep 15 (Tue) I-10 EB Broadway Curve: vehicle fire near US-60, record 17:34",
        "times": ["17:25", "17:36", "17:45", "17:55", "18:05", "18:20", "18:35", "18:50"],
    },
    "e2_0916_7thst_EB": {
        "dir": "E", "s_km": 36.72, "title": "Sep 16 (Wed) I-10 EB near 7th St: crash blocking the exit ramp, record 05:38",
        "times": ["05:30", "05:40", "05:50", "06:00", "06:15", "06:30", "06:50", "07:10"],
    },
    "e3_0917_exit138_WB": {
        "dir": "W", "s_km": 37.70,
        "title": "Sep 17 (Thu) I-10 WB: crash at Exit 138 10:23; secondary crashes 11:22 (near 769) and 12:28",
        "times": ["10:15", "10:45", "11:05", "11:20", "11:30", "11:50", "12:20", "12:45", "13:00"],
    },
}


def border_colour(ratio):
    if pd.isna(ratio):
        return (150, 150, 150)
    if ratio < 0.5:
        return (213, 94, 0)       # Okabe-Ito vermillion
    if ratio < 0.7:
        return (230, 159, 0)      # orange
    return (0, 114, 178)          # blue


def main():
    chains = screen.load_chains(screen.INRIX_SPEED / "metadata.csv")
    speed = pd.read_parquet(screen.DATA / "speed_0909_0917.parquet")
    speed["ratio"] = speed.speed_kmh / speed.hist_kmh
    speed = speed.set_index(["xd", "t_local"]).ratio
    cams = screen.camera_table(chains)
    names = pd.read_csv(screen.CAMS, dtype={"ID": str}).set_index("ID").Location
    idx = pd.read_csv(FRAMES / "frames_index.csv", parse_dates=["t_local"], dtype={"camera_id": str})
    font = ImageFont.load_default(size=22)
    small = ImageFont.load_default(size=18)
    out_dir = screen.OUT / "contact_sheets"
    out_dir.mkdir(exist_ok=True)

    for name, sh in SHEETS.items():
        ep = frames_mod.EPISODES[name]
        day = ep["window"][0][:10]
        chain = chains[sh["dir"]]
        times = [pd.Timestamp(f"{day} {t}") for t in sh["times"]]
        rows = ep["cameras"]
        W = LABEL_W + len(times) * (TILE_W + PAD)
        H = HEAD_H + len(rows) * (TILE_H + PAD)
        sheet = Image.new("RGB", (W, H), "white")
        d = ImageDraw.Draw(sheet)
        d.text((10, 8), sh["title"], fill="black", font=font)
        for j, t in enumerate(times):
            d.text((LABEL_W + j * (TILE_W + PAD) + 8, 40), f"{t:%H:%M}", fill="black", font=font)
        for i, cam in enumerate(rows):
            y = HEAD_H + i * (TILE_H + PAD)
            s = cams[(cams.camera_id == cam) & (cams.dir == sh["dir"])].s_km.iloc[0]
            xd = chain[(chain.s0_km <= s) & (chain.s1_km > s)].xd.iloc[0]
            label = f"{cam}\n{names[cam].replace('I-10 @', '')}\n{s - sh['s_km']:+.1f} km\nfrom incident"
            d.multiline_text((10, y + 10), label, fill="black", font=small, spacing=6)
            fr = idx[(idx.episode == name) & (idx.camera_id == cam)]
            for j, t in enumerate(times):
                x = LABEL_W + j * (TILE_W + PAD)
                gap = (fr.t_local - t).abs()
                if fr.empty or gap.min() > pd.Timedelta(minutes=3):
                    d.rectangle([x, y, x + TILE_W, y + TILE_H], fill=(220, 220, 220))
                    d.text((x + 10, y + 10), "no frame", fill="black", font=small)
                    continue
                f = fr.loc[gap.idxmin()]
                img = Image.open(FRAMES / "camera" / f.relative_path).convert("RGB").resize((TILE_W - 8, TILE_H - 8))
                ratio = speed.get((xd, f.t_local.floor("min")), float("nan"))
                d.rectangle([x, y, x + TILE_W, y + TILE_H], fill=border_colour(ratio))
                sheet.paste(img, (x + 4, y + 4))
                tag = f"{f.t_local:%H:%M:%S}  v/hist {ratio:.2f}" if pd.notna(ratio) else f"{f.t_local:%H:%M:%S}"
                d.rectangle([x + 4, y + TILE_H - 30, x + 250, y + TILE_H - 4], fill=(0, 0, 0))
                d.text((x + 10, y + TILE_H - 29), tag, fill="white", font=small)
        path = out_dir / f"{name}.jpg"
        sheet.save(path, quality=85)
        print(path, sheet.size)


if __name__ == "__main__":
    main()
