import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_contract_schemas_are_current():
    spec = importlib.util.spec_from_file_location('export_schemas', ROOT / 'scripts' / 'export_schemas.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.stale() == [], 'contracts/schemas is stale: run .venv/bin/python scripts/export_schemas.py'
    assert len(list((ROOT / 'contracts' / 'schemas').glob('*.json'))) == len(module.MODELS)
