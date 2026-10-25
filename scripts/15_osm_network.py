"""Build the GMNS road network of the study area from OpenStreetMap with osm2gmns.

Downloads (once, cached in data/osm/):
  freeways.osm        every motorway, motorway_link, trunk and trunk_link way in the corridor box
  cross_streets.json  named roads within 40 m of I-10, with geometry, for the gazetteer (17_gazetteer.py)

Writes data/network/node.csv and data/network/link.csv (GMNS), with three columns added to link.csv:
  highway   the OSM highway tag (motorway, motorway_link, trunk, trunk_link): tells ramps from mainline
  is_i10    True for I-10 mainline links (OSM ref contains "I 10")
  heading   compass bearing of the link from its first to its last point, degrees

osm2gmns 1.0 ships wheels for Python 3.8-3.12 only, so this script runs in its own environment:
  python3.12 -m venv ~/.venvs/gmns && ~/.venvs/gmns/bin/pip install osm2gmns==1.0.1 pandas shapely
  ~/.venvs/gmns/bin/python scripts/15_osm_network.py
The data are OpenStreetMap contributors' under ODbL; anything derived from them carries that licence.
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import osm2gmns as og
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from paths import DATA, INRIX_SPEED  # noqa: E402

OSM = DATA / "osm"
NET = DATA / "network"
OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "i10-incident-storyboard/0.1 (ASU research script)"
PAD_DEG = 0.01
FREEWAYS_Q = '[out:xml][timeout:300];way["highway"~"^(motorway|motorway_link|trunk|trunk_link)$"]({bbox});(._;>;);out body;'
CROSS_Q = ('[out:json][timeout:300];way["highway"="motorway"]["ref"~"I 10"]({bbox})->.i10;'
           'way(around.i10:40)["highway"~"^(trunk|primary|secondary|tertiary|unclassified|residential|motorway)$"]["name"];'
           'out tags geom;')


def corridor_bbox():
    m = pd.read_csv(Path(INRIX_SPEED) / "metadata.csv")
    m = m[m.Road.str.contains("I-10")]
    lat = pd.concat([m["Start Latitude"], m["End Latitude"]])
    lon = pd.concat([m["Start Longitude"], m["End Longitude"]])
    return f"{lat.min() - PAD_DEG:.3f},{lon.min() - PAD_DEG:.3f},{lat.max() + PAD_DEG:.3f},{lon.max() + PAD_DEG:.3f}"


def overpass(query, dst):
    if dst.exists():
        return
    req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": query}).encode(),
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as r:
        dst.write_bytes(r.read())
    print(f"downloaded {dst.name}: {dst.stat().st_size / 1e6:.1f} MB", flush=True)


def heading(wkt):
    pts = [tuple(map(float, p.split())) for p in wkt[wkt.index("(") + 1:-1].split(",")]
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    dx = (x1 - x0) * np.cos(np.radians((y0 + y1) / 2))
    return float(np.degrees(np.arctan2(dx, y1 - y0)) % 360)


def highway_tags(osm_file):
    """OSM way id -> highway tag, read straight from the XML (osm2gmns folds ramps into 'motorway')."""
    tags, way = {}, None
    for line in open(osm_file):
        line = line.strip()
        if line.startswith("<way "):
            way = int(line.split('id="')[1].split('"')[0])
        elif way is not None and line.startswith('<tag k="highway"'):
            tags[way] = line.split('v="')[1].split('"')[0]
        elif line.startswith("</way>"):
            way = None
    return tags


def main():
    OSM.mkdir(exist_ok=True)
    NET.mkdir(exist_ok=True)
    bbox = corridor_bbox()
    overpass(FREEWAYS_Q.format(bbox=bbox), OSM / "freeways.osm")
    overpass(CROSS_Q.format(bbox=bbox), OSM / "cross_streets.json")

    net = og.getNetFromFile(str(OSM / "freeways.osm"), mode_types="auto", link_types=["motorway", "trunk"],
                            osm_link_attributes=["ref", "destination", "destination:ref", "junction:ref"],
                            osm_node_attributes=["ref", "highway"])
    og.outputNetToCSV(net, output_folder=str(NET))

    link = pd.read_csv(NET / "link.csv")
    hw = highway_tags(OSM / "freeways.osm")
    link["highway"] = link.osm_way_id.map(hw)
    link["is_i10"] = link.ref.fillna("").str.contains(r"\bI 10\b") & link.highway.eq("motorway")
    link["heading"] = link.geometry.map(heading).round(1)
    link.to_csv(NET / "link.csv", index=False)
    print(f"nodes {len(pd.read_csv(NET / 'node.csv'))}, links {len(link)}, "
          f"I-10 mainline links {int(link.is_i10.sum())}, ramps {int(link.highway.str.endswith('_link').sum())}")
    print("bbox", bbox, "| OSM data (c) OpenStreetMap contributors, ODbL")


if __name__ == "__main__":
    main()
