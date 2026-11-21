"""The ar_calibrate and ar_estimate tasks, run through studio.tasks.runner.run like the Java server does (docs/PROTOCOL.md).

The photo is a header-only JPEG: the tasks read only its size and signature, never its pixels."""
import importlib.util
import io

import numpy as np
import pytest

from studio.ar import geometry as g
from studio.schemas import ArCalibration
from studio.tasks import protocol, runner
from test_ar_geometry import A4, FOCAL, HEIGHT, INTRINSICS, K, WIDTH, frontal, jpeg_bytes, look_at, rotation_error_deg


def photo(tmp_path, name='photo.jpg', payload=None):
    path = tmp_path / name
    path.write_bytes(jpeg_bytes(WIDTH, HEIGHT) if payload is None else payload)
    return path


def envelope(task, inp, tmp_path):
    return {'protocol': 1, 'task': task, 'input': inp, 'llm': {'roles': {}, 'profile': None}, 'out_dir': str(tmp_path / 'out')}


async def run(task, inp, tmp_path):
    data, _ = await runner.run(task, envelope(task, inp, tmp_path), protocol.Emitter(io.StringIO()))
    return data


def reference_pixels():
    T = look_at((A4[0] / 2, A4[1] / 2), 20.0, 35.0, 1.4)
    return T, g.project(np.column_stack([g.reference_points(A4), np.zeros(4)]), K, T)


async def test_ar_calibrate_end_to_end(tmp_path):
    T, pixels = reference_pixels()
    inp = {'image': {'path': str(photo(tmp_path))}, 'reference': {'corners_px': pixels.tolist(), 'size_m': A4},
           'intrinsics': None}
    data = await run('ar_calibrate', inp, tmp_path)
    calibration = ArCalibration.model_validate(data)
    assert calibration.scale_source == 'reference' and calibration.focal_source == 'reference'
    assert calibration.intrinsics.width == WIDTH and calibration.intrinsics.height == HEIGHT
    assert calibration.intrinsics.fx == pytest.approx(FOCAL, rel=0.01)
    estimate = np.array(calibration.world_from_camera)
    assert estimate.shape == (4, 4)
    assert np.linalg.norm(estimate[:3, 3] - T[:3, 3]) < 2e-3
    assert rotation_error_deg(estimate[:3, :3], T[:3, :3]) < 0.1
    assert calibration.camera_height_m == pytest.approx(estimate[2][3])
    assert calibration.camera_height_m == pytest.approx(1.4, abs=2e-3)
    assert calibration.residual_px is not None and calibration.residual_px < 0.1


async def test_ar_calibrate_frontal_uses_given_intrinsics(tmp_path):
    _, pixels = frontal()
    inp = {'image': {'path': str(photo(tmp_path))}, 'reference': {'corners_px': pixels.tolist(), 'size_m': A4},
           'intrinsics': INTRINSICS}
    data = await run('ar_calibrate', inp, tmp_path)
    assert data['focal_source'] == 'estimate' and data['warnings']
    inp['intrinsics'] = None
    data = await run('ar_calibrate', inp, tmp_path)
    assert data['focal_source'] == 'default' and data['warnings']


async def test_ar_calibrate_bad_reference_has_a_code(tmp_path):
    inp = {'image': {'path': str(photo(tmp_path))},
           'reference': {'corners_px': [[0, 0], [100, 100], [200, 200], [300, 300]], 'size_m': A4}}
    with pytest.raises(g.ArError) as raised:
        await run('ar_calibrate', inp, tmp_path)
    assert protocol.classify(raised.value) == ('value', 'ar_bad_reference')


async def test_ar_calibrate_validates_input(tmp_path):
    path = str(photo(tmp_path))
    with pytest.raises(ValueError):                                  # three corners
        await run('ar_calibrate', {'image': {'path': path}, 'reference': {'corners_px': [[0, 0]] * 3, 'size_m': A4}}, tmp_path)
    with pytest.raises(ValueError):                                  # non-positive size
        await run('ar_calibrate', {'image': {'path': path},
                                   'reference': {'corners_px': [[0, 0]] * 4, 'size_m': [0.3, 0]}}, tmp_path)
    text = photo(tmp_path, 'notes.jpg', b'this is not an image')
    with pytest.raises(ValueError):                                  # fails the intake check
        await run('ar_calibrate', {'image': {'path': str(text)},
                                   'reference': {'corners_px': [[0, 0]] * 4, 'size_m': A4}}, tmp_path)
    with pytest.raises(FileNotFoundError):
        await run('ar_calibrate', {'image': {'path': str(tmp_path / 'missing.jpg')},
                                   'reference': {'corners_px': [[0, 0]] * 4, 'size_m': A4}}, tmp_path)


@pytest.mark.skipif(importlib.util.find_spec('torch') is not None, reason='torch is installed; the backend is available')
async def test_ar_estimate_without_the_backend_is_coded(tmp_path):
    with pytest.raises(g.ArError) as raised:
        await run('ar_estimate', {'image': {'path': str(photo(tmp_path))}}, tmp_path)
    assert raised.value.code == 'ar_estimator_unavailable'
    assert protocol.classify(raised.value) == ('value', 'ar_estimator_unavailable')


def test_the_ar_schemas_are_exported():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import export_schemas
    assert {'ArIntrinsics', 'ArReference', 'ArCalibration', 'ArPlacement', 'ArScene'} <= set(export_schemas.MODELS)
