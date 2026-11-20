"""Grip force needed to hold an object while accelerating."""
from ..schemas import Calc, Quantity
from .statics import G


def force(grip, mass_kg, accel_m_s2, contacts=2, mu=0.4, safety_factor=1.5):
    """Required grip force as a Calc (N).

    parallel (friction): F = m (g + a) / (n mu) * SF per contact. vacuum/magnetic: holding force
    F = m (g + a) * SF. none/fork/hook: no clamping force; the result is the supported load m (g + a).
    """
    m, a, n, mu, sf = float(mass_kg), float(accel_m_s2), int(contacts), float(mu), float(safety_factor)
    load = m * (G + a)
    inputs = {'m': Quantity(value=m, unit='kg'), 'g': Quantity(value=G, unit='m/s^2'),
              'a': Quantity(value=a, unit='m/s^2'), 'SF': Quantity(value=sf, unit='')}
    if grip == 'parallel':
        inputs['n'] = Quantity(value=n, unit='contacts')
        inputs['mu'] = Quantity(value=mu, unit='')
        return Calc(formula='F = m (g + a) / (n mu) * SF', inputs=inputs, result=load / (n * mu) * sf, unit='N',
                    note='Friction grip: normal force per contact so friction carries weight plus acceleration.')
    if grip in ('vacuum', 'magnetic'):
        return Calc(formula='F = m (g + a) * SF', inputs=inputs, result=load * sf, unit='N',
                    note=f'{grip.capitalize()} holding force normal to the surface; friction not used.')
    return Calc(formula='W = m (g + a)', inputs={k: v for k, v in inputs.items() if k != 'SF'}, result=load,
                unit='N', note=f'{grip} support: no clamping force; value is the supported load.')
