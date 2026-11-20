"""Foundation: optional repair/fleet keys of a version, and FleetLimits bounds."""
import pytest
from pydantic import ValidationError

from fixtures_specs import bottle_spec
from studio.agents import scene
from studio.physics import derive, place_base
from studio.schemas import DesignVersion, FleetLimits, RepairLog, RobotConcept, SimResult
from studio.tasks.handlers import version_json
from studio_scripts import ROBOT, scene_dict


def _version(**extra):
    s = bottle_spec()
    return DesignVersion(version=1, created='now', spec=s, requirements=derive(s, place_base(s)),
                         scene=scene.SceneSpec.model_validate(scene_dict()), robot=RobotConcept(**ROBOT),
                         urdf='<robot/>', sim=SimResult(frames=[]), **extra)


def test_version_json_omits_unset_optional_keys():
    out = version_json(_version())
    assert 'repair' not in out and 'fleet' not in out
    assert out['version'] == 1 and out['approved'] is False


def test_version_json_keeps_repair_when_set():
    out = version_json(_version(repair=RepairLog(collisions_before=2)))
    assert out['repair']['collisions_before'] == 2 and out['repair']['attempts'] == []
    assert 'fleet' not in out


def test_fleet_limits_bounds():
    assert FleetLimits().max_robots == 2
    assert FleetLimits(max_robots=3).max_robots == 3
    for bad in (1, 4):
        with pytest.raises(ValidationError):
            FleetLimits(max_robots=bad)
