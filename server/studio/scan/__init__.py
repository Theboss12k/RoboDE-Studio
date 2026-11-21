"""Site scan (docs/SITE_SCAN.md): a walk-around video becomes a camera track, a floor and a 3D mesh.

Pure functions; numbers come from code. The heavy modules (video, sfm, dense) import av, open3d and torch only
when they are used, so importing this package is cheap.

Frames: R is the structure-from-motion frame (arbitrary), A the aligned frame (z up, floor z = 0, still in scan units),
S metres. `A_from_R` is a rigid 4x4; `S_from_R = diag(f, f, f, 1) @ A_from_R`. Cameras use the OpenCV convention
(x right, y down, z forward) and poses are `R_from_C` 4x4 matrices.
"""


class ScanError(ValueError):
    """A coded failure of the scan tasks (the task protocol passes `code` through): scan_bad_video, scan_too_long,
    scan_backend_unavailable, scan_failed, scan_bad_scale."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
