"""Sample site photos for the end-to-end AR test: synthetic floors with a sheet of paper, rendered from known cameras.

Each photo is a pinhole render (no lens distortion) of a checker floor (0.25 m squares) with a white sheet whose corner 0
is the floor origin. The manifest records, per photo, the sheet's true corners in DISPLAY pixels (after any EXIF
rotation), the sheet size, and the true focal length and camera height, so the test can check calibration against
ground truth. Usage: python e2e/fixtures/make_photos.py <out_dir>
"""
import io
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

A4 = (0.297, 0.210)
LETTER = (0.2794, 0.2159)

PHOTOS = [
    # name, display size, focal px, camera height m, pitch deg, yaw deg, sheet, format, exif orientation
    ('desk_a4_landscape.png', (1600, 1200), 1200.0, 1.30, 40.0, 15.0, A4, 'PNG', 1),
    ('floor_letter_square.jpg', (2000, 1500), 1500.0, 1.10, 50.0, 0.0, LETTER, 'JPEG', 1),
    ('phone_portrait_exif6.jpg', (1500, 2000), 1600.0, 1.45, 45.0, 35.0, A4, 'JPEG', 6),
]


def camera(height, pitch_deg, yaw_deg, sheet):
    """R (camera axes in floor frame, columns right/down/forward) and centre C; the optical axis hits the sheet centre."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    heading = np.array([-math.sin(yaw), math.cos(yaw), 0.0])
    forward = np.array([heading[0] * math.cos(pitch), heading[1] * math.cos(pitch), -math.sin(pitch)])
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    R = np.column_stack([right, down, forward])
    C = np.array([sheet[0] / 2, sheet[1] / 2, 0.0]) - forward * (height / math.sin(pitch))
    return R, C


def render(size, focal, height, pitch, yaw, sheet):
    W, H = size
    R, C = camera(height, pitch, yaw, sheet)
    u, v = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    rays = np.stack([(u - W / 2) / focal, (v - H / 2) / focal, np.ones_like(u)], -1) @ R.T
    with np.errstate(divide='ignore', invalid='ignore'):
        s = np.where(rays[..., 2] < -1e-9, -C[2] / rays[..., 2], np.nan)
    P = C + rays * s[..., None]
    img = np.empty((H, W, 3), np.uint8)
    img[:] = (190, 205, 225)
    floor = np.isfinite(s) & (s > 0)
    checker = (np.floor(P[..., 0] / 0.25) + np.floor(P[..., 1] / 0.25)) % 2 == 0
    img[floor & checker] = (120, 110, 100)
    img[floor & ~checker] = (150, 140, 128)
    inside = floor & (P[..., 0] >= 0) & (P[..., 0] <= sheet[0]) & (P[..., 1] >= 0) & (P[..., 1] <= sheet[1])
    img[inside] = (250, 250, 250)

    def project(p):
        c = R.T @ (np.asarray(p, float) - C)
        return [float(focal * c[0] / c[2] + W / 2), float(focal * c[1] / c[2] + H / 2)]

    corners = [project(p) for p in ([0, 0, 0], [sheet[0], 0, 0], [sheet[0], sheet[1], 0], [0, sheet[1], 0])]
    return img, corners


def save(img, path, fmt, orientation):
    stored = img if orientation == 1 else np.rot90(img, k=1)  # orientation 6: viewer turns stored pixels 90 deg CW
    pil = Image.fromarray(np.ascontiguousarray(stored))
    if fmt == 'PNG':
        pil.save(path, 'PNG')
        return
    exif = Image.Exif()
    if orientation != 1:
        exif[0x0112] = orientation
    buf = io.BytesIO()
    pil.save(buf, 'JPEG', quality=92, exif=exif.tobytes())
    path.write_bytes(buf.getvalue())


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, size, focal, height, pitch, yaw, sheet, fmt, orientation in PHOTOS:
        img, corners = render(size, focal, height, pitch, yaw, sheet)
        save(img, out / name, fmt, orientation)
        manifest.append({'file': name, 'width': size[0], 'height': size[1], 'focal_px': focal, 'camera_height_m': height,
                         'yaw_deg': yaw, 'size_m': list(sheet), 'corners_px': corners, 'exif_orientation': orientation})
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'wrote {len(manifest)} photos to {out}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'e2e/.photos')
