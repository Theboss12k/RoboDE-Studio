"""Video intake of the site scan: the container header (probe) and keyframe extraction.

PyAV (`av`) is imported inside the functions, so a missing backend surfaces as ImportError in the task handler.
"""
import math
import struct
from pathlib import Path

import numpy as np

from . import ScanError

MAX_DURATION_S = 180.0
CONTAINERS = ('mp4', 'mov', 'matroska', 'webm')       # PyAV format names: 'mov,mp4,m4a,3gp,3g2,mj2' and 'matroska,webm'
TARGET_FPS = 3.0
MAX_FRAMES = 180
LONG_SIDE = 1600
BLUR_RATIO = 0.4                                     # drop frames whose Laplacian variance is under 40% of the median
JPEG_QUALITY = 92


# -- probe ------------------------------------------------------------------------------------------------------------

def display_rotation_cw(matrix):
    """Clockwise degrees (0, 90, 180 or 270) that turn the stored picture upright, from the 36 bytes of an MP4/MOV
    display matrix (nine little-endian int32, 16.16 fixed point except the last, 2.30). None when unreadable.

    Same maths as FFmpeg's av_display_rotation_get, whose angle is counter-clockwise: this returns its negation."""
    try:
        m = struct.unpack('<9i', bytes(matrix)[:36])
    except (struct.error, TypeError):
        return None
    s0, s1 = math.hypot(m[0], m[3]), math.hypot(m[1], m[4])
    if s0 == 0 or s1 == 0:
        return None
    ccw = -math.degrees(math.atan2(m[1] / s1, m[0] / s0))
    return int(round(-ccw / 90.0)) % 4 * 90


def _read_header(path):
    """The container header as a dict (format, duration_s, width, height, fps, rotation_deg); ScanError scan_bad_video
    when the file is not an MP4, MOV or WebM with a video stream."""
    import av
    try:
        with av.open(str(path)) as container:
            names = (container.format.name or '').split(',')
            if not any(n in CONTAINERS for n in names):
                raise ScanError('scan_bad_video', f'The file is a {container.format.name} container; use an MP4, MOV or WebM video')
            if not container.streams.video:
                raise ScanError('scan_bad_video', 'The file has no video stream')
            stream = container.streams.video[0]
            duration = container.duration / 1e6 if container.duration else None
            if duration is None and stream.duration and stream.time_base:
                duration = float(stream.duration * stream.time_base)
            if duration is None:                                 # a WebM without a duration: read the packet times
                last = 0.0
                for packet in container.demux(stream):
                    if packet.pts is not None:
                        last = max(last, float((packet.pts + (packet.duration or 0)) * packet.time_base))
                duration = last
            rate = stream.average_rate or stream.guessed_rate
            rotation = None
            try:
                rotation = display_rotation_cw(stream.codec_context.coded_side_data.get('display_matrix'))
            except (AttributeError, TypeError):
                pass
            if rotation is None:
                try:
                    rotation = int(round(float(stream.metadata.get('rotate', 0)) / 90.0)) % 4 * 90
                except (ValueError, TypeError, AttributeError):
                    rotation = 0
            return {'format': container.format.name, 'duration_s': float(duration or 0.0), 'width': int(stream.width),
                    'height': int(stream.height), 'fps': float(rate) if rate else 0.0, 'rotation_deg': int(rotation)}
    except ScanError:
        raise
    except (OSError, ValueError, av.error.FFmpegError) as error:
        raise ScanError('scan_bad_video', f'The video cannot be read: {error}') from error


def probe(path):
    """{duration_s, width, height, fps, rotation_deg} of the video at `path`, from the container header (the stored
    width and height, before the rotation). Raises ScanError scan_bad_video (not MP4/MOV/WebM, unreadable, no frames)
    or scan_too_long (over 180 s)."""
    info = _read_header(path)
    if info['width'] < 16 or info['height'] < 16 or info['duration_s'] <= 0:
        raise ScanError('scan_bad_video', 'The video has no readable picture or duration')
    if info['duration_s'] > MAX_DURATION_S:
        raise ScanError('scan_too_long', f'The video is {info["duration_s"]:.0f} s long; the limit is {MAX_DURATION_S:.0f} s')
    return {k: info[k] for k in ('duration_s', 'width', 'height', 'fps', 'rotation_deg')}


# -- keyframes --------------------------------------------------------------------------------------------------------

def sharpness(gray):
    """Variance of the Laplacian of a grey image: the usual blur measure (higher is sharper)."""
    import cv2
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def rotate_cw(image, degrees):
    """Rotate an image array clockwise by a multiple of 90 degrees."""
    import cv2
    code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(int(degrees) % 360)
    return image if code is None else cv2.rotate(image, code)


def keep_sharp(values, ratio=BLUR_RATIO):
    """Indices whose sharpness is at least `ratio` of the median (pure; used by extract_keyframes)."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return []
    floor = ratio * float(np.median(values))
    return [i for i, v in enumerate(values) if v >= floor]


def extract_keyframes(path, rotation_deg, frames_dir, progress=None, target_fps=TARGET_FPS, max_frames=MAX_FRAMES,
                      long_side=LONG_SIDE):
    """Write sharp keyframes as `frames_dir/NNNN.jpg` and return {frames: [{index, t_s, image}], width, height, sampled,
    dropped}.

    The clip is cut into windows of 1/target_fps s (longer when the clip would give more than `max_frames`); the
    sharpest decoded frame of each window is the candidate. Candidates whose Laplacian variance is under 40% of the
    median are dropped. Each kept frame is rotated clockwise by `rotation_deg` and shrunk to `long_side` pixels."""
    import av
    import cv2
    frames_dir = Path(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    try:
        container = av.open(str(path))
    except (OSError, ValueError, av.error.FFmpegError) as error:
        raise ScanError('scan_bad_video', f'The video cannot be read: {error}') from error
    candidates = []                                         # (t_s, sharpness, temp file)
    shape = None
    try:
        stream = container.streams.video[0]
        stream.thread_type = 'AUTO'
        duration = container.duration / 1e6 if container.duration else 0.0
        interval = max(1.0 / target_fps, duration / max_frames if duration else 0.0)
        start = float(stream.start_time * stream.time_base) if stream.start_time is not None else 0.0

        def flush(best):
            nonlocal shape
            t, score, frame = best
            image = frame.to_ndarray(format='bgr24')
            image = rotate_cw(image, rotation_deg)
            h, w = image.shape[:2]
            if max(h, w) > long_side:
                s = long_side / max(h, w)
                image = cv2.resize(image, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA)
            shape = image.shape[:2]
            ok, buf = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if not ok:
                raise ScanError('scan_bad_video', 'A frame could not be encoded')
            temp = frames_dir / f'tmp_{len(candidates):05d}.jpg'
            temp.write_bytes(buf.tobytes())
            candidates.append((t, score, temp))

        window, best = -1, None
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * frame.time_base) - start
            k = int(max(t, 0.0) // interval)
            if k != window:
                if best is not None:
                    flush(best)
                window, best = k, None
            small = frame.reformat(width=640, height=max(2, int(round(640 * frame.height / frame.width / 2)) * 2),
                                   format='gray').to_ndarray()
            score = sharpness(small)
            if best is None or score > best[1]:
                best = (t, score, frame)
        if best is not None:
            flush(best)
    except av.error.FFmpegError as error:
        raise ScanError('scan_bad_video', f'The video cannot be decoded: {error}') from error
    finally:
        container.close()
    keep = set(keep_sharp([c[1] for c in candidates]))
    frames = []
    for i, (t, _, temp) in enumerate(candidates):
        if i in keep:
            name = f'{len(frames):04d}.jpg'
            temp.replace(frames_dir / name)
            frames.append({'index': len(frames), 't_s': round(t, 4), 'image': f'frames/{name}'})
        else:
            temp.unlink()
    if progress:
        dropped = len(candidates) - len(frames)
        progress(f'Frames: {len(frames)} kept' + (f' ({dropped} blurry dropped)' if dropped else ''))
    height, width = shape if shape else (0, 0)
    return {'frames': frames, 'width': int(width), 'height': int(height), 'sampled': len(candidates),
            'dropped': len(candidates) - len(frames)}
