"""Write the JSON Schemas of the domain models (single source of the domain types) to contracts/schemas/*.json.

    .venv/bin/python scripts/export_schemas.py          # rewrite contracts/schemas
    .venv/bin/python scripts/export_schemas.py --check  # exit 1 when contracts/ is stale

tests/test_contracts.py fails when the committed files differ from the models.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

from studio import schemas  # noqa: E402

MODELS = ['DesignVersion', 'TechnicalSpec', 'SceneSpec', 'RobotConcept', 'Requirement', 'Check', 'SimResult', 'Frame',
          'Candidate', 'Analysis', 'ManufacturerSpec', 'Difference', 'Question',
          'FleetLimits', 'FleetAssignment', 'FleetPlan', 'RepairLog', 'FleetHint']
OUT = ROOT / 'contracts' / 'schemas'


def render():
    """{file name: text} for every schema."""
    return {f'{name}.json': json.dumps(getattr(schemas, name).model_json_schema(), indent=2, ensure_ascii=False) + '\n'
            for name in MODELS}


def stale():
    """Names of files that are missing, different or no longer generated."""
    want = render()
    have = {p.name: p.read_text(encoding='utf-8') for p in OUT.glob('*.json')} if OUT.exists() else {}
    return sorted(n for n in want.keys() | have.keys() if want.get(n) != have.get(n))


def main(argv):
    if '--check' in argv:
        bad = stale()
        if bad:
            print('contracts/schemas is stale: ' + ', '.join(bad) + '; run scripts/export_schemas.py', file=sys.stderr)
        return 1 if bad else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for path in OUT.glob('*.json'):
        path.unlink()
    for name, text in render().items():
        (OUT / name).write_text(text, encoding='utf-8')
    print(f'wrote {len(MODELS)} schemas to {OUT.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
