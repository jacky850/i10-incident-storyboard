"""Time-space speed plots for screened episodes, with camera positions and incident records.

Colour is INRIX speed over the historic average for that hour and weekday. The vertical axis
is distance along the direction of travel, so traffic moves up the page and a queue tail
grows downward.

Usage: python scripts/04_plot_candidates.py <row index in out/incident_screen.csv> [...]
"""
import sys
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
screen = __import__("03_screen_incidents")

DIR_NAME = {"E": "EB", "W": "WB"}


def plot(ep, chains, speed, cams, path):
    chain = chains[ep.dir]
    t0 = ep.start - pd.Timedelta(minutes=60)
    t1 = min(ep.end, ep.start + pd.Timedelta(hours=4)) + pd.Timedelta(minutes=60)
    lo, hi = ep.s_km - 8, ep.s_km + 2
    seg = chain[(chain.s1_km > lo) & (chain.s0_km < hi)]
    sp = speed[speed.xd.isin(seg.xd) & speed.t_local.between(t0, t1)]
    grid = sp.pivot_table(index="t_local", columns="xd", values="ratio")[seg.xd]

    fig, ax = plt.subplots(figsize=(11, 5.5))
    t_edges = grid.index.append(pd.DatetimeIndex([grid.index[-1] + pd.Timedelta(minutes=1)]))
    for x, r in zip(grid.columns, seg.itertuples()):
        ax.pcolormesh(t_edges, [r.s0_km, r.s1_km], grid[[x]].T.values, cmap="RdYlBu", vmin=0.2, vmax=1.1,
                      shading="flat")
    c = cams[(cams.dir == ep.dir) & cams.s_km.between(lo, hi)]
    for r in c.itertuples():
        dead = r.camera_id in screen.DEAD_CAMERAS
        ax.axhline(r.s_km, color="0.3", lw=0.6, ls=":" if dead else "-")
        ax.text(t1, r.s_km, f" {r.camera_id}{' (no feed)' if dead else ''}", va="center", fontsize=7)
    ax.axvspan(ep.start, ep.end, color="k", alpha=0.06)
    ax.plot([ep.start], [ep.s_km], marker="X", ms=12, color="k")
    ax.set_ylim(lo, hi)
    ax.set_xlim(t0, t1)
    ax.set_ylabel(f"km along I-10 {DIR_NAME[ep.dir]} (direction of travel up)")
    title = ep.description if len(ep.description) < 110 else ep.description[:107] + "..."
    ax.set_title(f"{ep.start:%a %b %d %H:%M}  I-10 {DIR_NAME[ep.dir]}  grade {ep.grade}\n{title}", fontsize=9)
    sm = plt.cm.ScalarMappable(cmap="RdYlBu", norm=plt.Normalize(0.2, 1.1))
    fig.colorbar(sm, ax=ax, pad=0.08, label="speed / historic average")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main(rows):
    chains = screen.load_chains(screen.INRIX_SPEED / "metadata.csv")
    speed = pd.read_parquet(screen.DATA / "speed_0909_0917.parquet")
    speed["ratio"] = speed.speed_kmh / speed.hist_kmh
    cams = screen.camera_table(chains)
    eps = pd.read_csv(screen.OUT / "incident_screen.csv", parse_dates=["start", "end"])
    out = screen.OUT / "plots"
    out.mkdir(exist_ok=True)
    for i in rows:
        ep = eps.iloc[i]
        path = out / f"{i:02d}_{ep.start:%m%d_%H%M}_{DIR_NAME[ep.dir]}.png"
        plot(ep, chains, speed, cams, path)
        print(path)


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]])
