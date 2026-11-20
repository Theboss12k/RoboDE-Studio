"""Manufacturer report: PDF contents, deterministic bytes, BOM CSV, package.zip."""
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader
from studio_fixtures import SHOP_URL, make_build, make_version

from studio.robot import report


@pytest.fixture(scope='module')
def vb():
    version = make_version()
    return version, make_build(version)


@pytest.fixture
def build_dir(tmp_path):
    d = tmp_path / 'b1'
    for name, data in (('assembly.step', b'STEP'), ('parts/link_1.step', b'p1'), ('parts/link_1.stl', b'p2'),
                       ('urdf/robot.urdf', b'<robot/>'), ('urdf/meshes/link_1.stl', b'mesh'), ('notes.txt', b'x')):
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
    return d


def pdf_text(path: Path) -> tuple[str, str]:
    r = PdfReader(str(path))
    text = '\n'.join(' '.join(p.extract_text().split()) for p in r.pages)
    links = [a.get_object().get('/A', {}).get('/URI') for p in r.pages for a in (p.get('/Annots') or [])]
    return text, ' '.join(str(u) for u in links)


def test_pdf_contents(vb, build_dir):
    version, build = vb
    bf = report.write_pdf(build, version, {'title': 'Bottle Arm'}, build_dir, [], build_dir / 'manufacturer.pdf')
    assert bf.path == 'manufacturer.pdf' and bf.kind == 'pdf' and len(bf.sha256) == 64 and bf.bytes > 1000
    text, links = pdf_text(build_dir / 'manufacturer.pdf')
    for needle in ('Bottle Arm', 'Build b1 from design v2', 'Approved by the user on 2026-11-17',
                   'Upper arm tube', 'Bearing 6805-2RS', '6805-2RS, 25 x 37 x 7 mm',
                   'NEMA23 3 Nm stepper motor', 'Stepper motor, wrist spec', 'DM556 stepper driver',
                   'seen 2026-11-15', 'Procure to this specification', 'verified', 'assumed', 'defined',
                   'no dynamics simulation', 'not been tested on hardware', 'not quotes', 'a' * 64):
        assert needle in text, needle
    assert SHOP_URL in links
    for section in ('Task and requirements met', 'Renders', 'Bill of materials', 'Standard parts', 'Files',
                    'Assumptions and limits'):
        assert section in text
    assert text.index('Renders') < text.index('Bill of materials') < text.index('Assumptions and limits')


def test_fallback_renders_are_images(vb, build_dir):
    version, build = vb
    report.write_pdf(build, version, {}, build_dir, [], build_dir / 'm.pdf')
    pages = PdfReader(str(build_dir / 'm.pdf')).pages
    page = next(p for p in pages if 'Renders' in p.extract_text())
    assert len(page.images) >= 1
    assert 'orthographic' in page.extract_text() or 'wide' in page.extract_text()
    n = sum(len(p.images) for p in pages)
    assert n == 4
    pts = [p for _, p in report.chain_at_zero(build.urdf)]
    assert max(report.overall_dimensions(pts)) > 0.1


def test_given_snapshots_replace_fallback(vb, build_dir):
    import io

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(1, 1))
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    version, build = vb
    snaps = [{'view': 'iso', 'png': buf.getvalue()}]
    report.write_pdf(build, version, {}, build_dir, snaps, build_dir / 's.pdf')
    pages = PdfReader(str(build_dir / 's.pdf')).pages
    assert sum(len(p.images) for p in pages) == 1


def test_pdf_deterministic(vb, build_dir):
    version, build = vb
    a = build_dir / 'a.pdf'
    b = build_dir / 'b.pdf'
    report.write_pdf(build, version, {'title': 'T'}, build_dir, [], a)
    report.write_pdf(build, version, {'title': 'T'}, build_dir, [], b)
    assert a.read_bytes() == b.read_bytes()


def test_long_table_paginates(vb, build_dir):
    version, build = vb
    big = build.model_copy(update={'parts': [build.parts[0].model_copy(update={'id': f'p{i}', 'name': f'Part {i} ' + 'long ' * 30})
                                             for i in range(120)]})
    bf = report.write_pdf(big, version, {}, build_dir, [], build_dir / 'big.pdf')
    text, _ = pdf_text(build_dir / 'big.pdf')
    assert 'Part 119' in text and bf.bytes > 1000


def test_bom_csv(vb, tmp_path):
    _, build = vb
    bf = report.write_bom_csv(build, tmp_path / 'bom.csv')
    assert bf.kind == 'csv'
    lines = (tmp_path / 'bom.csv').read_text().splitlines()
    assert lines[0].startswith('item,name,qty')
    assert len(lines) == 1 + len(build.parts) + len(build.electronics)
    assert any('Bearing 6805-2RS' in line for line in lines)


def test_package_zip(vb, build_dir):
    version, build = vb
    pdf = build_dir / 'manufacturer.pdf'
    report.write_pdf(build, version, {}, build_dir, [], pdf)
    report.write_bom_csv(build, build_dir / 'bom.csv')
    out = build_dir / 'package.zip'
    bf = report.write_package(build_dir, pdf, out)
    assert bf.kind == 'zip' and bf.path == 'package.zip'
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist())
    assert names == sorted(names) == ['assembly.step', 'bom.csv', 'manufacturer.pdf', 'parts/link_1.step',
                                      'parts/link_1.stl', 'urdf/meshes/link_1.stl', 'urdf/robot.urdf']
    out2 = build_dir / 'package2.zip'
    report.write_package(build_dir, pdf, out2)
    assert out.read_bytes() == out2.read_bytes()


def test_package_skips_missing(tmp_path):
    (tmp_path / 'x.pdf').write_bytes(b'%PDF')
    report.write_package(tmp_path, tmp_path / 'x.pdf', tmp_path / 'package.zip')
    assert zipfile.ZipFile(tmp_path / 'package.zip').namelist() == ['x.pdf']
