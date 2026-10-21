"""Slice the INRIX 1-min XD speed export to the camera archive window.

The camera archive covers 2026-09-09 18:08 to 2026-09-17 15:45 local (America/Phoenix).
We keep 12:00 on 09-09 to 18:00 on 09-17 so every event has a few hours of context.

Usage: python scripts/01_slice_speed.py <inrix_1min_dir> <out.parquet>
"""
import sys

import pandas as pd

T0, T1 = "2026-09-09T12:00", "2026-09-17T18:00"

COLS = {
    "Date Time": "t_local",
    "Segment ID": "xd",
    "Speed(km/hour)": "speed_kmh",
    "Hist Av Speed(km/hour)": "hist_kmh",
    "Ref Speed(km/hour)": "ref_kmh",
    "CValue": "cvalue",
    "Pct Score30": "score30",
    "Road Closure": "closure",
}


def main(src_dir, out_path):
    parts = []
    reader = pd.read_csv(f"{src_dir}/data.csv", usecols=list(COLS), chunksize=2_000_000, dtype={"Date Time": str})
    for chunk in reader:
        # ISO strings with the same offset sort correctly as text; Phoenix has no DST
        stamp = chunk["Date Time"].str[:16]
        keep = chunk[(stamp >= T0) & (stamp <= T1)]
        if len(keep):
            parts.append(keep)
        print(f"read {len(chunk):,} rows, kept {len(keep):,}", flush=True)
    df = pd.concat(parts).rename(columns=COLS)
    df["t_local"] = pd.to_datetime(df["t_local"].str[:19])
    df = df.sort_values(["xd", "t_local"]).reset_index(drop=True)
    df.to_parquet(out_path, index=False)
    print(f"wrote {len(df):,} rows, {df.xd.nunique()} segments, {df.t_local.min()} to {df.t_local.max()}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
