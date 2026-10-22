"""Speeds on the same weekday as an incident, for the "usual day" comparison in the storyboards.

One pass over the INRIX 1-minute export keeps, for each case, the segments and time of day it
needs on every date in the export that falls on the incident's weekday (the incident day too).
The result is cached in data/typical/<case id>.parquet.
"""
import pandas as pd

from paths import DATA, INRIX_SPEED

CACHE = DATA / "typical"
EXPORT_DATES = pd.date_range("2026-08-01", "2026-09-30")
HOLIDAYS = {"2026-09-07"}            # Labor Day


def cache_path(case_id):
    return CACHE / f"{case_id}.parquet"


def same_weekday_dates(day):
    day = pd.Timestamp(day)
    return {f"{d:%Y-%m-%d}" for d in EXPORT_DATES if d.weekday() == day.weekday()} - HOLIDAYS


def build(specs):
    """specs: {case id: {"date": day, "xds": set of XD ids, "tod": ("HH:MM", "HH:MM")}}."""
    if not specs:
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    for s in specs.values():
        s["dates"] = same_weekday_dates(s["date"])
    parts = {k: [] for k in specs}
    cols = {"Date Time": "t", "Segment ID": "xd", "Speed(km/hour)": "v", "Ref Speed(km/hour)": "ref"}
    for chunk in pd.read_csv(INRIX_SPEED / "data.csv", usecols=list(cols), chunksize=2_000_000, dtype={"Date Time": str}):
        chunk = chunk.rename(columns=cols)
        date, tod = chunk.t.str[:10], chunk.t.str[11:16]
        for k, s in specs.items():
            keep = date.isin(s["dates"]) & chunk.xd.isin(s["xds"]) & (tod >= s["tod"][0]) & (tod <= s["tod"][1])
            if keep.any():
                parts[k].append(chunk[keep].assign(date=date[keep], tod=tod[keep]).drop(columns="t"))
        print("typical days: chunk read", flush=True)
    for k, p in parts.items():
        pd.concat(p).to_parquet(cache_path(k), index=False)


def load(case_id):
    return pd.read_parquet(cache_path(case_id))
