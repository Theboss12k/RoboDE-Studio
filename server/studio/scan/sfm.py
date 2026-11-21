"""Incremental structure-from-motion with OpenCV and SciPy (docs/SITE_SCAN.md, "Cameras").

One shared SIMPLE_RADIAL camera for the whole clip (focal length f and radial k1; the principal point stays at the image
centre). Pipeline:
  1. SIFT (RootSIFT descriptors, about 2000 per frame) on every keyframe; ratio-test matches between neighbouring
     frames plus a few skip-frame pairs, each pair verified by a fundamental-matrix RANSAC;
  2. tracks from the verified matches (connected components; a track that hits one frame twice is dropped);
  3. initialisation from the essential matrix of the best early pair, with the focal length chosen by a sweep of
     candidate fields of view (50-80 degrees) by lowest initial reprojection error;
  4. frames are added by PnP RANSAC, in the order of most 3D correspondences, and new tracks are triangulated;
  5. bundle adjustment (`scipy.optimize.least_squares`, Huber loss, sparse Jacobian) refines the poses, the points and the
     shared (f, k1): locally every few frames and over everything every time the model grew by about half, and once at
     the end.
The result is the one reconstruction grown from the initial pair, i.e. the largest connected one; frames that never
registered are None. Units and frame (R) are arbitrary: the first camera is the origin, the first baseline about 1.
"""
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from . import ScanError

FEATURES = 2000
RATIO = 0.8
PAIR_OFFSETS = (1, 2, 3, 5, 8, 12)           # neighbouring frames plus a few skip-frame pairs
MIN_PAIR_INLIERS = 30
FOV_SWEEP_DEG = (50.0, 80.0)
OUTLIER_PX = 4.0                            # observations farther than this are left out of an adjustment
INLIER_PX = 3.0                              # an observation farther than this from its point is an outlier
MIN_TRI_ANGLE_DEG = 1.5
LOCAL_EVERY = 3                              # local bundle adjustment after this many new frames
LOCAL_WINDOW = 8
GLOBAL_GROWTH = 1.4
K1_PRIOR = 600.0                             # pixels per unit of k1: a weak pull of the radial term to zero (|k1| 0.05 costs 30 px)
FINAL_NFEV = 20                              # least-squares evaluations of each of the two final global adjustments


@dataclass
class Reconstruction:
    camera: dict                                  # fx, fy, cx, cy, k1, width, height
    poses: list                                   # per frame: R_from_C 4x4 (np.ndarray) or None
    points: np.ndarray                            # (N, 3) in R
    colors: np.ndarray                            # (N, 3) uint8 RGB
    reproj_px: float                              # RMS reprojection error of the inlier observations
    visible: list = field(default_factory=list)   # per frame: (point ids, observed pixels (n, 2)) or None
    timings: dict = field(default_factory=dict)


# -- features and matching --------------------------------------------------------------------------------------------

def detect(gray, count=FEATURES):
    """(pixels (n, 2) float64, RootSIFT descriptors (n, 128) float32) of a grey image."""
    import cv2
    sift = cv2.SIFT_create(nfeatures=count, contrastThreshold=0.02)
    keypoints, desc = sift.detectAndCompute(gray, None)
    if desc is None or len(keypoints) == 0:
        return np.zeros((0, 2)), np.zeros((0, 128), np.float32)
    desc = desc / (desc.sum(axis=1, keepdims=True) + 1e-7)
    return np.array([k.pt for k in keypoints], dtype=np.float64), np.sqrt(desc).astype(np.float32)


def match_pair(xy1, d1, xy2, d2, ratio=RATIO, threshold_px=2.0):
    """Verified matches between two frames: (indices into frame 1, indices into frame 2), or None.

    Lowe ratio test, one-to-one, then a fundamental-matrix RANSAC keeps the geometrically consistent ones."""
    import cv2
    if len(d1) < 8 or len(d2) < 8:
        return None
    knn = cv2.BFMatcher(cv2.NORM_L2).knnMatch(d1, d2, k=2)
    best = {}
    for pair in knn:
        if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance:
            m = pair[0]
            if m.trainIdx not in best or m.distance < best[m.trainIdx].distance:
                best[m.trainIdx] = m
    if len(best) < MIN_PAIR_INLIERS:
        return None
    i1 = np.array([m.queryIdx for m in best.values()])
    i2 = np.array([m.trainIdx for m in best.values()])
    _, mask = cv2.findFundamentalMat(xy1[i1], xy2[i2], cv2.FM_RANSAC, threshold_px, 0.999, 3000)
    if mask is None:
        return None
    keep = mask.ravel().astype(bool)
    if keep.sum() < MIN_PAIR_INLIERS:
        return None
    return i1[keep], i2[keep]


# -- camera model -----------------------------------------------------------------------------------------------------

def undistort_norm(xy, f, k1, cx, cy):
    """Pixels -> undistorted normalised image coordinates (x/z, y/z) for the SIMPLE_RADIAL camera
    u = f * x * (1 + k1 * r^2) + c, by fixed-point iteration."""
    xd = (np.asarray(xy, dtype=np.float64) - [cx, cy]) / f
    xu = xd.copy()
    if k1 != 0.0:
        for _ in range(8):
            xu = xd / (1.0 + k1 * np.sum(xu * xu, axis=1, keepdims=True))
    return xu


def rotate(points, rvec):
    """Rodrigues rotation of each point by its own rotation vector (both (n, 3))."""
    theta = np.linalg.norm(rvec, axis=1)[:, None]
    with np.errstate(invalid='ignore', divide='ignore'):
        axis = np.where(theta > 1e-12, rvec / theta, 0.0)
    cos, sin = np.cos(theta), np.sin(theta)
    dot = np.sum(points * axis, axis=1, keepdims=True)
    return cos * points + sin * np.cross(axis, points) + dot * (1 - cos) * axis


def project(points, rvec, t, f, k1, cx, cy):
    """Pixels (n, 2) of world points seen by cameras given as camera-from-world (rvec, t) rows."""
    pc = rotate(points, rvec) + t
    z = np.where(pc[:, 2] > 1e-9, pc[:, 2], 1e-9)
    xn = pc[:, :2] / z[:, None]
    xd = xn * (1.0 + k1 * np.sum(xn * xn, axis=1, keepdims=True))
    return f * xd + [cx, cy], pc[:, 2]


def triangulate_batch(P1, P2, x1, x2):
    """Linear (DLT) triangulation of N point pairs. P1, P2: (N, 3, 4) projection matrices of normalised coordinates;
    x1, x2: (N, 2) normalised coordinates. Returns (N, 3)."""
    A = np.stack([x1[:, 0:1] * P1[:, 2] - P1[:, 0], x1[:, 1:2] * P1[:, 2] - P1[:, 1],
                  x2[:, 0:1] * P2[:, 2] - P2[:, 0], x2[:, 1:2] * P2[:, 2] - P2[:, 1]], axis=1)
    X = np.linalg.svd(A)[2][:, -1]
    with np.errstate(invalid='ignore', divide='ignore'):
        return X[:, :3] / X[:, 3:4]


# -- bundle adjustment ------------------------------------------------------------------------------------------------

def bundle_adjust(rvec, tvec, points, f, k1, cx, cy, obs_cam, obs_pt, obs_xy, var_cams, fit_k1, max_nfev,
                  f_scale=1.5, tr_options=None, x_scale='jac', loss='linear'):
    """Refine the poses of `var_cams`, all `points` and the shared (f, k1); other cameras are constants.

    rvec, tvec: (n_frames, 3) camera-from-world (not modified). obs_*: observation arrays (frame, point index, pixel).
    Linear loss on the pixel residuals (outliers are removed beforehand: Huber converged far more slowly), a sparse Jacobian pattern (`jac_sparsity`, so only about a dozen residual
    evaluations per Jacobian), f bounded to +-50% and k1 to [-0.5, 0.5]. Returns (rvec, tvec, points, f, k1)."""
    from scipy.optimize import least_squares
    from scipy.sparse import coo_matrix
    nv, npt, m = len(var_cams), len(points), len(obs_cam)
    n_intr = 2 if fit_k1 else 1
    slot = -np.ones(len(rvec), dtype=int)
    slot[var_cams] = np.arange(nv)
    base_pt = n_intr + 6 * nv
    # layout: intrinsics, then per camera (rvec 3, t 3), then points
    cam0 = np.hstack([rvec[var_cams], tvec[var_cams]]).ravel()
    x0 = np.concatenate([[f], [k1] if fit_k1 else [], cam0, points.ravel()])
    cs = slot[obs_cam]

    def unpack(x):
        f_ = x[0]
        k_ = x[1] if fit_k1 else k1
        cams = x[n_intr:base_pt].reshape(-1, 6)
        return f_, k_, cams, x[base_pt:].reshape(-1, 3)

    def residuals(x):
        f_, k_, cams, pts = unpack(x)
        r = rvec[obs_cam].copy()
        t = tvec[obs_cam].copy()
        sel = cs >= 0
        r[sel] = cams[cs[sel], :3]
        t[sel] = cams[cs[sel], 3:]
        uv, _ = project(pts[obs_pt], r, t, f_, k_, cx, cy)
        res = (uv - obs_xy).ravel()
        return np.append(res, K1_PRIOR * k_) if fit_k1 else res

    rows, cols = [], []
    ar = np.arange(m)
    for dr in (0, 1):
        for c in range(n_intr):
            rows.append(2 * ar + dr)
            cols.append(np.full(m, c))
        for j in range(6):
            sel = cs >= 0
            rows.append((2 * ar + dr)[sel])
            cols.append(n_intr + 6 * cs[sel] + j)
        for j in range(3):
            rows.append(2 * ar + dr)
            cols.append(base_pt + 3 * obs_pt + j)
    rows, cols = np.concatenate(rows), np.concatenate(cols)
    if fit_k1:                                         # the prior row depends on k1 only
        rows, cols = np.append(rows, 2 * m), np.append(cols, 1)
    sparsity = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)),
                          shape=(2 * m + (1 if fit_k1 else 0), len(x0))).tocsr()
    lb = np.full(len(x0), -np.inf)
    ub = np.full(len(x0), np.inf)
    lb[0], ub[0] = 0.5 * f, 1.5 * f
    if fit_k1:
        lb[1], ub[1] = -0.5, 0.5
        x0[1] = min(max(x0[1], -0.499), 0.499)
    result = least_squares(residuals, x0, jac_sparsity=sparsity, loss=loss, f_scale=f_scale, x_scale=x_scale,
                           method='trf', tr_solver='lsmr', tr_options=tr_options or {}, max_nfev=max_nfev, ftol=1e-5, xtol=1e-7, bounds=(lb, ub))
    f_, k_, cams, pts = unpack(result.x)
    rvec, tvec = rvec.copy(), tvec.copy()
    rvec[var_cams], tvec[var_cams] = cams[:, :3], cams[:, 3:]
    return rvec, tvec, pts.copy(), float(f_), float(k_)


# -- the reconstruction state -----------------------------------------------------------------------------------------

class _Model:
    """Tracks, poses and points of one reconstruction; the methods are the incremental steps."""

    def __init__(self, width, height, xy, tracks, progress):
        self.w, self.h = width, height
        self.cx, self.cy = width / 2.0, height / 2.0
        self.n_frames = len(xy)
        self.progress = progress
        self.f, self.k1 = 1.0, 0.0
        # observation arrays, sorted by track
        self.obs_track, self.obs_frame, self.obs_xy = tracks
        self.n_tracks = int(self.obs_track.max()) + 1 if len(self.obs_track) else 0
        self.ptr = np.searchsorted(self.obs_track, np.arange(self.n_tracks + 1))
        order = np.argsort(self.obs_frame, kind='stable')
        fptr = np.searchsorted(self.obs_frame[order], np.arange(self.n_frames + 1))
        self.obs_by_frame = [order[fptr[i]:fptr[i + 1]] for i in range(self.n_frames)]
        self.obs_ok = np.ones(len(self.obs_track), dtype=bool)
        self.rvec = np.zeros((self.n_frames, 3))
        self.tvec = np.zeros((self.n_frames, 3))
        self.registered = np.zeros(self.n_frames, dtype=bool)
        self.pts = np.full((self.n_tracks, 3), np.nan)
        self.valid = np.zeros(self.n_tracks, dtype=bool)
        self.order = []

    # -- helpers
    def rot(self, i):
        import cv2
        return cv2.Rodrigues(self.rvec[i])[0]

    def centre(self, i):
        return -self.rot(i).T @ self.tvec[i]

    def centres(self):
        import cv2
        c = np.full((self.n_frames, 3), np.nan)
        for i in np.flatnonzero(self.registered):
            c[i] = -cv2.Rodrigues(self.rvec[i])[0].T @ self.tvec[i]
        return c

    def norm(self, obs):
        return undistort_norm(self.obs_xy[obs], self.f, self.k1, self.cx, self.cy)

    def projection(self, i):
        P = np.zeros((3, 4))
        P[:, :3] = self.rot(i)
        P[:, 3] = self.tvec[i]
        return P

    def gather(self, tids):
        """Observation indices of the given tracks, concatenated, with the segment (track slot) of each."""
        starts, lens = self.ptr[tids], self.ptr[np.asarray(tids) + 1] - self.ptr[tids]
        total = int(lens.sum())
        seg = np.repeat(np.arange(len(tids)), lens)
        offs = np.concatenate([[0], np.cumsum(lens)[:-1]]) if len(lens) else np.zeros(0, dtype=int)
        idx = np.arange(total) - np.repeat(offs, lens) + np.repeat(starts, lens)
        return idx, seg

    # -- triangulation
    def _accept(self, tids, X, o1, o2, P1, P2):
        """Keep triangulated points with positive depth in both views, small reprojection error and a wide enough
        angle; store them. Returns the number accepted."""
        ok = np.all(np.isfinite(X), axis=1)
        X = np.where(ok[:, None], X, 0.0)
        err_max = np.zeros(len(X))
        for P, o in ((P1, o1), (P2, o2)):
            pc = np.einsum('nij,nj->ni', P[:, :, :3], X) + P[:, :, 3]
            z = pc[:, 2]
            ok &= z > 1e-9
            xn = pc[:, :2] / np.where(z > 1e-9, z, 1.0)[:, None]
            xd = xn * (1.0 + self.k1 * np.sum(xn * xn, axis=1, keepdims=True))
            err = np.linalg.norm(self.f * xd + [self.cx, self.cy] - self.obs_xy[o], axis=1)
            err_max = np.maximum(err_max, err)
        c1 = -np.einsum('nji,nj->ni', P1[:, :, :3], P1[:, :, 3])
        c2 = -np.einsum('nji,nj->ni', P2[:, :, :3], P2[:, :, 3])
        r1, r2 = X - c1, X - c2
        cosang = np.sum(r1 * r2, axis=1) / (np.linalg.norm(r1, axis=1) * np.linalg.norm(r2, axis=1) + 1e-12)
        angle = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
        ok &= (err_max < INLIER_PX + 1.0) & (angle >= MIN_TRI_ANGLE_DEG)
        self.pts[tids[ok]] = X[ok]
        self.valid[tids[ok]] = True
        return int(ok.sum())

    def triangulate_frame(self, f):
        """Triangulate the not-yet-3D tracks seen in frame f against their widest-baseline registered partner."""
        obs = self.obs_by_frame[f]
        tids = self.obs_track[obs]
        sel = ~self.valid[tids]
        obs, tids = obs[sel], tids[sel]
        if len(tids) == 0:
            return 0
        idx, seg = self.gather(tids)
        fr = self.obs_frame[idx]
        cen = self.centres()
        dist = np.linalg.norm(cen[fr] - cen[f], axis=1)
        usable = self.registered[fr] & (fr != f) & self.obs_ok[idx] & np.isfinite(dist)
        score = np.where(usable, dist, -1.0)
        order = np.lexsort((-score, seg))
        first = np.concatenate([[0], np.cumsum(np.bincount(seg, minlength=len(tids)))[:-1]])
        partner = idx[order[first]]
        good = score[order[first]] >= 0
        obs, tids, partner = obs[good], tids[good], partner[good]
        if len(tids) == 0:
            return 0
        Pf = self.projection(f)
        cache = {}
        P2 = np.stack([cache.setdefault(int(p), self.projection(int(p))) for p in self.obs_frame[partner]])
        P1 = np.broadcast_to(Pf, P2.shape)
        X = triangulate_batch(P1, P2, self.norm(obs), self.norm(partner))
        return self._accept(tids, X, obs, partner, P1, P2)

    # -- initialisation
    def initialise(self, a, b, ia, ib):
        """Set frames a and b from the essential matrix of their matches (indices ia, ib are observation indices of
        the matches in a and b). Returns True on success."""
        import cv2
        best = None

        def trial(fov):
            f = max(self.w, self.h) / (2.0 * math.tan(math.radians(fov) / 2.0))
            x1 = undistort_norm(self.obs_xy[ia], f, 0.0, self.cx, self.cy)
            x2 = undistort_norm(self.obs_xy[ib], f, 0.0, self.cx, self.cy)
            E, mask = cv2.findEssentialMat(x1, x2, np.eye(3), cv2.RANSAC, 0.999, 1.5 / f)
            if E is None or mask is None or E.shape[0] < 3:
                return None
            E = E[:3]
            n, R, t, _ = cv2.recoverPose(E, x1, x2, np.eye(3), mask=mask.copy())
            if n < 20:
                return None
            P1 = np.hstack([np.eye(3), np.zeros((3, 1))])
            P2 = np.hstack([R, t.reshape(3, 1)])
            X = triangulate_batch(np.broadcast_to(P1, (len(x1), 3, 4)), np.broadcast_to(P2, (len(x1), 3, 4)), x1, x2)
            err = np.zeros(len(x1))
            for P, x in ((P1, x1), (P2, x2)):
                pc = X @ P[:, :3].T + P[:, 3]
                z = np.where(pc[:, 2] > 1e-9, pc[:, 2], np.nan)
                e = f * np.linalg.norm(pc[:, :2] / z[:, None] - x, axis=1)
                err = np.maximum(err, np.nan_to_num(e, nan=1e3))
            return float(np.mean(np.minimum(err, 4.0))), f, R, t.ravel()

        lo, hi = FOV_SWEEP_DEG
        for fov in np.arange(lo, hi + 1e-9, 2.5):
            r = trial(float(fov))
            if r and (best is None or r[0] < best[0]):
                best = (*r, float(fov))
        if best is None:
            return False
        for fov in np.arange(best[4] - 2.0, best[4] + 2.0 + 1e-9, 0.5):
            r = trial(float(fov))
            if r and r[0] < best[0]:
                best = (*r, float(fov))
        _, f, R, t, _ = best
        self.f, self.k1 = f, 0.0
        self.registered[[a, b]] = True
        self.order = [a, b]
        self.rvec[a] = 0.0
        self.tvec[a] = 0.0
        self.rvec[b] = cv2.Rodrigues(R)[0].ravel()
        self.tvec[b] = t
        self.triangulate_frame(b)
        return int(self.valid.sum()) >= 30

    # -- registration
    def register(self, f):
        """PnP RANSAC of frame f against the current points. Returns True when it registered."""
        import cv2
        obs = self.obs_by_frame[f]
        obs = obs[self.valid[self.obs_track[obs]]]
        if len(obs) < 15:
            return False
        obj = self.pts[self.obs_track[obs]]
        img = self.norm(obs)
        ok, rvec, tvec, inl = cv2.solvePnPRansac(obj, img, np.eye(3), None, iterationsCount=300,
                                                 reprojectionError=2.5 / self.f, confidence=0.999)
        if not ok or inl is None or len(inl) < 15:
            return False
        inl = inl.ravel()
        rvec, tvec = cv2.solvePnPRefineLM(obj[inl], img[inl], np.eye(3), None, rvec, tvec)
        self.rvec[f], self.tvec[f] = rvec.ravel(), tvec.ravel()
        if not np.all(np.isfinite(self.rvec[f])) or np.linalg.norm(self.tvec[f]) > 1e6:
            return False
        self.registered[f] = True
        self.order.append(f)
        self.triangulate_frame(f)
        return True

    # -- adjustment
    def adjust(self, var_frames, max_nfev, fit_k1, point_cap=40000):
        """Bundle adjustment of `var_frames` (None: every registered frame) and the points they see."""
        reg = self.registered
        valid_obs = self.obs_ok & reg[self.obs_frame] & self.valid[self.obs_track]
        if var_frames is None:
            var = np.flatnonzero(reg)
            tids = np.unique(self.obs_track[valid_obs])
        else:
            var = np.asarray(var_frames)
            in_var = np.zeros(self.n_frames, dtype=bool)
            in_var[var] = True
            tids = np.unique(self.obs_track[valid_obs & in_var[self.obs_frame]])
        if len(tids) == 0:
            return
        idx, seg = self.gather(tids)
        keep = self.obs_ok[idx] & reg[self.obs_frame[idx]]
        idx, seg = idx[keep], seg[keep]
        # a plain least-squares problem converges in a handful of iterations, so the gross outliers are left out first
        uv, depth = project(self.pts[tids][seg], self.rvec[self.obs_frame[idx]], self.tvec[self.obs_frame[idx]], self.f,
                            self.k1, self.cx, self.cy)
        near = (np.linalg.norm(uv - self.obs_xy[idx], axis=1) < OUTLIER_PX) & (depth > 0)
        idx, seg = idx[near], seg[near]
        counts = np.bincount(seg, minlength=len(tids))
        good = counts >= 2
        if good.sum() > point_cap:
            cut = np.sort(counts[good])[::-1][point_cap - 1]
            good &= counts >= cut
        remap = -np.ones(len(tids), dtype=int)
        remap[good] = np.arange(int(good.sum()))
        sel = good[seg]
        idx, local = idx[sel], remap[seg[sel]]
        tids = tids[good]
        rvec, tvec, pts, f, k1 = bundle_adjust(self.rvec, self.tvec, self.pts[tids], self.f, self.k1, self.cx, self.cy,
                                               self.obs_frame[idx], local, self.obs_xy[idx], var, fit_k1, max_nfev)
        self.rvec, self.tvec, self.f, self.k1 = rvec, tvec, f, k1
        self.pts[tids] = pts

    def prune(self, thr=INLIER_PX):
        """Flag observations farther than thr pixels from their point; drop points left with under 2 good
        observations; returns the RMS of the remaining observations."""
        reg = self.registered
        use = np.flatnonzero(reg[self.obs_frame] & self.valid[self.obs_track])
        tr, fr = self.obs_track[use], self.obs_frame[use]
        uv, depth = project(self.pts[tr], self.rvec[fr], self.tvec[fr], self.f, self.k1, self.cx, self.cy)
        err = np.linalg.norm(uv - self.obs_xy[use], axis=1)
        bad = (err > thr) | (depth <= 0) | ~np.isfinite(err)
        self.obs_ok[use] = ~bad
        count = np.bincount(tr[~bad], minlength=self.n_tracks)
        self.valid &= count >= 2
        keep = ~bad & self.valid[tr]
        return float(math.sqrt(np.mean(err[keep] ** 2))) if keep.any() else float('inf')


# -- driver -----------------------------------------------------------------------------------------------------------

def build_tracks(n_frames, counts, pairs):
    """Tracks from verified pair matches. `counts[i]` is the keypoint count of frame i; `pairs` maps (i, j) to
    (indices in i, indices in j). Returns (track id, frame, keypoint) arrays of the observations of every track that
    has at least two observations and never two in one frame."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    offsets = np.concatenate([[0], np.cumsum(counts)])
    total = int(offsets[-1])
    a = np.concatenate([offsets[i] + m[0] for (i, j), m in pairs.items()])
    b = np.concatenate([offsets[j] + m[1] for (i, j), m in pairs.items()])
    graph = coo_matrix((np.ones(len(a), dtype=np.int8), (a, b)), shape=(total, total))
    _, label = connected_components(graph, directed=False)
    used = np.zeros(total, dtype=bool)
    used[a] = used[b] = True
    nodes = np.flatnonzero(used)
    frame = np.searchsorted(offsets, nodes, side='right') - 1
    kp = nodes - offsets[frame]
    lab = label[nodes]
    _, inv = np.unique(lab, return_inverse=True)
    key = inv.astype(np.int64) * n_frames + frame
    _, kinv, kcount = np.unique(key, return_inverse=True, return_counts=True)
    dup = np.zeros(inv.max() + 1, dtype=bool)
    dup[inv[kcount[kinv] > 1]] = True
    keep = ~dup[inv]
    inv, frame, kp = inv[keep], frame[keep], kp[keep]
    size = np.bincount(inv, minlength=inv.max() + 1)
    keep = size[inv] >= 2
    inv, frame, kp = inv[keep], frame[keep], kp[keep]
    _, track = np.unique(inv, return_inverse=True)
    order = np.argsort(track, kind='stable')
    return track[order], frame[order], kp[order]


def reconstruct(read_gray, read_color, n_frames, width, height, progress=None, threads=4):
    """Camera, poses and sparse points of a keyframe sequence.

    read_gray(i) -> uint8 grey image and read_color(i) -> BGR image of frame i (both at the keyframe size width x
    height). `progress(text)` receives stage messages. Raises ScanError('scan_failed') when no reconstruction can be
    started. Returns a Reconstruction."""
    import cv2
    cv2.setRNGSeed(0)
    say = progress or (lambda text: None)
    timings = {}
    t0 = time.monotonic()
    with ThreadPoolExecutor(threads) as pool:
        feats = list(pool.map(lambda i: detect(read_gray(i)), range(n_frames)))
        timings['features_s'] = time.monotonic() - t0
        t0 = time.monotonic()
        todo = [(i, i + o) for o in PAIR_OFFSETS for i in range(n_frames - o)]
        results = pool.map(lambda p: (p, match_pair(feats[p[0]][0], feats[p[0]][1], feats[p[1]][0], feats[p[1]][1])), todo)
        pairs = {p: m for p, m in results if m is not None}
    timings['matching_s'] = time.monotonic() - t0
    say(f'Cameras: {len(pairs)} frame pairs matched')
    if not pairs:
        raise ScanError('scan_failed', 'No frames could be matched to each other. Move slower, use more light and '
                                       'film textured surfaces.')
    t0 = time.monotonic()
    track, frame, kp = build_tracks(n_frames, [len(f[0]) for f in feats], pairs)
    xy = np.array([feats[f][0][k] for f, k in zip(frame, kp)]) if len(frame) else np.zeros((0, 2))
    model = _Model(width, height, [f[0] for f in feats], (track, frame, xy), say)

    # initial pair: many matches, real parallax, not a plane
    maxdim = max(width, height)
    candidates = []
    for (i, j), (m1, m2) in pairs.items():
        if i >= min(n_frames, 40):
            continue
        disp = float(np.median(np.linalg.norm(feats[i][0][m1] - feats[j][0][m2], axis=1)))
        if len(m1) >= 60 and disp >= 0.02 * maxdim:
            candidates.append((len(m1) * min(disp / (0.08 * maxdim), 1.0), i, j, m1, m2))
    candidates.sort(key=lambda c: -c[0])
    scored = []
    for score, i, j, m1, m2 in candidates[:12]:
        H, mask = cv2.findHomography(feats[i][0][m1], feats[j][0][m2], cv2.RANSAC, 3.0)
        ratio = float(mask.mean()) if mask is not None else 1.0
        scored.append((score * (1.0 - ratio), i, j, m1, m2))
    scored.sort(key=lambda c: -c[0])
    started = False
    for _, a, b, m1, m2 in scored[:6]:
        # observation indices of the matches: track membership is keyed by (frame, keypoint)
        lookup = {}
        for o in model.obs_by_frame[a]:
            lookup[kp[o]] = o
        ia, ib = [], []
        look_b = {kp[o]: o for o in model.obs_by_frame[b]}
        for u, v in zip(m1, m2):
            if u in lookup and v in look_b:
                ia.append(lookup[u])
                ib.append(look_b[v])
        if len(ia) < 40:
            continue
        model.registered[:] = False
        model.valid[:] = False
        if model.initialise(a, b, np.array(ia), np.array(ib)):
            started = True
            break
    if not started:
        raise ScanError('scan_failed', 'The camera motion could not be started: no pair of frames had enough overlap '
                                       'and parallax. Move slower, use more light, film textured surfaces and walk '
                                       'sideways instead of turning on the spot.')
    model.adjust(None, 15, fit_k1=False)
    model.prune()
    say(f'Cameras: 2/{n_frames} registered')

    last_global, since_local, attempts = 2, 0, {}
    while True:
        remaining = np.flatnonzero(~model.registered)
        if len(remaining) == 0:
            break
        valid_obs = model.valid[model.obs_track]
        counts = np.bincount(model.obs_frame[valid_obs], minlength=n_frames)
        order = remaining[np.argsort(-counts[remaining], kind='stable')]
        progressed = False
        for f in order:
            if counts[f] < 15 or counts[f] < 1.25 * attempts.get(int(f), 0):
                continue
            attempts[int(f)] = int(counts[f])
            if model.register(int(f)):
                progressed = True
                since_local += 1
                break
        if not progressed:
            break
        n_reg = int(model.registered.sum())
        fit_k1 = n_reg >= 10
        if n_reg >= GLOBAL_GROWTH * last_global and n_reg >= 6:
            model.adjust(None, 15, fit_k1)
            model.prune()
            last_global, since_local = n_reg, 0
        elif since_local >= LOCAL_EVERY:
            model.adjust(model.order[-LOCAL_WINDOW:], 8, fit_k1)
            since_local = 0
        if n_reg % 10 == 0:
            say(f'Cameras: {n_reg}/{n_frames} registered')

    # final passes: global adjustment, outlier removal, a second triangulation pass for tracks that stayed 2D
    model.adjust(None, FINAL_NFEV, True)
    model.prune()
    for f in np.flatnonzero(model.registered):
        model.triangulate_frame(int(f))
    model.adjust(None, FINAL_NFEV, True)
    rms = model.prune(2.0)
    timings['sfm_s'] = time.monotonic() - t0
    n_reg = int(model.registered.sum())
    say(f'Cameras: {n_reg}/{n_frames} registered')

    # pack the result
    cen_ok = model.registered
    poses = []
    for i in range(n_frames):
        if cen_ok[i]:
            R = model.rot(i)
            T = np.eye(4)
            T[:3, :3] = R.T
            T[:3, 3] = -R.T @ model.tvec[i]
            poses.append(T)
        else:
            poses.append(None)
    pid = -np.ones(model.n_tracks, dtype=int)
    tids = np.flatnonzero(model.valid)
    pid[tids] = np.arange(len(tids))
    points = model.pts[tids]
    colors = np.zeros((len(tids), 3), dtype=np.uint8)
    assigned = np.zeros(len(tids), dtype=bool)
    visible = [None] * n_frames
    for i in np.flatnonzero(cen_ok):
        o = model.obs_by_frame[i]
        o = o[model.obs_ok[o] & model.valid[model.obs_track[o]]]
        visible[i] = (pid[model.obs_track[o]], model.obs_xy[o])
        image = read_color(int(i))
        todo = ~assigned[visible[i][0]]
        if todo.any():
            ids = visible[i][0][todo]
            uv = np.clip(np.round(visible[i][1][todo]).astype(int), [0, 0], [width - 1, height - 1])
            colors[ids] = image[uv[:, 1], uv[:, 0], ::-1]
            assigned[ids] = True
    camera = {'fx': model.f, 'fy': model.f, 'cx': model.cx, 'cy': model.cy, 'k1': model.k1, 'width': width,
              'height': height}
    return Reconstruction(camera=camera, poses=poses, points=points, colors=colors, reproj_px=rms, visible=visible,
                          timings=timings)
