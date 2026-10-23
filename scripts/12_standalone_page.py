"""Pack built storyboards into single HTML files that open anywhere, offline.

Every camera frame is re-encoded and embedded as a data URI, so a file needs no server, no
frames folder and no network. All cameras keep one frame a minute. The site camera is stored
once, at SITE_SIZE from its high-resolution copy, and that one image serves both the card and
the enlarged viewer (f[2] = 1 tells the page f[1] is the sharp copy). Other cameras stay at the
page's 480 x 270.

Usage: python scripts/12_standalone_page.py [case id ...]   (no id: every case in demo_cases.json)
Writes out/standalone/<case id>_storyboard.html.
"""
import base64
import io
import json
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from paths import OUT, REPO  # noqa: E402

DATA_RE = re.compile(r"const D = (\{.*?\});\n", re.S)
QUALITY = 42
SITE_SIZE, SITE_QUALITY = (800, 450), 60


def data_uri(path, quality, size=None):
    img = Image.open(path).convert("RGB")
    if size:
        img = img.resize(size)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def pack(case_id):
    src = OUT / "storyboard" / case_id / "index.html"
    html = src.read_text()
    m = DATA_RE.search(html)
    D = json.loads(m.group(1))
    for c in D["cameras"]:
        if c["role"] == "site":
            c["frames"] = [[f[0], data_uri(src.parent / f[-1], SITE_QUALITY, SITE_SIZE), 1] for f in c["frames"]]
        else:
            c["frames"] = [[f[0], data_uri(src.parent / f[1], QUALITY)] for f in c["frames"]]
    html = html[:m.start(1)] + json.dumps(D, separators=(",", ":")) + html[m.end(1):]
    dst = OUT / "standalone" / f"{case_id}_storyboard.html"
    dst.parent.mkdir(exist_ok=True)
    dst.write_text(html)
    n = sum(len(c["frames"]) for c in D["cameras"])
    print(f"{dst.name}: {n} frames, {dst.stat().st_size / 1e6:.1f} MB", flush=True)


def main(ids):
    if not ids:
        cfg = json.loads((REPO / "demo_cases.json").read_text())
        ids = [c["id"] for c in cfg["cases"] + cfg.get("optional", [])]
    for case_id in ids:
        pack(case_id)


if __name__ == "__main__":
    main(sys.argv[1:])
