"""Camera and floor from one photo with a monocular depth model (MoGe-2). Optional: needs torch and MoGe
(requirements-ar.txt). The scale is approximate: the model guesses metres from what it sees.

UNTESTED: the MoGe call below follows MoGe's published API (`MoGeModel.from_pretrained`, `model.infer`) but has not been
run in this repository, because torch and MoGe are not installed on the development machines. Treat the first real run as
the test.
"""
from pathlib import Path

import numpy as np

from .geometry import ArError, fit_plane_ransac, image_size, intrinsic_matrix, pose_from_floor_plane

MODEL_NAME = 'Ruicheng/moge-2-vitl-normal'
MAX_POINTS = 20000                      # points given to the RANSAC fit (a random subset of the lower half)
MIN_INLIER_FRACTION = 0.15


def estimate(path):
    """ArCalibration-shaped dict (scale_source 'estimate', focal_source 'estimate') for the photo at `path`.

    Raises ArError('ar_estimator_unavailable') when torch or MoGe is not installed, and ArError('ar_estimate_failed')
    when no floor can be found in the lower half of the image."""
    try:
        import torch
        from moge.model.v2 import MoGeModel
        try:
            import cv2
            rgb = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
        except ImportError:
            from PIL import Image
            rgb = np.asarray(Image.open(path).convert('RGB'))
    except ImportError as error:
        raise ArError('ar_estimator_unavailable',
                      'Automatic estimation needs the optional depth backend (torch + MoGe); install '
                      f'requirements-ar.txt ({error})') from error

    # --- UNTESTED: MoGe call (see the module note) ---
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = MoGeModel.from_pretrained(MODEL_NAME).to(device).eval()
    tensor = torch.tensor(rgb / 255.0, dtype=torch.float32, device=device).permute(2, 0, 1)
    output = model.infer(tensor)       # "points" HxWx3 (OpenCV camera frame, metres), "mask" HxW, "intrinsics" 3x3 normalised
    points = output['points'].cpu().numpy()
    mask = output['mask'].cpu().numpy().astype(bool)
    k = output['intrinsics'].cpu().numpy()
    # --- end of the untested part ---

    height, width = points.shape[:2]
    if (width, height) != tuple(image_size(path)):
        raise ArError('ar_estimate_failed', 'The depth model returned a different image size than the photo')
    valid = mask & np.all(np.isfinite(points), axis=-1)
    valid[: height // 2] = False                           # the floor is in the lower half of the photo
    cloud = points[valid]
    if len(cloud) < 200:
        raise ArError('ar_estimate_failed', 'Not enough depth in the lower half of the photo to find the floor')
    if len(cloud) > MAX_POINTS:
        cloud = cloud[np.random.default_rng(0).choice(len(cloud), MAX_POINTS, replace=False)]
    threshold = 0.02 * float(np.median(cloud[:, 2]))       # 2% of the typical depth
    n, d, inliers = fit_plane_ransac(cloud, threshold=threshold, seed=0)
    fraction = float(np.count_nonzero(inliers)) / len(cloud)
    if fraction < MIN_INLIER_FRACTION:
        raise ArError('ar_estimate_failed', 'No clear floor plane found in the lower half of the photo')
    T = pose_from_floor_plane(n, d)

    fx, fy, cx, cy = float(k[0, 0] * width), float(k[1, 1] * height), float(k[0, 2] * width), float(k[1, 2] * height)
    intrinsic_matrix(fx, fy, cx, cy)                       # fails early on a non-numeric result
    return {'intrinsics': {'fx': fx, 'fy': fy, 'cx': cx, 'cy': cy, 'width': int(width), 'height': int(height)},
            'world_from_camera': T.tolist(), 'scale_source': 'estimate', 'focal_source': 'estimate',
            'residual_px': None, 'camera_height_m': float(T[2, 3]),
            'warnings': ['Scale and floor come from a depth model and are approximate; add a reference rectangle for '
                         'true scale.']}
