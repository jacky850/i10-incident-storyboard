"""Read single frames straight out of the daily tar archives, without extracting them.

A tar stores each member's bytes contiguously after its header. Indexing a tar once records
(member name, byte offset, size); a frame is then one seek and one read.

Build the index:  python scripts/tarindex.py           (all days; about 1 min per tar)
Use it:           from tarindex import FrameStore; FrameStore().read("camera_images/...jpg")
"""
import sys
import tarfile

import pandas as pd

from paths import ARCHIVE, DATA

INDEX = DATA / "tar_index"


def tar_path(day):
    return ARCHIVE / day / f"tits_i10_{day}.tar"


def build(day):
    out = INDEX / f"{day}.csv"
    if out.exists():
        return out
    rows = []
    with tarfile.open(tar_path(day), "r:") as tf:
        for m in tf:
            if m.isfile() and m.name.endswith(".jpg"):
                rows.append((m.name.removeprefix("camera/"), m.offset_data, m.size))
    INDEX.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["relative_path", "offset", "size"]).to_csv(out, index=False)
    return out


class FrameStore:
    """Map manifest relative_path -> JPEG bytes, reading from the tars."""

    def __init__(self):
        parts = []
        for f in sorted(INDEX.glob("*.csv")):
            df = pd.read_csv(f)
            df["day"] = f.stem
            parts.append(df)
        self.index = pd.concat(parts).set_index("relative_path")
        self._handles = {}

    def read(self, relative_path):
        row = self.index.loc[relative_path]
        fh = self._handles.get(row.day)
        if fh is None:
            fh = self._handles[row.day] = open(tar_path(row.day), "rb")
        fh.seek(int(row.offset))
        return fh.read(int(row["size"]))


if __name__ == "__main__":
    days = sys.argv[1:] or sorted(p.name for p in ARCHIVE.iterdir() if p.is_dir())
    for day in days:
        print(day, build(day), flush=True)
