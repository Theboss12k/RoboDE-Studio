"""A synthetic walk-around video of a textured room, with ground truth, for the site-scan tests (a helper, not a test).

The room is 5 x 4 m with 2.6 m walls (world frame W: x, y along the floor, z up, origin in a corner of the floor).
It holds a table box (1.2 x 0.8 x 0.75 m) and a white A4 sheet on the floor. Pixels come from numpy ray casting of
procedural textures (a checker floor with coloured blobs, walls with random rectangles and noise).
The camera walks an arc of about 300 degrees round the table at 1.5 m height, looking slightly down, with the gaze
swinging left and right. 30 s at 10 fps, 960 x 540, f = 700 px.
"""
import json
import math
from pathlib import Path

import numpy as np

ROOM = (5.0, 4.0, 2.6)
TABLE_MIN = (1.9, 1.6, 0.0)
TABLE_SIZE = (1.2, 0.8, 0.75)
SHEET_SIZE = (0.297, 0.210)                  # A4: corner 0->1 is the long side
SHEET_CENTRE = (2.5, 0.95)
SHEET_YAW_DEG = 20.0
WIDTH, HEIGHT, FOCAL = 960, 540, 700.0
FPS, SECONDS = 10, 30
PX_PER_M = 400
VERSION = 'v3'


def _noise(rng, shape, scales=(4, 16, 64), weights=(0.5, 0.3, 0.2)):
    """Smooth multi-scale noise in [0, 1], shape (h, w)."""
    import cv2
    h, w = shape
    out = np.zeros(shape, np.float32)
    for s, wt in zip(scales, weights):
        small = rng.random((max(2, h // s), max(2, w // s))).astype(np.float32)
        out += wt * cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    return np.clip(out, 0, 1)


def _decorate(rng, img, blobs, rects, base_noise=0.25):
    """Random coloured circles and rectangles plus noise on an RGB float image (h, w, 3)."""
    import cv2
    h, w = img.shape[:2]
    out = (img * 255).astype(np.uint8).copy()
    for _ in range(rects):
        x0, y0 = int(rng.integers(0, w)), int(rng.integers(0, h))
        x1, y1 = x0 + int(rng.integers(20, 160)), y0 + int(rng.integers(20, 120))
        cv2.rectangle(out, (x0, y0), (x1, y1), tuple(int(c) for c in rng.integers(30, 230, 3)), -1)
    for _ in range(blobs):
        centre = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        cv2.circle(out, centre, int(rng.integers(6, 30)), tuple(int(c) for c in rng.integers(20, 240, 3)), -1)
    out = out.astype(np.float32) / 255.0
    n = _noise(rng, (h, w))[..., None] * base_noise
    return np.clip(out * (1 - base_noise) + n + 0.0, 0, 1)


def sheet_corners_world():
    """The 4 corners of the sheet in W (z = 0), in order around it, counter-clockwise seen from above."""
    L, Wd = SHEET_SIZE
    yaw = math.radians(SHEET_YAW_DEG)
    c, s = math.cos(yaw), math.sin(yaw)
    local = [(-L / 2, -Wd / 2), (L / 2, -Wd / 2), (L / 2, Wd / 2), (-L / 2, Wd / 2)]
    return [[SHEET_CENTRE[0] + c * x - s * y, SHEET_CENTRE[1] + s * x + c * y, 0.0] for x, y in local]


def make_textures(seed=7):
    """RGB float32 textures: floor, wall_x (5 m wide), wall_y (4 m wide), ceiling, table top, table side."""
    import cv2
    rng = np.random.default_rng(seed)
    fw, fh = int(ROOM[0] * PX_PER_M), int(ROOM[1] * PX_PER_M)
    yy, xx = np.mgrid[0:fh, 0:fw]
    cell = (((xx // (PX_PER_M // 2)) + (yy // (PX_PER_M // 2))) % 2).astype(np.float32)
    tint = rng.random((int(ROOM[1] * 2) + 2, int(ROOM[0] * 2) + 2))[(yy // (PX_PER_M // 2)), (xx // (PX_PER_M // 2))]
    floor = np.stack([0.35 + 0.25 * cell, 0.33 + 0.2 * cell + 0.1 * tint, 0.3 + 0.25 * (1 - cell)], axis=-1).astype(np.float32)
    floor = _decorate(rng, floor, blobs=700, rects=220, base_noise=0.3)
    # the sheet: a white quadrilateral in floor pixels (texture x = world x, texture y = world y)
    quad = np.array([[p[0] * PX_PER_M, p[1] * PX_PER_M] for p in sheet_corners_world()], np.float32)
    sheet = floor.copy()
    cv2.fillConvexPoly(sheet, np.round(quad).astype(np.int32), (0.97, 0.97, 0.97))
    wall_x = _decorate(rng, np.full((int(ROOM[2] * PX_PER_M), int(ROOM[0] * PX_PER_M), 3), 0.6, np.float32), 500, 300)
    wall_y = _decorate(rng, np.full((int(ROOM[2] * PX_PER_M), int(ROOM[1] * PX_PER_M), 3), 0.55, np.float32), 400, 250)
    ceiling = _decorate(rng, np.full((fh, fw, 3), 0.8, np.float32), 100, 60)
    top = _decorate(rng, np.full((int(TABLE_SIZE[1] * PX_PER_M), int(TABLE_SIZE[0] * PX_PER_M), 3), 0.5, np.float32), 80, 40)
    side = _decorate(rng, np.full((int(TABLE_SIZE[2] * PX_PER_M), int(TABLE_SIZE[0] * PX_PER_M), 3), 0.4, np.float32), 60, 40)
    return {'floor': sheet, 'wall_x': wall_x, 'wall_y': wall_y, 'ceiling': ceiling, 'top': top, 'side': side}


def camera_path():
    """Camera positions and R_from_C (4x4, W from camera) for every frame; also the table-centre look-at."""
    n = FPS * SECONDS
    centre = np.array([TABLE_MIN[0] + TABLE_SIZE[0] / 2, TABLE_MIN[1] + TABLE_SIZE[1] / 2])
    poses = []
    for i in range(n):
        s = i / (n - 1)
        theta = math.radians(-100.0) + math.radians(300.0) * s
        radius = 1.5 + 0.12 * math.sin(2 * math.pi * 2 * s)
        pos = np.array([centre[0] + radius * math.cos(theta), centre[1] + radius * math.sin(theta),
                        1.5 + 0.04 * math.sin(2 * math.pi * 3 * s)])
        to_centre = math.atan2(centre[1] - pos[1], centre[0] - pos[0])
        yaw = to_centre + math.radians(24.0) * math.sin(2 * math.pi * 3 * s + 0.5)
        pitch = math.radians(-22.0 + 4.0 * math.sin(2 * math.pi * 2 * s + 1.0))
        forward = np.array([math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), math.sin(pitch)])
        right = np.cross(forward, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        down = np.cross(forward, right)
        T = np.eye(4)
        T[:3, 0], T[:3, 1], T[:3, 2], T[:3, 3] = right, down, forward, pos
        poses.append(T)
    return poses


def intrinsics():
    return np.array([[FOCAL, 0.0, WIDTH / 2], [0.0, FOCAL, HEIGHT / 2], [0.0, 0.0, 1.0]])


def project(points_w, T_wc):
    """Pixels (n, 2) of W points seen from the camera with pose T_wc (R_from_C), and their depths."""
    P = np.atleast_2d(np.asarray(points_w, float))
    pc = (P - T_wc[:3, 3]) @ T_wc[:3, :3]
    return np.column_stack([FOCAL * pc[:, 0] / pc[:, 2] + WIDTH / 2, FOCAL * pc[:, 1] / pc[:, 2] + HEIGHT / 2]), pc[:, 2]


def render(T_wc, tex, rng=None):
    """One RGB uint8 frame (HEIGHT, WIDTH, 3) by ray casting."""
    import cv2
    u, v = np.meshgrid(np.arange(WIDTH, dtype=np.float32) + 0.5, np.arange(HEIGHT, dtype=np.float32) + 0.5)
    d_c = np.stack([(u - WIDTH / 2) / FOCAL, (v - HEIGHT / 2) / FOCAL, np.ones_like(u)], axis=-1).reshape(-1, 3)
    d = d_c @ T_wc[:3, :3].T
    o = T_wc[:3, 3]
    n = len(d)
    inf = np.float32(1e9)
    best = np.full(n, inf, np.float32)
    surf = np.full(n, -1, np.int8)
    coords = {}
    with np.errstate(divide='ignore', invalid='ignore'):
        def plane(axis, value, lo, hi, sid):
            t = (value - o[axis]) / d[:, axis]
            p = o + t[:, None] * d
            a1, a2 = [a for a in range(3) if a != axis]
            ok = (t > 1e-3) & (p[:, a1] >= lo[0]) & (p[:, a1] <= hi[0]) & (p[:, a2] >= lo[1]) & (p[:, a2] <= hi[1])
            nonlocal best
            better = ok & (t < best)
            best = np.where(better, t.astype(np.float32), best)
            surf[better] = sid
            coords[sid] = p
        plane(2, 0.0, (0, 0), (ROOM[0], ROOM[1]), 0)                         # floor
        plane(2, ROOM[2], (0, 0), (ROOM[0], ROOM[1]), 1)                      # ceiling
        plane(0, 0.0, (0, 0), (ROOM[1], ROOM[2]), 2)                          # wall x = 0
        plane(0, ROOM[0], (0, 0), (ROOM[1], ROOM[2]), 3)                      # wall x = 5
        plane(1, 0.0, (0, 0), (ROOM[0], ROOM[2]), 4)                          # wall y = 0
        plane(1, ROOM[1], (0, 0), (ROOM[0], ROOM[2]), 5)                      # wall y = 4
        lo, hi = np.array(TABLE_MIN), np.array(TABLE_MIN) + np.array(TABLE_SIZE)
        t1, t2 = (lo - o) / d, (hi - o) / d
        tn, tf = np.minimum(t1, t2).max(axis=1), np.maximum(t1, t2).min(axis=1)
        hit = (tn <= tf) & (tn > 1e-3) & (tn < best)
        best = np.where(hit, tn.astype(np.float32), best)
        surf[hit] = 6
        coords[6] = o + tn[:, None] * d
    out = np.zeros((n, 3), np.float32)
    px = PX_PER_M

    def sample(texture, tu, tv, mask):
        k = int(mask.sum())
        rows = -(-k // 512)                                  # cv2.remap wants a 2-D map with each side < 32767

        def padded(a):
            return np.pad(np.asarray(a[mask] * px, np.float32), (0, rows * 512 - k)).reshape(rows, 512)
        got = cv2.remap(texture, padded(tu), padded(tv), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        return got.reshape(-1, 3)[:k]

    for sid, key, shade, fu, fv in ((0, 'floor', 1.0, 0, 1), (1, 'ceiling', 0.9, 0, 1), (2, 'wall_y', 0.85, 1, 2),
                                    (3, 'wall_y', 0.8, 1, 2), (4, 'wall_x', 0.9, 0, 2), (5, 'wall_x', 0.75, 0, 2)):
        m = surf == sid
        if m.any():
            p = coords[sid]
            tv = ROOM[2] - p[:, fv] if fv == 2 else p[:, fv]
            out[m] = shade * sample(tex[key], p[:, fu], tv, m)
    m = surf == 6
    if m.any():
        p = coords[6]
        top = m & (p[:, 2] > TABLE_MIN[2] + TABLE_SIZE[2] - 1e-3)
        side = m & ~top
        if top.any():
            out[top] = 1.0 * sample(tex['top'], p[:, 0] - TABLE_MIN[0], p[:, 1] - TABLE_MIN[1], top)
        if side.any():
            along = np.where((np.abs(p[:, 0] - TABLE_MIN[0]) < 1e-3) | (np.abs(p[:, 0] - lo[0] - TABLE_SIZE[0]) < 1e-3),
                             p[:, 1] - TABLE_MIN[1], p[:, 0] - TABLE_MIN[0])
            out[side] = 0.7 * sample(tex['side'], along, TABLE_SIZE[2] - p[:, 2], side)
    img = out.reshape(HEIGHT, WIDTH, 3)
    img = cv2.GaussianBlur(img, (0, 0), 0.7)
    if rng is not None:
        img = img + rng.normal(0, 0.008, img.shape).astype(np.float32)
    return np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8)


def sheet_view(T_wc, margin=10):
    """{pixels, visible, area_px} of the sheet seen from pose T_wc: visible when all 4 corners are in front of the
    camera, inside the picture (by `margin` px) and not hidden behind the table."""
    corners = np.array(sheet_corners_world())
    lo, hi = np.array(TABLE_MIN), np.array(TABLE_MIN) + np.array(TABLE_SIZE)
    px, depth = project(corners, T_wc)
    inside = bool(np.all(depth > 0.2) and np.all(px[:, 0] > margin) and np.all(px[:, 0] < WIDTH - margin)
                  and np.all(px[:, 1] > margin) and np.all(px[:, 1] < HEIGHT - margin))
    blocked = False
    for c in corners:                                       # the segment camera -> corner must miss the table box
        dvec = c - T_wc[:3, 3]
        with np.errstate(divide='ignore', invalid='ignore'):
            t1, t2 = (lo - T_wc[:3, 3]) / dvec, (hi - T_wc[:3, 3]) / dvec
        tn, tf = np.minimum(t1, t2).max(), np.maximum(t1, t2).min()
        blocked |= bool(tn <= tf and tn < 1.0 and tf > 0)
    area = float(0.5 * abs(np.dot(px[:, 0], np.roll(px[:, 1], -1)) - np.dot(px[:, 1], np.roll(px[:, 0], -1))))
    return {'pixels': px.tolist(), 'visible': inside and not blocked, 'area_px': area}


def ground_truth(poses):
    """The ground truth dict (JSON-able): K, poses, room, table and sheet. `sheet_view(pose)` gives the sheet's pixels."""
    return {'K': intrinsics().tolist(), 'width': WIDTH, 'height': HEIGHT, 'fps': FPS, 'seconds': SECONDS,
            'poses': [T.tolist() for T in poses], 'room': list(ROOM),
            'table': {'min': list(TABLE_MIN), 'size': list(TABLE_SIZE), 'top_height': TABLE_SIZE[2]},
            'sheet': {'corners_world': sheet_corners_world(), 'size_m': list(SHEET_SIZE)}}

def make_video(directory):
    """Render and encode the clip into `directory` (cached: reused when it is already there). Returns
    (video path, ground-truth dict)."""
    import av
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    video, gt_file = directory / f'room_{VERSION}.mp4', directory / f'room_{VERSION}.json'
    if video.is_file() and gt_file.is_file():
        return video, json.loads(gt_file.read_text(encoding='utf-8'))
    poses = camera_path()
    tex = make_textures()
    rng = np.random.default_rng(3)
    codec = 'libx264' if 'libx264' in av.codecs_available else 'mpeg4'
    tmp = video.with_suffix('.tmp.mp4')
    with av.open(str(tmp), 'w') as container:
        stream = container.add_stream(codec, rate=FPS)
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, 'yuv420p'
        stream.options = {'crf': '16', 'preset': 'veryfast'} if codec == 'libx264' else {}
        if codec == 'mpeg4':
            stream.bit_rate = 8_000_000
        for T in poses:
            frame = av.VideoFrame.from_ndarray(render(T, tex, rng), format='rgb24')
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    gt = ground_truth(poses)
    gt_file.write_text(json.dumps(gt), encoding='utf-8')
    tmp.replace(video)
    return video, gt


if __name__ == '__main__':
    import sys
    import time
    t = time.time()
    path, _ = make_video(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / '.cache')
    print(path, f'{time.time() - t:.1f}s')


