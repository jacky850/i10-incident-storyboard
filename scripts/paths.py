"""Local data locations. Copy config/paths.example.json to config/paths.json and edit it.

None of these data sets are in the repository: the AZ511 archive and the INRIX exports stay where
they were downloaded, and data/ and out/ are ignored by git.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
OUT = REPO / "out"

_cfg_file = REPO / "config" / "paths.json"
if not _cfg_file.exists():
    raise FileNotFoundError(f"{_cfg_file} is missing; copy config/paths.example.json and set your paths")
_cfg = json.loads(_cfg_file.read_text())


def _path(key):
    p = Path(_cfg[key]).expanduser()
    return p if p.is_absolute() else (REPO / p).resolve()


ARCHIVE = _path("az511_archive")          # folder of YYYY-MM-DD/tits_i10_YYYY-MM-DD.tar
CAMS = _path("camera_list")               # ID, Latitude, Longitude, Location
INRIX_SPEED = _path("inrix_speed_dir")    # data.csv, metadata.csv of the 1-min XD export
INRIX_INC = _path("inrix_incidents_csv")  # data.csv of the incident export
