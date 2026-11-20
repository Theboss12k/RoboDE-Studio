"""Hand-built, complete StudioBuild + DesignVersion for robot studio tests (no model, no CAD kernel, no network)."""
from fixtures_specs import bottle_spec

from studio import families, physics, sim
from studio.schemas import (BuildFile, BuildPart, Calc, Check, DesignVersion, Drive, ElectronicsItem, JointSizing,
                            MotorAlternative, MotorChoice, RobotConcept, SceneSpec, ShopPrice, SpecValue,
                            StructureStyle, StudioBuild, TwinJointLimit, TwinLimits)

SHOP_URL = 'https://robu.in/product/nema23-stepper-motor/'


def make_version() -> DesignVersion:
    spec = bottle_spec()
    base = physics.place_base(spec)
    reqs = physics.derive(spec, base)
    fam = families.get('serial_arm')
    p = fam.size_from_physics(reqs, spec, {})
    concept = RobotConcept(family='serial_arm', name='test', params=p.model_dump(), base=base)
    scene = SceneSpec(boxes=[], robot_base=base)
    res = sim.simulate(spec, reqs, scene, concept)
    return DesignVersion(version=2, created='2026-11-01T10:00:00Z', spec=spec, requirements=reqs, scene=scene,
                         robot=concept, urdf=fam.build_urdf(p, 'test'), sim=res, approved=True)


def _calc(result: float, unit: str) -> Calc:
    return Calc(formula='tau = m g r', inputs={}, result=result, unit=unit)


def make_build(version: DesignVersion | None = None) -> StudioBuild:
    version = version or make_version()
    price = ShopPrice(amount=2450.0, currency='INR', seen='2026-11-15', shop='robu.in', source_url=SHOP_URL,
                      quote='Rs. 2,450.00')
    drive = Drive(type='planetary', ratio=10, efficiency=0.9, name='NEMA23 planetary 10:1')
    shop_motor = MotorChoice(
        joint='shoulder', source='shop', alternative_id='robu_nema23', name='NEMA23 3 Nm stepper motor', shop='robu.in',
        url=SHOP_URL, price=price, motor_type='stepper',
        values=[SpecValue(key='rated_torque_nm', value=3.0, unit='N*m', status='verified', quote='Holding torque 3 Nm',
                          source_url=SHOP_URL),
                SpecValue(key='frame', text='NEMA 23', status='assumed', note='frame size from the NEMA table'),
                SpecValue(key='current_a', value=4.2, unit='A', status='verified', quote='4.2 A')],
        drive=drive, required={'continuous': 12.0, 'peak': 20.0}, provided={'continuous': 24.0, 'peak': 27.0},
        margins={'continuous': 2.0, 'peak': 1.35},
        alternatives=[MotorAlternative(id='robu_nema23', name='NEMA23 3 Nm stepper motor', shop='robu.in', url=SHOP_URL,
                                       price=price, passes=True)])
    defined_motor = MotorChoice(
        joint='wrist', source='defined', name='Stepper motor, wrist spec',
        values=[SpecValue(key='rated_torque_nm', value=0.8, unit='N*m', status='defined'),
                SpecValue(key='shaft', text='8 mm D-cut', status='defined')],
        drive=Drive(type='direct', ratio=1, efficiency=1.0), required={'continuous': 0.5})
    electronics = [
        ElectronicsItem(id='driver_shoulder', role='driver', name='DM556 stepper driver', source='shop', url=SHOP_URL,
                        for_joints=['shoulder'], shop='robu.in', price=price,
                        values=[SpecValue(key='current_a', value=5.6, unit='A', status='verified')],
                        checks=[Check(requirement_id='driver_current', name='driver current', required=5.04, actual=5.6,
                                      unit='A', passed=True)]),
        ElectronicsItem(id='psu', role='power_supply', name='24 V 10 A power supply', source='defined',
                        values=[SpecValue(key='power_w', value=240, unit='W', status='defined')])]
    files = [BuildFile(path='assembly.step', kind='step', sha256='a' * 64, bytes=10),
             BuildFile(path='urdf/robot.urdf', kind='urdf'), BuildFile(path='urdf/meshes/link_1.stl', kind='stl')]
    parts = [BuildPart(id='link_1', name='Upper arm tube', qty=1, material='6061 aluminium', process='machined',
                       source='custom', mass_kg=0.42, link='link_1', files=[BuildFile(path='parts/link_1.step',
                                                                                    kind='step')]),
             BuildPart(id='bearing_6805', name='Bearing 6805-2RS', qty=2, material='steel', process='standard',
                       source='standard', mass_kg=0.05, joint='shoulder', spec='6805-2RS, 25 x 37 x 7 mm'),
             BuildPart(id='motor_shoulder', name='NEMA23 3 Nm stepper motor', qty=1, process='purchased',
                       source='purchased', mass_kg=1.0, joint='shoulder', url=SHOP_URL)]
    return StudioBuild(
        build=1, design_version=version.version, created='2026-11-16T09:00:00Z', summary='Fixture build.',
        family='serial_arm', search_date='2026-11-15',
        sizing=[JointSizing(joint='shoulder', kind='revolute', static_torque=8.0, accel_torque=1.0, continuous=12.0,
                            peak=20.0, speed=1.0, calcs=[_calc(8.0, 'N*m')])],
        motors=[shop_motor, defined_motor], electronics=electronics,
        style=StructureStyle(style='machined_aluminium', proposed='machined_aluminium'), parts=parts,
        urdf=version.urdf,
        twin_limits=TwinLimits(joints=[TwinJointLimit(joint='shoulder', kind='revolute', lower=-1.5, upper=1.5,
                                                     max_speed=1.0, max_accel=5.0, max_torque=27.0)]),
        checks=[Check(requirement_id='motor_margin', name='shoulder motor margin', required=1.0, actual=2.0, unit='x',
                      passed=True)],
        files=files, approved=True, approved_at='2026-11-17T12:00:00Z')
