"""Writes the synthetic room video and its ground truth for the site-scan e2e tests (a helper, not a test).

usage: make_scan.py <output dir>   ->  <dir>/room.mp4 and <dir>/room_truth.json
The video comes from tests/scan_synth.py and is cached in tests/.cache, so only the first run renders it.
The truth holds the room size, the table, the A4 sheet and, for every video frame, where the sheet's corners are in pixels
(960 x 540) and whether the whole sheet is visible there.
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo / 'tests'))
import scan_synth  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
video, gt = scan_synth.make_video(repo / 'tests' / '.cache')
shutil.copyfile(video, out / 'room.mp4')
poses = [np.array(p) for p in gt['poses']]
truth = {
    'width': gt['width'], 'height': gt['height'], 'fps': gt['fps'], 'seconds': gt['seconds'],
    'room': gt['room'], 'table': gt['table'], 'sheet': gt['sheet'],
    'sheet_views': [scan_synth.sheet_view(T) for T in poses],
}
(out / 'room_truth.json').write_text(json.dumps(truth), encoding='utf-8')
print(f'room video {(out / "room.mp4").stat().st_size / 1e6:.1f} MB, {len(poses)} frames')
