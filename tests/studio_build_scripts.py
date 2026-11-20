"""Scripted (fixture) answers for the robot-studio build: shop candidates that point at the saved pages in
tests/fixtures/shops/ and a structure-style answer. These are not model results and not real shop data."""
import json
from pathlib import Path

SHOPS = Path(__file__).parent / 'fixtures' / 'shops'
ROBU = 'https://robu.in/product/nema23-3nm'
SOL = 'https://www.omc-stepperonline.com/nema23-1-26nm'
PAGES = {ROBU: 'robu_nema23_3nm.html', SOL: 'stepperonline_23hs_1_26nm.html'}
STYLE_MATCH, SEARCH_MATCH = 'structure style of a robot', 'find real motors'


def _v(key, value, unit, quote):
    return {'key': key, 'value': value, 'unit': unit, 'quote': quote}


def motor_answer():
    """Two candidates: a NEMA 23 3 N.m stepper (robu page) and a weaker 1.26 N.m one (StepperOnline page)."""
    return {'candidates': [
        {'name': 'NEMA 23 Stepper Motor 3 N.m 76mm', 'url': ROBU, 'motor_type': 'stepper',
         'values': [_v('holding_torque_nm', 3.0, 'N.m', 'Holding Torque: 3.0 N.m'),
                    _v('current_a', 4.2, 'A', 'Rated Current: 4.2 A'), _v('voltage_v', 24, 'V', 'Rated Voltage: 24 V'),
                    _v('rated_speed_rpm', 600, 'rpm', 'Rated Speed: 600 rpm')],
         'price': {'amount': 2499, 'currency': 'INR', 'quote': 'Rs. 2,499.00 (inclusive of GST)', 'source_url': ROBU}},
        {'name': 'Nema 23 Stepper Motor 1.26Nm', 'url': SOL, 'motor_type': 'stepper',
         'values': [_v('holding_torque_nm', 1.26, 'N.m', 'Holding Torque 1.26 N.m'),
                    _v('current_a', 2.8, 'A', 'Rated Current/phase 2.8 A'), _v('voltage_v', 24, 'V', 'Rated Voltage 24 V')],
         'price': {'amount': 18.5, 'currency': 'USD', 'quote': 'Price: US $18.50', 'source_url': SOL}}]}


def style_answer(style='extrusion_printed'):
    return {'style': style, 'reasons': ['Light task, a printed bracket is enough (fixture answer).']}


def fake_download_map(directory: Path) -> Path:
    """Writes the STUDIO_FAKE_DOWNLOAD map {url: absolute path of the saved page} into `directory`."""
    path = Path(directory) / 'studio_pages.json'
    path.write_text(json.dumps({url: str(SHOPS / name) for url, name in PAGES.items()}, indent=1))
    return path
