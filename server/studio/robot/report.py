"""Manufacturer report: PDF, BOM CSV and package.zip for an approved studio build. No LLM, no network.

Everything printed comes from the StudioBuild (numbers computed in code); prices are dated web estimates, never quotes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,  # noqa: E402
                                Table, TableStyle)

from ..schemas import BuildFile, BuildPart, DesignVersion, MotorChoice, SpecValue, StudioBuild  # noqa: E402

LIMITS = [
    'Sizing is kinematic, static-load and acceleration-load calculation in code (continuous torque with a 1.25 safety '
    'factor, peak with 1.5).',
    'No finite-element analysis (FEA) was run and no dynamics simulation was run.',
    'The robot has not been tested on hardware.',
    'Prices are dated web estimates as listed by the shop on the date shown, not quotes; confirm before ordering.',
    'Values marked verified were found in a quoted shop or datasheet page; assumed values come from a standard table or '
    'rule; defined values are specified here for procurement.',
]
PROCURE = 'Procure to this specification'
VIEWS = ('front', 'side', 'top', 'iso')

_ss = getSampleStyleSheet()
BODY = ParagraphStyle('body', parent=_ss['BodyText'], fontSize=9, leading=11)
CELL = ParagraphStyle('cell', parent=BODY, fontSize=7.5, leading=9, wordWrap='CJK')
HEAD = ParagraphStyle('head', parent=CELL, fontName='Helvetica-Bold', textColor=colors.white)
H1 = ParagraphStyle('h1', parent=_ss['Heading1'], fontSize=15, spaceBefore=6, spaceAfter=6)
H2 = ParagraphStyle('h2', parent=_ss['Heading2'], fontSize=11, spaceBefore=8, spaceAfter=4)
TITLE = ParagraphStyle('title', parent=_ss['Title'], fontSize=24, leading=28)


# --- helpers -------------------------------------------------------------------------------------------------------

def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _build_file(path: Path, root: Path, kind: str) -> BuildFile:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.name
    return BuildFile(path=rel, kind=kind, sha256=_sha(path), bytes=path.stat().st_size)


def _p(text, style=CELL) -> Paragraph:
    return Paragraph(escape(str(text)), style)


def _link(url: str, label: str = '') -> str:
    """Markup (not yet escaped as a whole) for a clickable link; empty url gives plain text."""
    label = escape(label or url)
    if not url:
        return label
    return f'<a href="{escape(url, {chr(34): "&quot;"})}" color="#1a56b0">{label}</a>'


def _fmt(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return '-'
    return f'{value:.{digits}g}'


def _table(rows: list[list], widths: list[float], header: bool = True) -> Table:
    data = [[c if not isinstance(c, str) else Paragraph(c, CELL) for c in row] for row in rows]
    if header:
        data[0] = [Paragraph(escape(str(c)), HEAD) if isinstance(c, str) else c for c in rows[0]]
    total = sum(widths)
    avail = A4[0] - 30 * mm
    widths = [w * avail / total for w in widths]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#9aa4b2')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
             ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
             ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]
    if header:
        style.append(('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2b3a4f')))
    t.setStyle(TableStyle(style))
    return t


def _date(stamp: str) -> str:
    return (stamp or '')[:10]


def _part_files(part: BuildPart) -> str:
    return ', '.join(f.path for f in part.files)


# --- fallback renders ------------------------------------------------------------------------------------------------

def _rot(r: float, p: float, y: float):
    import numpy as np
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    return rz @ ry @ rx


def chain_at_zero(urdf: str) -> list[tuple[str, list[float]]]:
    """(link, world position) for every link of the URDF with all joints at 0 (link frame origins)."""
    import numpy as np
    root = ElementTree.fromstring(urdf)
    tf: dict[str, object] = {}
    joints = []
    children = set()
    for j in root.findall('joint'):
        o = j.find('origin')
        xyz = [float(v) for v in (o.get('xyz', '0 0 0') if o is not None else '0 0 0').split()]
        rpy = [float(v) for v in (o.get('rpy', '0 0 0') if o is not None else '0 0 0').split()]
        m = np.eye(4)
        m[:3, :3] = _rot(*rpy)
        m[:3, 3] = xyz
        joints.append((j.find('parent').get('link'), j.find('child').get('link'), m))
        children.add(j.find('child').get('link'))
    for lk in root.findall('link'):
        if lk.get('name') not in children:
            tf[lk.get('name')] = np.eye(4)
    for _ in range(len(joints) + 1):
        for parent, child, m in joints:
            if parent in tf and child not in tf:
                tf[child] = tf[parent] @ m
    return [(n, [float(v) for v in t[:3, 3]]) for n, t in tf.items()]


def overall_dimensions(points: list[list[float]]) -> tuple[float, float, float]:
    if not points:
        return 0.0, 0.0, 0.0
    return tuple(max(p[i] for p in points) - min(p[i] for p in points) for i in range(3))  # type: ignore[return-value]


def render_fallback_views(urdf: str) -> list[tuple[str, bytes]]:
    """Orthographic front/side/top plus an isometric view of the link chain at q=0, as PNG bytes."""
    pts = [p for _, p in chain_at_zero(urdf)]
    dx, dy, dz = overall_dimensions(pts)
    iso = math.radians(35)

    def proj(view, p):
        x, y, z = p
        return {'front': (x, z), 'side': (y, z), 'top': (x, y),
                'iso': ((x - y) * math.cos(iso), z + (x + y) * math.sin(iso) * 0.5)}[view]
    dims = {'front': f'{dx:.3f} m wide x {dz:.3f} m high', 'side': f'{dy:.3f} m deep x {dz:.3f} m high',
            'top': f'{dx:.3f} m x {dy:.3f} m', 'iso': f'{dx:.3f} x {dy:.3f} x {dz:.3f} m (X x Y x Z)'}
    out = []
    for view in VIEWS:
        fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
        xy = [proj(view, p) for p in pts]
        ax.plot([a for a, _ in xy], [b for _, b in xy], '-o', color='#2b3a4f', markersize=4, linewidth=2)
        ax.set_aspect('equal', adjustable='datalim')
        ax.margins(0.25)
        ax.grid(True, linewidth=0.3)
        ax.set_title(f'{view}: {dims[view]}', fontsize=8)
        ax.tick_params(labelsize=6)
        buf = io.BytesIO()
        fig.savefig(buf, format='png', metadata={'Software': None})
        plt.close(fig)
        out.append((view, buf.getvalue()))
    return out


# --- PDF sections ----------------------------------------------------------------------------------------------------

def _cover(build: StudioBuild, version: DesignVersion, project: dict) -> list:
    title = project.get('title') or project.get('name') or version.spec.title
    parts = [Spacer(1, 40 * mm), Paragraph(escape(str(title)), TITLE), Spacer(1, 6 * mm),
             Paragraph(f'Build b{build.build} from design v{version.version}', H1),
             Paragraph(f'Robot family: {escape(build.family)}', BODY),
             Paragraph(f'Built: {escape(_date(build.created))}', BODY)]
    if build.search_date:
        parts.append(Paragraph(f'Shops searched: {escape(_date(build.search_date))}', BODY))
    if build.approved:
        parts.append(Paragraph(f'<b>Approved by the user on {escape(_date(build.approved_at or ""))}</b>', BODY))
    if build.summary:
        parts += [Spacer(1, 4 * mm), Paragraph(escape(build.summary), BODY)]
    return parts + [PageBreak()]


def _requirements(build: StudioBuild, version: DesignVersion) -> list:
    out = [Paragraph('Task and requirements met', H1), Paragraph(escape(version.spec.summary), BODY)]
    reqs = {r.id: r for r in version.requirements}
    checks = list(version.sim.checks) + list(build.checks)
    rows = [['Requirement', 'Required', 'Actual', 'Result', 'Detail']]
    seen = set()
    for c in checks:
        key = (c.requirement_id, c.name)
        if key in seen:
            continue
        seen.add(key)
        r = reqs.get(c.requirement_id)
        cmp = r.comparator if r else ''
        res = {True: 'pass', False: 'FAIL', None: 'not checked'}[c.passed]
        rows.append([c.name, f'{cmp} {_fmt(c.required)} {c.unit}'.strip(), f'{_fmt(c.actual)} {c.unit}'.strip(), res,
                     c.detail])
    if len(rows) == 1:
        rows += [[r.name, f'{r.comparator} {_fmt(r.target)} {r.unit}', '-', 'not checked', ''] for r in version.requirements]
    return out + [Spacer(1, 3 * mm), _table([[escape(str(c)) for c in r] for r in rows], [4, 2.5, 2.5, 1.6, 5])]


def _images(build, version, snapshots, build_dir):
    imgs: list[tuple[str, bytes]] = []
    for s in snapshots or []:
        raw = s.get('png') or s.get('data')
        path = s.get('path')
        if raw is None and path and Path(path).is_file():
            raw = Path(path).read_bytes()
        elif raw is None and path and (build_dir / path).is_file():
            raw = (build_dir / path).read_bytes()
        if raw:
            imgs.append((str(s.get('view', '')), raw))
    return imgs, bool(imgs)


def _renders(build, version, snapshots, build_dir) -> list:
    imgs, given = _images(build, version, snapshots, build_dir)
    out = [PageBreak(), Paragraph('Renders', H1)]
    if not given:
        out.append(Paragraph('No saved snapshots: orthographic views drawn from the link frames of the robot at joint '
                             'position 0, with overall dimensions of the frame chain (not including part thickness).',
                             BODY))
        imgs = render_fallback_views(build.urdf or version.urdf)
    for view, raw in imgs:
        w, h = ImageReader(io.BytesIO(raw)).getSize()
        width = 80 * mm
        out += [Paragraph(escape(view), H2), Image(io.BytesIO(raw), width=width, height=width * h / w)]
    return out


def _bom_rows(build: StudioBuild) -> list[list[str]]:
    rows = []
    for i, p in enumerate(build.parts, 1):
        rows.append([str(i), p.name, str(p.qty), p.material, p.process, f'{p.mass_kg:.3f}', _part_files(p)])
    return rows


def _bom(build: StudioBuild) -> list:
    rows = [['Item', 'Name', 'Qty', 'Material', 'Process', 'Mass (kg)', 'File']] + _bom_rows(build)
    total = sum(p.mass_kg * p.qty for p in build.parts)
    return [PageBreak(), Paragraph('Bill of materials', H1),
            _table([[escape(c) for c in r] for r in rows], [1, 4, 1, 2.5, 2, 1.8, 4]),
            Paragraph(f'Total mass of listed parts: {total:.3f} kg', BODY)]


def _spec_lines(values: list[SpecValue]) -> str:
    lines = []
    for v in values:
        val = v.text or _fmt(v.value, 5)
        unit = f' {escape(v.unit)}' if v.unit and not v.text else ''
        lines.append(f'{escape(v.key)}: {escape(val)}{unit} [{v.status}]')
    return '<br/>'.join(lines)


def _price(price) -> str:
    if price is None:
        return 'no price listed'
    amt = f'{price.amount:g} {escape(price.currency)}' if price.amount is not None else 'price not read'
    return f'{amt} (as listed by {escape(price.shop)}, seen {escape(_date(price.seen))})'


def _motor_block(m: MotorChoice) -> list:
    req = ', '.join(f'{k} {_fmt(v)}' for k, v in m.required.items()) or '-'
    prov = ', '.join(f'{k} {_fmt(v)}' for k, v in m.provided.items()) or '-'
    marg = ', '.join(f'{k} x{_fmt(v)}' for k, v in m.margins.items()) or '-'
    d = m.drive
    drive = f'{d.type}, ratio {_fmt(d.ratio)}, efficiency {_fmt(d.efficiency)}' + (f', {escape(d.name)}' if d.name else '')
    head = f'<b>{escape(m.joint)}</b>: {escape(m.name)}'
    if m.source == 'defined':
        head += f' <i>({PROCURE})</i>'
    shop = _link(m.url, m.shop or m.url) if m.url else escape(m.shop or '-')
    rows = [[f'{head}', ''],
            ['Shop', f'{shop}  {_price(m.price)}'],
            ['Specs', _spec_lines(m.values) or '-'],
            ['Drive', drive],
            ['Required (joint side)', escape(req)], ['Provided (joint side)', escape(prov)], ['Margin', escape(marg)]]
    if m.alternatives:
        rows.append(['Alternatives', escape('; '.join(f'{a.name} ({a.shop})' for a in m.alternatives))])
    t = _table([[Paragraph(c, CELL) if isinstance(c, str) else c for c in r] for r in rows], [2, 8], header=False)
    t.setStyle(TableStyle([('SPAN', (0, 0), (-1, 0)), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e3e8ef'))]))
    return [KeepTogether([t, Spacer(1, 3 * mm)])]


def _drivetrain(build: StudioBuild) -> list:
    out = [PageBreak(), Paragraph('Motors, gearboxes, drivers, controller and power supply', H1),
           Paragraph('Motors and gearboxes', H2)]
    for m in build.motors:
        out += _motor_block(m)
    for role, label in (('driver', 'Drivers'), ('controller', 'Controller'), ('power_supply', 'Power supply'),
                        ('battery', 'Battery'), ('other', 'Other electronics')):
        items = [e for e in build.electronics if e.role == role]
        if not items:
            continue
        out.append(Paragraph(label, H2))
        rows = [['Model', 'Qty', 'Shop and price', 'Specs', 'Checks']]
        for e in items:
            name = escape(e.name) + (f'<br/><i>{PROCURE}</i>' if e.source == 'defined' else '')
            shop = (_link(e.url, e.shop or e.url) if e.url else escape(e.shop or '-')) + '<br/>' + _price(e.price)
            checks = '<br/>'.join(escape(f'{c.name}: {_fmt(c.actual)} vs {_fmt(c.required)} {c.unit} -> '
                                         f'{ {True: "pass", False: "FAIL", None: "not checked"}[c.passed] }')
                                  for c in e.checks) or '-'
            rows.append([name, str(e.qty), shop, _spec_lines(e.values) or '-', checks])
        out.append(_table([[c if i == 0 else c for c in r] for i, r in enumerate(rows)], [3, 0.8, 3.5, 4, 4]))
    return out


def _defined(build: StudioBuild) -> list:
    items = [(m.joint, m.name, m.values) for m in build.motors if m.source == 'defined']
    items += [(e.role, e.name, e.values) for e in build.electronics if e.source == 'defined']
    out = [PageBreak(), Paragraph(f'Defined specifications: {PROCURE}', H1)]
    if not items:
        return out + [Paragraph('Every motor and electronics item was found at a shop; none is defined only by '
                                'specification.', BODY)]
    out.append(Paragraph('No suitable product was found for these items. Buy any part that meets every value below '
                         '(procure to this specification).', BODY))
    rows = [['For', 'Item', 'Required specification']]
    rows += [[escape(a), escape(b), _spec_lines(c) or '-'] for a, b, c in items]
    return out + [_table(rows, [2, 4, 8])]


def _standard(build: StudioBuild) -> list:
    std = [p for p in build.parts if p.source in ('standard', 'purchased') or p.process in ('standard', 'purchased')]
    out = [Paragraph('Standard parts', H1)]
    if not std:
        return out + [Paragraph('No standard parts listed.', BODY)]
    rows = [['Name', 'Qty', 'Specification', 'Material', 'Link']]
    rows += [[escape(p.name), str(p.qty), escape(p.spec), escape(p.material), _link(p.url) if p.url else '-'] for p in std]
    return out + [_table(rows, [4, 1, 5, 2.5, 3])]


def _files(build: StudioBuild, build_dir: Path) -> list:
    rows = [['File', 'Kind', 'Bytes', 'SHA-256']]
    for f in sorted(build.files, key=lambda f: f.path):
        sha, size = f.sha256, f.bytes
        p = build_dir / f.path
        if not sha and p.is_file():
            sha, size = _sha(p), p.stat().st_size
        rows.append([escape(f.path), f.kind, str(size), sha or 'not available'])
    return [PageBreak(), Paragraph('Files', H1), _table(rows, [4.5, 1, 1.2, 9]),
            Paragraph('The PDF itself and package.zip are not listed: a file cannot contain its own hash.', BODY)]


def _limits(build: StudioBuild) -> list:
    out = [Paragraph('Assumptions and limits', H1)]
    out += [Paragraph('- ' + escape(t), BODY) for t in LIMITS]
    out += [Paragraph('- ' + escape(a), BODY) for a in dict.fromkeys(a for a in _assumptions(build))]
    return out


def _assumptions(build: StudioBuild):
    for m in build.motors:
        for v in m.values:
            if v.status == 'assumed' and v.note:
                yield f'{m.joint} {v.key}: {v.note}'


def _page_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont('Helvetica', 7)
    canvas.drawString(15 * mm, 8 * mm, f'RoboDE Studio manufacturer report, build b{doc.build_no}')
    canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f'Page {doc.page}')
    canvas.restoreState()


def write_pdf(build: StudioBuild, version: DesignVersion, project: dict, build_dir: Path, snapshots: list[dict],
              out_path: Path) -> BuildFile:
    """Write the manufacturer PDF to out_path (deterministic: same input, same bytes)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=16 * mm, invariant=1, title=f'Build b{build.build} manufacturer report',
                            author='RoboDE Studio')
    doc.build_no = build.build
    story = (_cover(build, version, project) + _requirements(build, version) + _renders(build, version, snapshots, build_dir)
             + _bom(build) + _drivetrain(build) + _defined(build) + [PageBreak()] + _standard(build)
             + _files(build, build_dir) + [PageBreak()] + _limits(build))
    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return _build_file(out_path, build_dir, 'pdf')


def write_bom_csv(build: StudioBuild, out_path: Path) -> BuildFile:
    """Parts, then electronics, as CSV (UTF-8, LF line ends)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['item', 'name', 'qty', 'material', 'process', 'mass_kg', 'source', 'file', 'spec', 'url'])
    n = 0
    for p in build.parts:
        n += 1
        w.writerow([n, p.name, p.qty, p.material, p.process, f'{p.mass_kg:.4f}', p.source, _part_files(p), p.spec, p.url])
    for e in build.electronics:
        n += 1
        spec = '; '.join(f'{v.key}={v.text or v.value} {v.unit} [{v.status}]'.strip() for v in e.values)
        w.writerow([n, e.name, e.qty, '', 'purchased', '', e.source, '', spec, e.url])
    out_path.write_text(buf.getvalue(), encoding='utf-8', newline='')
    return _build_file(out_path, out_path.parent, 'csv')


def write_package(build_dir: Path, pdf: Path, out_path: Path) -> BuildFile:
    """ZIP of the PDF, assembly.step, parts/, urdf/ (with meshes) and bom.csv: existing files only, sorted, fixed times."""
    entries: dict[str, Path] = {}
    if pdf.is_file():
        entries[pdf.name] = pdf
    for name in ('assembly.step', 'bom.csv'):
        if (build_dir / name).is_file():
            entries[name] = build_dir / name
    for folder in ('parts', 'urdf'):
        for p in (build_dir / folder).rglob('*') if (build_dir / folder).is_dir() else []:
            if p.is_file():
                entries[p.relative_to(build_dir).as_posix()] = p
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, entries[name].read_bytes())
    return _build_file(out_path, build_dir, 'zip')
