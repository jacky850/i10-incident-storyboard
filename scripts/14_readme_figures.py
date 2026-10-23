"""Figures for the README: where the data are, and when.

  docs/figures/coverage_map.png   INRIX XD segments, AZ511 cameras and the six cases on a basemap
  docs/figures/camera_map.png     the camera stretch with every camera's ID
  docs/figures/data_timeline.png  the period each data set covers, the camera outage and the cases

The basemap is Esri World Topographic Map, fetched as Web Mercator tiles and cached in data/tiles/.

Usage: python scripts/14_readme_figures.py
"""
import io
import json
import math
import sys
from pathlib import Path

import matplotlib
import pandas as pd
import requests
from PIL import Image

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from corridor import load_chains  # noqa: E402
from paths import CAMS, DATA, INRIX_SPEED, REPO  # noqa: E402

FIG = REPO / "docs" / "figures"
TILES = DATA / "tiles"
TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}"
ATTRIBUTION = "Basemap: Esri World Topographic Map (Esri, HERE, Garmin, USGS, NGA, EPA, USDA, NPS)"
R = 6378137.0
ADOT_BLUE, SPEED_BLUE, CASE_GREY, DEAD_RED = "#003863", "#2f7fd8", "#7a7a75", "#d55e00"
DEAD_CAMERAS = {"1257"}


def merc(lon, lat):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def tile_xy(lon, lat, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def tile_bounds(x, y, z):
    """Web Mercator metres of a tile's left, right, bottom, top."""
    n = 2 ** z
    size = 2 * math.pi * R / n
    left = -math.pi * R + x * size
    top = math.pi * R - y * size
    return left, left + size, top - size, top


def basemap(ax, lon0, lon1, lat0, lat1, z):
    """Draw the Esri tiles covering the box and set the axes to it, in Web Mercator metres."""
    TILES.mkdir(parents=True, exist_ok=True)
    x0, y0 = (int(v) for v in tile_xy(lon0, lat1, z))
    x1, y1 = (int(v) for v in tile_xy(lon1, lat0, z))
    mosaic = Image.new("RGB", ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256))
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            cached = TILES / f"{z}_{x}_{y}.jpg"
            if not cached.exists():
                r = requests.get(TILE_URL.format(z=z, x=x, y=y), timeout=30,
                                 headers={"User-Agent": "i10-incident-storyboard README figure"})
                r.raise_for_status()
                cached.write_bytes(r.content)
            mosaic.paste(Image.open(io.BytesIO(cached.read_bytes())).convert("RGB"), ((x - x0) * 256, (y - y0) * 256))
    left, _, _, top = tile_bounds(x0, y0, z)
    _, right, bottom, _ = tile_bounds(x1, y1, z)
    ax.imshow(mosaic, extent=(left, right, bottom, top), interpolation="lanczos", alpha=0.85)
    (ax0, ay0), (ax1, ay1) = merc(lon0, lat0), merc(lon1, lat1)
    ax.set_xlim(ax0, ax1)
    ax.set_ylim(ay0, ay1)
    ax.set_axis_off()


def scale_bar(ax, lon, lat, km, label):
    x, y = merc(lon, lat)
    metres = km * 1000 / math.cos(math.radians(lat))      # Web Mercator stretches distances by 1/cos(lat)
    ax.plot([x, x + metres], [y, y], color="k", lw=3, solid_capstyle="butt")
    for f in (0, 0.5, 1):
        ax.plot([x + f * metres] * 2, [y - 250, y + 250], color="k", lw=1.2)
    ax.text(x + metres / 2, y + 600, label, ha="center", va="bottom", fontsize=9)


def north_arrow(ax, lon, lat):
    x, y = merc(lon, lat)
    ax.annotate("N", xy=(x, y + 2500), xytext=(x, y), ha="center", va="top", fontsize=11, fontweight="bold",
                arrowprops={"arrowstyle": "-|>", "color": "k", "lw": 1.5})


def corridor_lines(ax, chains, color, lw):
    for chain in chains.values():
        for r in chain.itertuples():
            (xa, ya), (xb, yb) = merc(r.lon0, r.lat0), merc(r.lon1, r.lat1)
            ax.plot([xa, xb], [ya, yb], color=color, lw=lw, solid_capstyle="round", zorder=3)


def cameras():
    c = pd.read_csv(CAMS, dtype={"ID": str})
    c["x"], c["y"] = zip(*(merc(r.Longitude, r.Latitude) for r in c.itertuples()))
    return c


def draw_cameras(ax, cams, size):
    ok = cams[~cams.ID.isin(DEAD_CAMERAS)]
    ax.scatter(ok.x, ok.y, s=size, facecolor="white", edgecolor="k", lw=0.9, zorder=5)
    dead = cams[cams.ID.isin(DEAD_CAMERAS)]
    ax.scatter(dead.x, dead.y, s=size * 1.6, marker="x", color=DEAD_RED, lw=2, zorder=6)


def cases():
    cfg = json.loads((REPO / "demo_cases.json").read_text())
    return [(c, False) for c in cfg["cases"]] + [(c, True) for c in cfg.get("optional", [])]


def draw_cases(ax, offsets):
    for c, optional in cases():
        x, y = merc(c["record"]["lon"], c["record"]["lat"])
        dx, dy = offsets.get(c["id"][0], (0, 2200))
        colour = CASE_GREY if optional else ADOT_BLUE
        ax.annotate(c["id"][0], xy=(x, y), xytext=(x + dx, y + dy), ha="center", va="center", fontsize=10,
                    fontweight="bold", color="white", zorder=8,
                    bbox={"boxstyle": "circle,pad=0.3", "fc": colour, "ec": "white", "lw": 1.2},
                    arrowprops={"arrowstyle": "-", "color": colour, "lw": 1.4})
        ax.scatter([x], [y], s=28, color=colour, edgecolor="white", lw=1, zorder=7)


def legend_box(ax, items, loc):
    from matplotlib.lines import Line2D
    handles = []
    for kind, label, style in items:
        if kind == "line":
            handles.append(Line2D([0], [0], color=style, lw=4, label=label))
        elif kind == "cam":
            handles.append(Line2D([0], [0], marker="o", ls="", mfc="white", mec="k", ms=7, label=label))
        elif kind == "dead":
            handles.append(Line2D([0], [0], marker="x", ls="", color=DEAD_RED, mew=2, ms=8, label=label))
        else:
            handles.append(Line2D([0], [0], marker="o", ls="", mfc=style, mec="white", ms=11, label=label))
    leg = ax.legend(handles=handles, loc=loc, fontsize=9, framealpha=0.92, borderpad=0.8, labelspacing=0.7)
    leg.set_zorder(10)


def note(ax, lon, lat, text, dx, dy, **kw):
    x, y = merc(lon, lat)
    ax.annotate(text, xy=(x, y), xytext=(x + dx, y + dy), fontsize=kw.pop("fontsize", 9), ha=kw.pop("ha", "center"),
                va="center", zorder=9, bbox={"boxstyle": "round,pad=0.3", "fc": "white", "ec": "none", "alpha": 0.85},
                arrowprops={"arrowstyle": "->", "color": "k", "lw": 1}, **kw)


def coverage_map(chains, cams):
    fig, ax = plt.subplots(figsize=(13, 7.6))
    basemap(ax, -112.50, -111.90, 33.25, 33.54, 12)
    corridor_lines(ax, chains, SPEED_BLUE, 4.5)
    draw_cameras(ax, cams, 26)
    draw_cases(ax, {"A": (-2500, 3200), "B": (1500, 3600), "C": (3800, -1500), "D": (-4200, -1800),
                    "E": (0, 3200), "F": (-1200, -3600)})
    by = cams.set_index("ID")
    note(ax, -112.30, 33.4615, "INRIX XD speeds: I-10 both directions,\n89 segments each way, 63 km, 1 minute",
         0, 5200)
    note(ax, by.Longitude["779"], by.Latitude["779"], "Deck Park tunnel", -3500, -5200)
    note(ax, by.Longitude["789"], by.Latitude["789"], "Mini Stack (I-10 / I-17 / SR-51 / Loop 202)", -2500, 9800)
    note(ax, -112.010, 33.4352, "Sky Harbor Airport", 6500, 6500)
    note(ax, by.Longitude["1258"], by.Latitude["1258"], "Broadway Curve\n(camera 1257 no feed)", -7000, -5200)
    note(ax, by.Longitude["769"], by.Latitude["769"], "AZ511 cameras: 37 views,\n27th Ave to US-60, 21 km", -9000, -6500)
    scale_bar(ax, -112.06, 33.268, 10, "10 km")
    north_arrow(ax, -111.925, 33.505)
    legend_box(ax, [("line", "INRIX XD segments (speed, 1 min)", SPEED_BLUE),
                    ("cam", "AZ511 camera (still about once a minute)", None),
                    ("dead", "Camera 1257, no image on any day", None),
                    ("case", "Main case (A, B, D, E)", ADOT_BLUE),
                    ("case", "Optional case (C, F)", CASE_GREY)], "lower left")
    ax.set_title("Data coverage on I-10, Phoenix, Arizona", fontsize=13, loc="left")
    fig.text(0.99, 0.01, ATTRIBUTION, ha="right", fontsize=7, color="#555")
    fig.tight_layout()
    fig.savefig(FIG / "coverage_map.png", dpi=150)
    plt.close(fig)


def camera_map(chains, cams):
    fig, ax = plt.subplots(figsize=(13, 8.2))
    basemap(ax, -112.140, -111.950, 33.385, 33.478, 14)
    corridor_lines(ax, chains, SPEED_BLUE, 3)
    draw_cameras(ax, cams, 40)
    tunnel = [str(i) for i in range(776, 784)]
    placed = {
        # labels to the north or south of the east-west part, east or west of the curve; metres
        "769": (0, 650), "770": (0, -650), "771": (0, 650), "772": (-250, -700), "773": (-380, 700),
        "774": (0, -650), "775": (380, 700), "784": (0, -650), "785": (0, 650), "786": (-300, -650),
        "787": (300, -650), "788": (0, 650), "789": (650, 300), "790": (650, 0), "1187": (700, 0),
        "1198": (-750, 0), "1254": (-750, 0), "791": (650, 150), "1255": (-750, -100), "792": (700, 150),
        "1170": (0, 650), "1197": (-350, -700), "1195": (0, 650), "1256": (650, -250), "1257": (-650, 450),
        "1258": (750, 0), "1259": (-800, 0), "1260": (750, 50), "1261": (-750, -150),
    }
    for r in cams.itertuples():
        if r.ID in tunnel:
            continue
        dx, dy = placed.get(r.ID, (0, 650))
        colour = DEAD_RED if r.ID in DEAD_CAMERAS else "k"
        ax.text(r.x + dx, r.y + dy, r.ID, fontsize=8.5, ha="center", va="center", color=colour, zorder=9,
                bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": "none", "alpha": 0.8})
    t = cams[cams.ID.isin(tunnel)]
    ax.annotate("776–783: 8 views in the\nDeck Park tunnel (EB and WB)", xy=(t.x.mean(), t.y.mean()),
                xytext=(t.x.mean() - 600, t.y.mean() - 2600), fontsize=9, ha="center", zorder=9,
                bbox={"boxstyle": "round,pad=0.3", "fc": "white", "ec": "none", "alpha": 0.9},
                arrowprops={"arrowstyle": "->", "lw": 1})
    draw_cases(ax, {"A": (-900, 1400), "B": (900, 1500), "C": (1600, -900), "D": (-1500, -900),
                    "E": (0, 1300), "F": (-1900, 700)})
    by = cams.set_index("ID")
    for label, cid, dx, dy in [("Mini Stack", "788", 1800, 1300), ("Sky Harbor Airport", None, 0, 0),
                               ("Broadway Curve", "1258", -2600, -1700)]:
        if cid:
            note(ax, by.Longitude[cid], by.Latitude[cid], label, dx, dy)
    x, y = merc(-112.005, 33.433)
    ax.text(x, y, "Sky Harbor\nAirport", fontsize=10, ha="center", color="#333", style="italic", zorder=4)
    scale_bar(ax, -112.135, 33.390, 2, "2 km")
    north_arrow(ax, -111.958, 33.468)
    legend_box(ax, [("line", "INRIX XD segments", SPEED_BLUE), ("cam", "AZ511 camera (ID beside it)", None),
                    ("dead", "1257: no image on any day", None), ("case", "Main case", ADOT_BLUE),
                    ("case", "Optional case", CASE_GREY)], "lower left")
    ax.set_title("The 37 AZ511 camera views, 27th Ave to US-60 (I-10 MP 142.1–155.2, about 21 km)",
                 fontsize=12, loc="left")
    fig.text(0.99, 0.01, ATTRIBUTION, ha="right", fontsize=7, color="#555")
    fig.tight_layout()
    fig.savefig(FIG / "camera_map.png", dpi=150)
    plt.close(fig)


def timeline():
    rows = [
        ("INRIX XD speeds\n1 min, 189 segments", "2026-08-01", "2026-10-01", SPEED_BLUE),
        ("INRIX incidents\nstart and end per record", "2026-08-01", "2026-10-01", "#56b4e9"),
        ("AZ511 events (ERS)\nsnapshot every ~5 min", "2026-09-09 18:08", "2026-09-17 15:43", "#cc79a7"),
        ("KPHX weather\nevery ~30 min", "2026-09-09 18:08", "2026-09-17 15:43", "#999999"),
        ("AZ511 camera stills\n~1 min, 37 views", "2026-09-09 18:08", "2026-09-17 15:45", ADOT_BLUE),
    ]
    outage = (pd.Timestamp("2026-09-10 05:18"), pd.Timestamp("2026-09-10 09:39"))
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(13, 6.2), gridspec_kw={"height_ratios": [1, 1.25]})
    for ax, lo, hi in [(a1, "2026-07-30", "2026-10-03"), (a2, "2026-09-09 12:00", "2026-09-17 23:59")]:
        for i, (label, s, e, col) in enumerate(rows):
            s, e = pd.Timestamp(s), pd.Timestamp(e)
            ax.barh(i, (e - s).total_seconds() / 86400, left=mdates.date2num(s), height=0.55, color=col)
            ax.text(-0.01, i, label, transform=ax.get_yaxis_transform(), ha="right", va="center", fontsize=8.5)
        ax.set_yticks([])
        ax.set_ylim(len(rows) - 0.4 + (1.2 if ax is a2 else 0), -0.6)
        ax.set_xlim(mdates.date2num(pd.Timestamp(lo)), mdates.date2num(pd.Timestamp(hi)))
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.grid(axis="x", color="#ddd", lw=0.6)
    a1.axvspan(mdates.date2num(pd.Timestamp("2026-09-09 18:08")), mdates.date2num(pd.Timestamp("2026-09-17 15:45")),
               color=ADOT_BLUE, alpha=0.08)
    a1.text(mdates.date2num(pd.Timestamp("2026-09-13 12:00")), -0.45, "camera window (below)", ha="center",
            fontsize=8, color=ADOT_BLUE)
    a1.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0))
    a1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    a1.set_title("Periods each data set covers (local time, America/Phoenix)", loc="left", fontsize=12)
    a2.barh(4, (outage[1] - outage[0]).total_seconds() / 86400, left=mdates.date2num(outage[0]), height=0.55,
            color="white", hatch="////", edgecolor=DEAD_RED, lw=0)
    a2.annotate("collection stopped\nSep 10 05:18–09:39", xy=(mdates.date2num(outage[0]), 4.3),
                xytext=(mdates.date2num(pd.Timestamp("2026-09-10 16:00")), 5.35), fontsize=8, color=DEAD_RED,
                arrowprops={"arrowstyle": "->", "color": DEAD_RED})
    for c, optional in cases():
        t = pd.Timestamp(c["record"]["start"])
        col = CASE_GREY if optional else ADOT_BLUE
        a2.plot([mdates.date2num(t)] * 2, [-0.5, 4.9], color=col, lw=1, ls=":")
        a2.text(mdates.date2num(t), 5.25, c["id"][0], ha="center", va="center", color="white", fontsize=9,
                fontweight="bold", bbox={"boxstyle": "circle,pad=0.25", "fc": col, "ec": "none"})
    a2.xaxis.set_major_locator(mdates.DayLocator())
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%a %b %d"))
    a2.set_title("The camera window, Sep 9 18:08 to Sep 17 15:45, with the six cases", loc="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(FIG / "data_timeline.png", dpi=150)
    plt.close(fig)


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    chains = load_chains(INRIX_SPEED / "metadata.csv")
    cams = cameras()
    coverage_map(chains, cams)
    camera_map(chains, cams)
    timeline()
    print("\n".join(str(p) for p in sorted(FIG.glob("*.png"))))


if __name__ == "__main__":
    main()
