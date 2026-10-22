"""Pack one built storyboard into a single HTML file that opens anywhere, offline.

Every camera frame is re-encoded and embedded as a data URI, so the file needs no server, no
frames folder and no network. Frames of the site camera keep a higher quality so the scene
stays readable when enlarged.

Usage: python scripts/12_standalone_page.py <case id> [quality] [site quality]
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
from paths import OUT  # noqa: E402

DATA_RE = re.compile(r"const D = (\{.*?\});\n", re.S)


def data_uri(path, quality):
    buf = io.BytesIO()
    Image.open(path).convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main(case_id, quality=55, site_quality=78):
    src = OUT / "storyboard" / case_id / "index.html"
    html = src.read_text()
    m = DATA_RE.search(html)
    D = json.loads(m.group(1))
    for c in D["cameras"]:
        q = site_quality if c["role"] == "site" else quality
        c["frames"] = [[t, data_uri(src.parent / rel, q)] for t, rel in c["frames"]]
    packed = json.dumps(D, separators=(",", ":"))
    html = html[:m.start(1)] + packed + html[m.end(1):]
    dst = OUT / "standalone" / f"{case_id}_storyboard.html"
    dst.parent.mkdir(exist_ok=True)
    dst.write_text(html)
    n = sum(len(c["frames"]) for c in D["cameras"])
    print(f"{dst}: {n} frames, {dst.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1], *(int(a) for a in sys.argv[2:]))
