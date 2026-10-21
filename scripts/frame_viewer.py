"""Local web viewer for the AZ511 archive: frames are read straight from the tars, nothing is extracted.

Needs data/tar_index/ (scripts/tarindex.py) and the manifests in data/az511/.

Usage: python scripts/frame_viewer.py [port]      then open http://localhost:8511
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from paths import CAMS, REPO as ROOT  # noqa: E402
from tarindex import FrameStore  # noqa: E402


def load():
    rows = []
    for f in sorted((ROOT / "data/az511").glob("camera_manifest_*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r["download_status"] == "saved":
                rows.append((r["camera_id"], r["view_description"], r["collected_at_utc"], r["relative_path"]))
    m = pd.DataFrame(rows, columns=["camera_id", "view", "t_utc", "path"])
    m["t"] = pd.to_datetime(m.t_utc).dt.tz_convert("America/Phoenix").dt.tz_localize(None)
    m = m.sort_values(["camera_id", "t"]).reset_index(drop=True)
    cams = pd.read_csv(CAMS, dtype={"ID": str})
    views = m.groupby("camera_id").view.first()
    cams["view"] = cams.ID.map(views).fillna("no frames")
    events = []
    screen = ROOT / "out/incident_screen.csv"
    if screen.exists():
        e = pd.read_csv(screen, parse_dates=["start"])
        for r in e.itertuples():
            near = [c.split("(")[0] for c in str(r.cameras).split()[:4] if ",gap" not in c]
            events.append({"t": f"{r.start:%Y-%m-%dT%H:%M}", "label": f"{r.start:%m-%d %H:%M} {r.dir}B {r.description[:70]}",
                           "cams": near})
    return m, cams, events


FRAMES, CAMERAS, EVENTS = load()
STORE = FrameStore()
PAGE = (Path(__file__).parent / "frame_viewer.html").read_text()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/":
            self.send(PAGE.encode(), "text/html; charset=utf-8")
        elif url.path == "/api/meta":
            body = {"cameras": CAMERAS.rename(columns={"ID": "id"})[["id", "Location", "view"]].to_dict("records"),
                    "events": EVENTS, "first": str(FRAMES.t.min()), "last": str(FRAMES.t.max())}
            self.send(json.dumps(body).encode(), "application/json")
        elif url.path == "/api/nearest":
            # nearest frame to time t for camera cam, or the step-th frame after/before it
            f = FRAMES[FRAMES.camera_id == q["cam"]]
            if f.empty:
                return self.send(b"{}", "application/json")
            t = pd.Timestamp(q["t"])
            i = int((f.t - t).abs().values.argmin())
            i = min(max(i + int(q.get("step", 0)), 0), len(f) - 1)
            r = f.iloc[i]
            body = {"t": f"{r.t:%Y-%m-%dT%H:%M:%S}", "path": r.path, "off_s": round((r.t - t).total_seconds())}
            self.send(json.dumps(body).encode(), "application/json")
        elif url.path == "/img":
            self.send(STORE.read(q["path"]), "image/jpeg")
        else:
            self.send(b"not found", "text/plain", 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8511
    print(f"{len(FRAMES):,} frames from {FRAMES.t.min()} to {FRAMES.t.max()}; http://localhost:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
