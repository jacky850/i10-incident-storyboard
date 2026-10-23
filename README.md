# I-10 incident storyboard

Replays real incidents on I-10 in Phoenix from three sources on one time axis and one road axis:
AZ511 camera stills (about one a minute), INRIX 1-minute XD speeds, and incident records (ERS from
AZ511, INRIX incidents). It is the real-data front end for the camnet camera-evidence prototype
(SPR-803 Task 4) and the material for the IEEE ITS Magazine lab column.

**Phase 1 (this version).** INRIX speeds give the traffic state; camera frames are shown as evidence
beside it. For each case the page shows, minute by minute:

- the one-line message a TOC operator would read, using only data available at that minute, with
  5, 15 and 30 min forecasts of the queue length and the proposed Tier 1 warning;
- every camera from downstream to upstream, its role (downstream, site, upstream), when the queue
  reached it and left, and when an operator moved it;
- a time-distance diagram of speeds with the queue head and tail and the forecast range;
- the lane, ramp and intersection closures named in the records, marked over the diagram;
- travel time over the plotted road against the other days of the same weekday;
- a timeline of ERS and INRIX records (each rewording of an ERS record), camera moves and KPHX weather;
- an enlarged viewer for any frame, with zoom, pan and frame stepping.

**Phase 2 (next).** Register each camera to the road (6–8 landmark clicks per view), measure vehicle
density in the frames, place the queue tail between cameras with kinematic-wave bounds, and replace
the straight-line forecast.

## Storyboards

Six incidents from Sep 9–17, 2026, chosen after screening all 159 incident episodes in the window;
see [DEMO_CASES.md](DEMO_CASES.md) and [demo_cases.json](demo_cases.json). Each storyboard is **one
self-contained HTML file**: every camera frame is inside it, one a minute, so it opens offline on any
computer with a browser.

**To open one:** click its link below, then **Download raw file** (the download icon at the top right of
the file view), and double-click the downloaded file. GitHub does not show these files as pages and
does not preview files this large.

### Main cases

| | Storyboard | What it shows | Size |
|---|---|---|---|
| A | [Sun Sep 13, EB 7th St, car into a light pole](out/standalone/A_0913_7thst_storyboard.html) | The full chain in daylight: scene camera, queue through the tunnel and back, downstream unaffected | 13 MB |
| B | [Wed Sep 16, EB tunnel, crash blocking several lanes, then an exit-ramp closure](out/standalone/B_0916_tunnel_storyboard.html) | Slow traffic over 12 km across 10 cameras; scene camera moved 2 min before the record; the morning peak on top after 07:00 | 37 MB |
| D | [Tue Sep 15, WB University Dr, crash and ramp closure](out/standalone/D_0915_university_closure_storyboard.html) | The scene through operator PTZ zoom (8.9x, 13.3x); a 12.7 h ramp closure with a small mainline effect | 25 MB |
| E | [Thu Sep 17, WB Exit 138, crash and two secondary crashes](out/standalone/E_0917_exit138_secondary_storyboard.html) | Largest delay of the 8 days; a second queue from a secondary crash runs past 769, 770 and 771 | 11 MB |

### Optional cases

| | Storyboard | Why it is kept | Limits | Size |
|---|---|---|---|---|
| C | [Tue Sep 15, EB Broadway Curve, vehicle fire](out/standalone/C_0915_broadway_fire_storyboard.html) | The Broadway Curve pilot corridor; logged by INRIX, not by ERS; a dead camera (1257) for phase 2 to bridge | No site or downstream camera: the fire is 2 km past the last camera | 5 MB |
| F | [Wed Sep 16, EB 3rd St, crash in a thunderstorm queue](out/standalone/F_0916_thunderstorm_storyboard.html) | Rain on camera 784, KPHX thunderstorms | Congestion began before the crash | 43 MB |

The site camera is stored at 800 x 450 for the enlarged viewer; the other cameras at 480 x 270. The
storyboards contain AZ511 stills (plates and faces not blurred) and INRIX-derived speeds. Keep the
repository private and the files inside the team; anything published needs ADOT's written permission.

## Data

The raw data are not in this repository: the AZ511 daily tars, the INRIX exports and everything built
from them under `data/` stay where they were downloaded and are ignored by git.

1. Copy `config/paths.example.json` to `config/paths.json` and set the four paths: the AZ511 daily
   tar archive, the camera list, the INRIX 1-minute XD export and the INRIX incident export.
2. Extract the manifests, events and weather from the tars into `data/az511/` (small; the images stay
   in the tars):
   `for d in <archive>/*/; do tar -xf $d/*.tar -C data/az511 events weather 'camera_manifest_*'; done`

## Run

```bash
pip install -r requirements.txt
python scripts/tarindex.py              # byte offsets of every frame in the tars, once (about 1 min)
python scripts/01_slice_speed.py <inrix_1min_dir> data/speed_0909_0917.parquet
python scripts/02_ers_events.py data/az511 data/ers_events.csv
python scripts/03_screen_incidents.py   # episodes, speed impact, nearby cameras
python scripts/08_story_screen.py       # camera roles and queue arrival per episode
python scripts/10_final_sweep.py        # adds operator camera moves; ranks episodes
python scripts/11_build_storyboard.py   # out/storyboard/<case>/index.html for every case
python scripts/12_standalone_page.py    # out/standalone/<case>_storyboard.html, one file per case
python -m http.server 8512 --directory out/storyboard   # optional: browse the unpacked pages
```

`scripts/frame_viewer.py` is a local viewer for any camera at any minute, reading frames straight from
the tars (`python scripts/frame_viewer.py`, then http://localhost:8511).

| Script | Does |
|---|---|
| `corridor.py` | Orders the I-10 XD segments along each direction; distance axis in km |
| `tarindex.py` | Indexes the tars; reads one frame by seek |
| `01`–`03` | Speed slice, ERS events from AZ511 snapshots, incident screen |
| `04`, `06`, `07`, `09` | Time-distance plots and contact sheets for review |
| `05` | Extracts frames of selected cameras and windows |
| `08`, `10` | Story screen (site, queue, downstream cameras) and final ranking |
| `11` | Builds the storyboards |
| `12` | Packs each storyboard into one self-contained HTML file |
| `typical_days.py` | Same-weekday INRIX speeds for the usual-day travel time |

## Known limits

- The usual speed is INRIX's historic average for the hour and weekday, not the Track 1 counterfactual.
- Queue head and tail are at XD segment resolution and carry INRIX latency of a few minutes.
- AZ511 serves stills that can be minutes older than the time we fetched them: the timestamp printed
  on the image ran 0.5–3.6 min behind the fetch time in case A. Frame times should come from the overlay
  (OCR) before camera timing is used for measurement.
- The straight-line forecast overshoots when a queue's growth slows; 30 min forecasts held the observed
  length in only 27–64% of minutes.
- Camera 1257 (Broadway Rd) returned no image on any of the 8 days. Collection stopped 05:18–09:39 on
  Sep 10.
- Camera moves are found from frame-to-frame change; dawn light changes can trigger it.
- Any still or clip leaving the team needs ADOT's written permission and blurred plates and faces.
