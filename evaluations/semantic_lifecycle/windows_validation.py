"""External (Windows / LibreOffice) validation harness for the narrow semantic lifecycle.

The harness only *calls* the existing runtime APIs and records what they do:
`confirm_semantic_layout`, `open_semantic_flow`, `plan_semantic_transition`,
`build_semantic_candidate` and `publish_semantic_bundle` (plus the shared-flow
preparation APIs in `prepare`). It never changes semantic authority, owner or
publication contracts, never rewrites its inputs, and never turns a runtime
refusal into success.

    python -m evaluations.semantic_lifecycle.windows_validation prepare-synthetic --output-dir SRC [--libreoffice-shape]
    python -m evaluations.semantic_lifecycle.windows_validation prepare-fonts --output-dir FONTS [--from-a TTF --from-b TTF]
    python -m evaluations.semantic_lifecycle.windows_validation prepare --source-pdf PDF --spec SPEC.json --font-a A.ttf --output-dir PREP
    python -m evaluations.semantic_lifecycle.windows_validation run --pdf PDF --sidecar JSON --font-a A.ttf --font-b B.ttf --output-dir OUT
    python -m evaluations.semantic_lifecycle.windows_validation run --mode underline --pdf PDF --sidecar JSON --font-a A.ttf --font-b B.ttf --output-dir OUT
    python -m evaluations.semantic_lifecycle.windows_validation run --mode strikeout --pdf PDF --sidecar JSON --font-a A.ttf --font-b B.ttf --output-dir OUT

`run` without `--mode` is the text-only lifecycle (§24) exactly as before. `--mode underline` runs the
narrow semantic underline lifecycle (§28) on the same preparation: explicit `decorations.add` (version 2 → 3),
two no-ops, edit remap, recipe, style, font, publication A, removal from bundle A, publication B.
`--mode strikeout` runs the narrow semantic strikeout lifecycle (§32, version 4): explicit strikeout add
(version 2 → 4), two no-ops, edit remap, recipe, style, font, publication A, removal from bundle A, a disjoint
underline + strikeout mixed state, its no-op, strikeout removal (stays version 4), publication B, a small
v2 → v3 → v4 route control, recipe-sign/separation/overlap refusals and tampering.

Stage statuses: PASS (expected success), REFUSED (runtime refused for a scope or
safety reason), SKIPPED (not applicable or a prerequisite did not pass), FAIL
(behaviour differs from the expectation). Exit codes: 0 PASS, 1 FAIL, 2 usage
error / output collision, 3 REFUSED or UNSUPPORTED_TARGET, 4 INCOMPLETE.

The PR that added this harness did not execute it on Windows; a result is
Windows evidence only when `result.json` says `windows_execution: true`. The PR that added the underline
mode did not execute it on Windows either. For the strikeout mode, too, only a result with
`windows_execution: true` is Windows evidence.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction as F
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 1
PASS, REFUSED, SKIPPED, FAIL = 'PASS', 'REFUSED', 'SKIPPED', 'FAIL'
EXIT = dict(PASS=0, FAIL=1, USAGE=2, REFUSED=3, UNSUPPORTED_TARGET=3, INCOMPLETE=4)
DEFAULT_PLAN = dict(edit=dict(start=0, end=0, text='A'), style=dict(tracking='1/4'),
                    next_edit=dict(start=0, end=0, text='B'))
DISCLAIMER = ('Windows execution has not been performed by the PR that added this harness. '
              'This report is evidence only for the execution it describes.')
# Underline mode (§28). The recipe is the §27/§28 caller-confirmed validation recipe, not a new default.
RECIPE = {'offset_em': '13/120', 'thickness_em': '7/120'}
DEFAULT_UNDERLINE_PLAN = dict(underline_add=dict(start=0, end=3, recipe=dict(RECIPE)),
                              underline_edit=dict(start=1, end=1, text='A'),
                              underline_recipe={'offset_em': '1/10', 'thickness_em': '1/20'},
                              underline_style=dict(font_size='13', tracking='1/4'))
UNDERLINE_DISCLAIMER = 'Windows underline execution has not been performed by the PR that added the underline mode.'
# Strikeout mode (§31/§32, semantic version 4). The recipe is the §31/§32 caller-confirmed evidence recipe, not a
# standard. Ranges are chosen on the synthetic text: `A B` (add), `AA B` after the edit; the mixed state uses
# disjoint visible characters (underline on the first A, strikeout on B).
STRIKEOUT_RECIPE = {'offset_em': '-3/10', 'thickness_em': '1/20'}
DEFAULT_STRIKEOUT_PLAN = dict(strikeout_add=dict(start=0, end=3, recipe=dict(STRIKEOUT_RECIPE)),
                              strikeout_edit=dict(start=1, end=1, text='A'),
                              strikeout_recipe={'offset_em': '-1/4', 'thickness_em': '1/20'},
                              strikeout_style=dict(font_size='13', tracking='1/4'),
                              mixed_underline=dict(start=0, end=1, recipe=dict(RECIPE)),
                              mixed_strikeout=dict(start=3, end=4, recipe=dict(STRIKEOUT_RECIPE)),
                              control_underline=dict(start=0, end=1, recipe=dict(RECIPE)),
                              control_strikeout=dict(start=2, end=3, recipe=dict(STRIKEOUT_RECIPE)))
STRIKEOUT_DISCLAIMER = 'Windows strikeout execution has not been performed by the PR that added the strikeout mode.'
VALIDATION_KIND = dict(text='semantic-text-lifecycle', underline='semantic-underline-lifecycle',
                       strikeout='semantic-strikeout-lifecycle')


class Failure(Exception):
    """The runtime behaved differently from the expectation (stage FAIL)."""


class Skip(Exception):
    """The stage does not apply to this input or environment (stage SKIPPED)."""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def runtime_digest():
    """Same algorithm as the docs: SHA-256 over sorted pdfeditor/*.py (name + NUL + bytes)."""
    value = hashlib.sha256()
    for path in sorted((ROOT / 'pdfeditor').glob('*.py')):
        value.update(path.name.encode() + b'\0' + path.read_bytes())
    return value.hexdigest()


# --- fonts -------------------------------------------------------------------------------------------------------

def synthetic_font(family, *, width, box):
    """Deterministic static simple unhinted TrueType with A, B and space (same construction as the tests)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    builder = FontBuilder(1000, isTTF=True)
    names = ['.notdef', 'A', 'B', 'space']
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({65: 'A', 66: 'B', 32: 'space'})
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        if name != 'space':
            a, b, c, d = box
            pen.moveTo((a, b)); pen.lineTo((c, b)); pen.lineTo((c, d)); pen.lineTo((a, d)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({n: (width, 0) for n in names})
    builder.setupHorizontalHeader(ascent=600, descent=-200)
    builder.setupNameTable(dict(familyName=family, styleName='Regular', uniqueFontIdentifier=family + '-v1',
                                fullName=family, psName=family))
    builder.setupOS2(sTypoAscender=600, sTypoDescender=-200, usWinAscent=600, usWinDescent=200, fsType=0)
    builder.setupPost(); builder.setupMaxp()
    builder.font['head'].created = builder.font['head'].modified = 2082844800
    out = BytesIO(); builder.save(out)
    return out.getvalue()


def unhinted_subset(path):
    """A/B/space subset of an installed TrueType font with hinting removed (local derivative, never committed)."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    font = TTFont(path, recalcTimestamp=False)
    options = subset.Options()
    options.hinting = False
    options.notdef_outline = True
    options.recalc_timestamp = False
    options.layout_features = []
    options.name_IDs = ['*']
    worker = subset.Subsetter(options=options)
    worker.populate(unicodes=[0x20, 0x41, 0x42])
    worker.subset(font)
    out = BytesIO(); font.save(out)
    return out.getvalue()


def check_font(path):
    """The runtime's own static-TT and character-scope gate for an asset."""
    from pdfeditor import semantic_measure as measure
    from pdfeditor.shaped_font import ShapedFont
    font = ShapedFont(path)
    try:
        measure.require_static_tt(font)
        for char in 'AB ':
            measure.nominal_glyph(char, font)
    finally:
        font.font.close()


def prepare_fonts(args):
    out = Path(args.output_dir)
    if out.exists():
        return usage(f'output directory exists: {out}')
    out.mkdir(parents=True)
    record = {}
    for name, source, synthetic in (('font-a.ttf', args.from_a, dict(family='SemanticProof', width=600,
                                                                      box=(0, 0, 500, 600))),
                                    ('font-b.ttf', args.from_b, dict(family='SemanticAlternate', width=500,
                                                                      box=(50, -100, 400, 700)))):
        data = unhinted_subset(source) if source else synthetic_font(**synthetic)
        (out / name).write_bytes(data)
        check_font(out / name)
        record[name] = dict(sha256=sha(out / name), derived_from=Path(source).name if source else 'synthetic',
                            source_sha256=sha(source) if source else None)
    (out / 'fonts.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))
    return 0


# --- synthetic source (in-scope control, or the LibreOffice page shape) -----------------------------------------

SYNTHETIC_SPEC = dict(page=1, glyph_ids=[0, 1], explicit_width=150,
                      region=dict(bounds=[18, 40, 200, 250], x=20, width=150, first_baseline=200),
                      layout=dict(max_bottom=220, min_line_height=22, first_line_indent=0),
                      empty=dict(ascent=10, descent=3), initial_text='A B')
TEXT_PROGRAM = b'BT /Regular 12 Tf 20 200 Td (XY) Tj ET'
# LibreOffice draws body text inside a page-sized rectangular clip group after "0.1 w" (see
# evaluations/continuation/README.md). That entry context is outside the semantic scope.
LIBREOFFICE_SHAPE = b'0.1 w\nq 0 0.1 595.2 841.8 re W* n\nq ' + TEXT_PROGRAM + b' Q\nQ'


def prepare_synthetic(args):
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject
    out = Path(args.output_dir)
    if out.exists():
        return usage(f'output directory exists: {out}')
    out.mkdir(parents=True)
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Courier'), NameObject('/Encoding'): NameObject('/WinAnsiEncoding'),
        NameObject('/FirstChar'): NumberObject(32), NameObject('/LastChar'): NumberObject(126),
        NameObject('/Widths'): ArrayObject([NumberObject(600)] * 95)}))
    page = writer.add_blank_page(320, 260)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject(
        {NameObject('/Regular'): font})})
    stream = DecodedStreamObject()
    stream.set_data(LIBREOFFICE_SHAPE if args.libreoffice_shape else TEXT_PROGRAM)
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(out / 'source.pdf')
    (out / 'spec.json').write_text(json.dumps(SYNTHETIC_SPEC, indent=2) + '\n')
    print(json.dumps(dict(source_sha256=sha(out / 'source.pdf'), libreoffice_shape=args.libreoffice_shape)))
    return 0


# --- preparation of a single owned source slot (explicit spec, no inference) ------------------------------------

SPEC_KEYS = {'page', 'glyph_ids', 'explicit_width', 'region', 'layout', 'empty', 'initial_text'}


def prepare(args):
    """Source PDF → shared-flow v2 owner sidecar for one explicitly confirmed slot (existing APIs only)."""
    from pdfeditor.attributed import inspect_paragraph
    from pdfeditor.selection import make_selection
    from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow
    from pdfeditor.story_flow import confirm_story
    out = Path(args.output_dir)
    if out.exists():
        return usage(f'output directory exists: {out}')
    spec = json.loads(Path(args.spec).read_text(encoding='utf-8'))
    if set(spec) != SPEC_KEYS:
        return usage('preparation spec needs exactly: ' + ', '.join(sorted(SPEC_KEYS)))
    before = {p: sha(p) for p in (args.source_pdf, args.spec, args.font_a)}
    out.mkdir(parents=True)
    source = Path(args.source_pdf)
    region, layout = spec['region'], spec['layout']
    paragraph = inspect_paragraph(source, make_selection(source, spec['page'], glyph_ids=spec['glyph_ids'],
                                                         explicit_width=spec['explicit_width']))
    story = confirm_story(source, {'part': dict(page=spec['page'], bounds=region['bounds'], paragraph=paragraph,
        paint_relations=[], layout=dict(x=region['x'], baseline=region['first_baseline'], width=region['width'],
            max_bottom=layout['max_bottom'], min_line_height=layout['min_line_height'],
            first_line_indent=layout['first_line_indent']))},
        paragraph_id='A', chain=['part'], protected_regions={},
        styles={'body': dict(provider=dict(path=str(Path(args.font_a).resolve())), provider_relation='substituted')},
        style_assignments={'part': {s['id']: 'body' for s in paragraph['styles']}}, typing_style_id='body')
    state = confirm_shared_flow(source, {'A': story}, flow_id='semantic-external', paragraph_order=['A'],
        regions={'R': dict(page=spec['page'], bounds=region['bounds'], x=region['x'], width=region['width'],
                           first_baseline=region['first_baseline'])},
        region_order=['R'], slot_regions={'A': {'part': 'R'}},
        paragraph_policies={'A': dict(min_line_height=layout['min_line_height'],
            first_line_indent=layout['first_line_indent'], keep_together=False, break_before='auto',
            break_after='auto', empty=dict(kind='reserve-line', **spec['empty']))},
        follows=[], protected_regions={})
    text = state['paragraphs']['A']['logical']['text']
    edit_shared_flow(source, state, out / 'document.pdf', out / 'shared-flow.json',
                     {'A': dict(edits=[dict(start=0, end=len(text), text=spec['initial_text'], style_id='body')])})
    opened = open_shared_flow(out / 'document.pdf', out / 'shared-flow.json')
    if opened['status'] != 'restored':
        raise Failure('prepared shared flow does not reopen: ' + opened['reason'])
    if {p: sha(p) for p in before} != before:
        raise Failure('preparation changed an input file')
    record = dict(source_pdf=source.name, source_sha256=before[args.source_pdf], spec=spec,
                  font_a_sha256=before[args.font_a], source_text=text,
                  owner=opened['state']['slots']['slot-0']['source_output']['state'],
                  outputs={n: sha(out / n) for n in ('document.pdf', 'shared-flow.json')})
    (out / 'prepare.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record['outputs'], indent=2))
    return 0


# --- environment and input metadata ------------------------------------------------------------------------------

def _command(arguments, *, stderr=False):
    try:
        done = subprocess.run(arguments, capture_output=True, text=True, timeout=60, cwd=ROOT)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (done.stderr if stderr else done.stdout).strip() or (done.stdout or done.stderr).strip()
    return text.splitlines()[0] if text else None


def libreoffice():
    candidates = [shutil.which('soffice'), shutil.which('libreoffice'),
                  r'C:\Program Files\LibreOffice\program\soffice.exe',
                  r'C:\Program Files (x86)\LibreOffice\program\soffice.exe']
    for candidate in filter(None, candidates):
        path = Path(candidate)
        if not path.exists():
            continue
        ini = path.parent / 'version.ini'
        if ini.exists():
            for line in ini.read_text(errors='replace').splitlines():
                if line.split('=')[0] in ('MsiProductVersion', 'ProductVersion'):
                    return dict(version=line.split('=', 1)[1].strip(), source='version.ini')
        return dict(version=_command([str(path), '--version']) or 'unavailable', source='--version')
    return 'unavailable'


def environment():
    import fontTools
    import pymupdf
    import pypdf
    import uharfbuzz
    poppler = shutil.which('pdftoppm')
    return dict(
        os=dict(system=platform.system(), release=platform.release(), version=platform.version(),
                platform=platform.platform(), machine=platform.machine()),
        python=sys.version.split()[0], pymupdf=pymupdf.VersionBind, fonttools=fontTools.version,
        uharfbuzz=uharfbuzz.__version__, pypdf=pypdf.__version__,
        poppler=dict(available=poppler is not None, version=_command([poppler, '-v'], stderr=True) if poppler else None),
        libreoffice=libreoffice(),
        repository=dict(commit=_command(['git', 'rev-parse', 'HEAD']) or 'unavailable',
                        dirty=bool(_command(['git', 'status', '--porcelain', '--untracked-files=no'])),
                        runtime_digest=runtime_digest()))


def pdf_metadata(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    info = reader.metadata or {}
    producer, creator = info.get('/Producer'), info.get('/Creator')
    return dict(filename=Path(path).name, sha256=sha(path), page_count=len(reader.pages),
                producer=str(producer) if producer is not None else None,
                creator=str(creator) if creator is not None else None,
                # Informational only: a Producer string is never trust authority.
                libreoffice_claimed=any('libreoffice' in str(v).lower() for v in (producer, creator) if v))


# --- per-revision evidence ---------------------------------------------------------------------------------------

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def owner_summary(state, sid):
    record = state['slots'][sid]['source_output']
    current = record.get('current') or {}
    return dict(marker_id=record['marker_id'], created_from_sha256=digest(record['created_from']),
                program_sha256=current.get('program_sha256'), block_sha256=current.get('block_sha256'),
                range=current.get('range'), pdf_sha256=state['pdf_sha256'])


def semantic_summary(opened):
    payload, authority = opened['semantic'], opened['authority'] or {}
    style = payload['style']
    provider = authority.get('provider') or {}
    return dict(text=payload['text'], font_sha256=payload['font']['sha'],
                provider_sha256=provider.get('sha256'), provider_name=Path(provider['path']).name if provider else None,
                font_size=style['font_size'], tracking=style['tracking'], rise=style['rise'],
                horizontal_scale=style['horizontal_scale'], word_spacing=style['word_spacing'],
                edges=len(payload['edges']), provenance=authority.get('provenance'), version=opened['version'],
                canonical=opened['island']['canonical'])


def creation_evidence(state, sid):
    slot = state['slots'][sid]
    p = state['paragraphs'][slot['paragraph_id']]
    return dict(style_registry=digest(p['style_registry']),
                source_observations=digest([e['source_observations'] for e in p['style_registry'].values()]),
                source_provider=digest([{k: v for k, v in e['reflow_provider'].items() if k != 'path'}
                                        for e in p['style_registry'].values()]),
                source_model_sha256=p['source_model_sha256'], source_snapshot_sha256=slot['source_snapshot_sha256'],
                created_from=digest(slot['source_output']['created_from']), contract_sha256=state['contract_sha256'])


def island(pdf, state, sid):
    """Owned body bytes of the slot (read-only)."""
    from pdfeditor import source_ownership as owned
    from pdfeditor.content_stream import ContentPage, operators
    slot = state['slots'][sid]
    content = ContentPage(pdf, state['regions'][slot['region_id']]['page'])
    try:
        data = content.streams[-content.page.xref]
        span = owned.inventory(data)[slot['source_output']['marker_id']]
        body = data[span[1]:span[2]]
    finally:
        content.close()
    return dict(sha256=hashlib.sha256(body).hexdigest(), size=len(body), operators=len(list(operators(body))))


def owned_body(pdf, state, sid):
    from pdfeditor import source_ownership as owned
    from pdfeditor.content_stream import ContentPage
    slot = state['slots'][sid]
    content = ContentPage(pdf, state['regions'][slot['region_id']]['page'])
    try:
        data = content.streams[-content.page.xref]
        span = owned.inventory(data)[slot['source_output']['marker_id']]
        return data[span[1]:span[2]]
    finally:
        content.close()


def underline_evidence(pdf, state, sid, opened):
    """Physical + semantic underline evidence of one revision, read from the output (read-only).

    The owned body is split by the runtime's own v3 body grammar into the text group and the
    underline group; rectangles are the operands actually written (PDF y-up, as serialized).
    `paint_body_sha256` is null when the body has no underline group.
    """
    from pdfeditor import semantic_paint as paint
    from pdfeditor.content_stream import operators
    body = owned_body(pdf, state, sid)
    payload = opened['semantic']
    decorations = payload.get('decorations')
    try:
        text, group = paint.body_grammar(body)
        split = 'v3 body grammar'
    except Exception as error:  # noqa: BLE001 - e.g. a pre-L2 confirmed island; recorded, never hidden
        text, group, split = None, None, f'not split: {type(error).__name__}: {error}'
    rectangles, fill, baselines, origins = [], None, [], []
    if group:
        ops = list(operators(group))
        fill = [ops[1].name, *map(str, ops[1].args)]
        for k in range(2, len(ops) - 1, 6):
            (x0, yu), (x1, _), _, (_, yl) = (op.args for op in ops[k:k + 4])
            rectangles.append([str(x0), str(yu), str(x1), str(yl)])
    if text:
        baselines = sorted({str(op.args[5]) for op in operators(text) if op.name == 'Tm'})
        origins = [str(op.args[4]) for op in operators(text) if op.name == 'Tm']
    return dict(semantic_version=opened['version'], decorations=decorations,
                decoration_count=None if decorations is None else len(decorations),
                ranges=None if decorations is None else [[d['start'], d['end']] for d in decorations],
                recipes=None if decorations is None else [d['recipe'] for d in decorations],
                rectangle_count=len(rectangles), rectangles_pdf=rectangles, paint_fill=fill,
                text_tm_y=baselines, text_tm_x=origins, body_sha256=hashlib.sha256(body).hexdigest(), body_split=split,
                text_body_sha256=hashlib.sha256(text).hexdigest() if text is not None else None,
                paint_body_sha256=hashlib.sha256(group).hexdigest() if group else None,
                paint_body_size=len(group) if group else 0)


def decoration_evidence(pdf, state, sid, opened):
    """Strikeout-mode evidence: the underline evidence plus kinds and the physical side of every rectangle.

    `rectangle_sides` is read from the written bytes only: `above` when the serialized upper edge is
    strictly above the serialized glyph baseline (PDF y-up: Tm y − rise), `at_or_below` otherwise (§31.6).
    Each rectangle is compared with the nearest written baseline (the synthetic fixture is one line).
    """
    value = underline_evidence(pdf, state, sid, opened)
    decorations = value['decorations']
    rise = F(opened['semantic']['style']['rise'])
    baselines = [F(y) - rise for y in value['text_tm_y']]
    sides = []
    for _, yu, _, _ in value['rectangles_pdf']:
        baseline = min(baselines, key=lambda b: abs(b - F(yu))) if baselines else None
        sides.append(None if baseline is None else 'above' if F(yu) > baseline else 'at_or_below')
    provider = (opened['authority'] or {}).get('provider') or {}
    return dict(value, kinds=None if decorations is None else [d['kind'] for d in decorations],
                rectangle_sides=sides, provider_sha256=provider.get('sha256'))


def mupdf_raster(pdf, page, png=None):
    import pymupdf
    with pymupdf.open(pdf) as document:
        pixmap = document[page - 1].get_pixmap(dpi=144, alpha=False)
        if png is not None:
            pixmap.save(png)
        return hashlib.sha256(pixmap.samples).hexdigest()


# --- the run -----------------------------------------------------------------------------------------------------

class Run:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.output_dir)
        self.art = self.out / 'artifacts'
        self.inputs = {'pdf': Path(args.pdf), 'sidecar': Path(args.sidecar), 'font_a': Path(args.font_a)}
        if args.font_b:
            self.inputs['font_b'] = Path(args.font_b)
        self.mode = getattr(args, 'mode', None) or 'text'
        if self.mode == 'strikeout' and args.plan:
            self.inputs['plan'] = Path(args.plan)  # strikeout mode also preserves an optional plan file
        self.result = dict(schema_version=SCHEMA_VERSION, executed_at=datetime.now(timezone.utc).isoformat(),
                           disclaimer=DISCLAIMER, mode=self.mode, validation_kind=VALIDATION_KIND[self.mode],
                           underline_runtime=self.mode == 'underline', stages=[], revisions={}, verdict=None)
        if self.mode == 'underline':
            self.result['underline_disclaimer'] = UNDERLINE_DISCLAIMER
        if self.mode == 'strikeout':
            # Additive keys only for the strikeout mode: text/underline results keep their schema exactly.
            self.result['strikeout_runtime'] = True
            self.result['strikeout_disclaimer'] = STRIKEOUT_DISCLAIMER
        self.revisions = {}  # name -> dict(pdf, sidecar)

    # bookkeeping
    def rel(self, path):
        try:
            return Path(path).resolve().relative_to(self.out.resolve()).as_posix()
        except ValueError:
            return Path(path).name

    def log(self, line):
        with open(self.out / 'logs' / 'harness.log', 'a', encoding='utf-8') as handle:
            handle.write(datetime.now(timezone.utc).isoformat() + ' ' + line + '\n')

    def save(self):
        path = self.out / 'result.json'
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(self.result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        os.replace(tmp, path)

    def status(self, name):
        return next((s['status'] for s in self.result['stages'] if s['id'] == name), None)

    def stage(self, name, function, *, required=True, needs=()):
        entry = dict(id=name, required=required, status=None, error=None, details={})
        self.result['stages'].append(entry)
        blocked = [n for n in needs if self.status(n) != PASS]
        self.log(f'stage {name} start')
        try:
            if blocked:
                raise Skip('prerequisite did not pass: ' + ', '.join(f'{n}={self.status(n)}' for n in blocked))
            entry['details'] = function() or {}
            entry['status'] = PASS
        except Skip as skip:
            entry.update(status=SKIPPED, error=str(skip))
        except Failure as failure:
            entry.update(status=FAIL, error=str(failure))
            self.log(traceback.format_exc())
        except Exception as error:  # noqa: BLE001 - every exception is recorded, never converted to success
            from pdfeditor.backend import PdfError
            entry.update(status=REFUSED if isinstance(error, PdfError) else FAIL,
                         error=f'{type(error).__name__}: {error}')
            self.log(traceback.format_exc())
        self.log(f'stage {name} {entry["status"]}' + (f': {entry["error"]}' if entry['error'] else ''))
        self.save()
        return entry['status']

    def record(self, name, pdf, sidecar, *, body=None):
        from pdfeditor import semantic_layout as semantic
        opened = semantic.open_semantic_flow(pdf, sidecar)
        if opened['status'] != 'restored':
            raise Failure(f'{name} does not reopen fresh: ' + opened['reason'])
        state = load(sidecar)
        sid = opened['slot_id']
        evidence = dict(pdf=self.rel(pdf), sidecar=self.rel(sidecar), pdf_sha256=sha(pdf), sidecar_sha256=sha(sidecar),
                        pdf_size=Path(pdf).stat().st_size, sidecar_size=Path(sidecar).stat().st_size,
                        owner=owner_summary(state, sid), semantic=semantic_summary(opened), island=island(pdf, state, sid))
        if self.mode == 'underline':
            evidence['underline'] = dict(underline_evidence(pdf, state, sid, opened),
                                         raster_mupdf_sha256=mupdf_raster(pdf, self.result['target']['page']))
        if self.mode == 'strikeout':
            evidence['decoration_evidence'] = dict(decoration_evidence(pdf, state, sid, opened),
                                                   raster_mupdf_sha256=mupdf_raster(pdf, self.result['target']['page']))
        self.revisions[name] = dict(pdf=Path(pdf), sidecar=Path(sidecar), opened=opened, state=state)
        self.result['revisions'][name] = evidence
        return evidence

    def build(self, name, parent, request, asset=None):
        from pdfeditor import semantic_layout as semantic
        from pdfeditor import semantic_writer as writer
        source = self.revisions[parent]
        plan = semantic.plan_semantic_transition(source['pdf'], source['sidecar'], request, asset=asset)
        result = writer.build_semantic_candidate(source['pdf'], source['sidecar'], request,
                                                 workspace=self.art / name, asset=asset)
        evidence = self.record(name, result['pdf'], result['sidecar'])
        if load(result['sidecar'])['slots'][self.sid]['semantic']['payload'] != plan['next']:
            raise Failure(f'{name} semantic payload differs from the authorized plan')
        self.last_plan = plan
        if self.mode in ('underline', 'strikeout'):
            evidence['transition'] = dict(parent=parent, classification=plan['classification'],
                                          version=plan['version'], next_version=plan.get('next_version'),
                                          authority_preserved=plan['next_authority'] == plan['authority'])
        return dict(request=request, classification=plan['classification'], diff=plan['diff'], revision=evidence)

    # stages
    def preflight(self):
        self.result['environment'] = environment()
        self.result['windows_execution'] = platform.system() == 'Windows'
        self.result['inputs'] = dict(pdf=pdf_metadata(self.inputs['pdf']),
                                     sidecar=dict(filename=self.inputs['sidecar'].name, sha256=sha(self.inputs['sidecar'])),
                                     fonts={k: dict(filename=v.name, sha256=sha(v)) for k, v in self.inputs.items()
                                            if k.startswith('font_')})
        self.before = {k: sha(v) for k, v in self.inputs.items()}
        plan = deepcopy(dict(underline=DEFAULT_UNDERLINE_PLAN, strikeout=DEFAULT_STRIKEOUT_PLAN).get(self.mode,
                                                                                                     DEFAULT_PLAN))
        if self.args.plan:
            plan.update(load(self.args.plan))
        self.plan = plan
        self.result['plan'] = plan
        for key in ('font_a', 'font_b'):
            if key in self.inputs:
                check_font(self.inputs[key])
        return dict(plan=plan)

    def baseline(self):
        from pdfeditor import semantic_layout as semantic
        from pdfeditor.backend import PdfError
        from pdfeditor.shared_flow import open_shared_flow
        state = load(self.inputs['sidecar'])
        slots = list(state.get('slots', {}))
        if self.args.slot_id:
            if self.args.slot_id not in slots:
                raise PdfError('requested slot is not in the sidecar')
            self.sid = self.args.slot_id
        elif len(slots) == 1:
            self.sid = slots[0]
        else:
            raise PdfError(f'ambiguous target: {len(slots)} slots and no --slot-id')
        schema = state.get('schema')
        opened = (open_shared_flow if schema == 'pdfengine-shared-flow-2' else semantic.open_semantic_flow)(
            self.inputs['pdf'], self.inputs['sidecar'])
        if opened['status'] != 'restored':
            raise PdfError('baseline does not open: ' + opened['reason'])
        slot = state['slots'][self.sid]
        page = state['regions'][slot['region_id']]['page']
        p = state['paragraphs'][slot['paragraph_id']]
        typing = p['logical']['typing_style_id']
        self.schema = schema
        self.baseline_state = state
        self.result['target'] = dict(slot_id=self.sid, page=page, paragraph_id=slot['paragraph_id'],
            region_id=slot['region_id'], sidecar_schema=schema,
            semantic_version=slot.get('semantic', {}).get('version'), owner_state=slot['source_output']['state'],
            source_font_name=p['style_registry'][typing]['attributes']['font_name']['value'],
            generated_fonts=sorted(state.get('generated_fonts', {}).get(str(page), {})),
            text_length=len(p['logical']['text']))
        return dict(schema=schema, creation_evidence=creation_evidence(state, self.sid))

    def confirm(self):
        from pdfeditor import semantic_layout as semantic
        from pdfeditor import story_styles as styles
        from pdfeditor.semantic_measure import font_policy
        if self.schema == semantic.SCHEMA:
            evidence = self.record('confirmed', self.inputs['pdf'], self.inputs['sidecar'])
            return dict(mode='input already shared-flow-3', revision=evidence)
        state = self.baseline_state
        slot = state['slots'][self.sid]
        p = state['paragraphs'][slot['paragraph_id']]
        typing = p['logical']['typing_style_id']
        props = styles.properties(p['style_registry'][typing])
        exact = lambda value: str(F(str(value)))  # noqa: E731
        statement = dict(text=p['logical']['text'], style=dict(font_size=exact(props['font_size']),
                horizontal_scale=exact(props['horizontal_scale']), rise=exact(props['baseline_shift']),
                tracking=exact(props['tracking']), word_spacing='0', spacing_intent='confirmed-inline'),
            font=dict(sha=sha(self.inputs['font_a']), policy=font_policy()), body_style_id=typing, edges=[],
            region_id=slot['region_id'])
        v3 = semantic.confirm_semantic_layout(self.inputs['pdf'], self.inputs['sidecar'], slot_id=self.sid,
                                              semantic=statement)
        directory = self.art / 'confirmed'
        directory.mkdir(parents=True)
        (directory / 'shared-flow.json').write_text(json.dumps(v3, indent=2) + '\n', encoding='utf-8')
        evidence = self.record('confirmed', self.inputs['pdf'], directory / 'shared-flow.json')
        return dict(mode='confirmed from shared-flow-2 (statement = source-confirmed registry values)',
                    revision=evidence)

    def edit(self):
        return self.build('candidate-01-edit', 'confirmed', dict(operation='edit', **self.plan['edit']))

    def style(self):
        if not self.plan.get('style'):
            raise Skip('no style transition requested in the plan')
        return self.build('candidate-02-style', 'candidate-01-edit',
                          dict(operation='reinterpret', changes=self.plan['style']))

    def font(self):
        if 'font_b' not in self.inputs:
            raise Skip('--font-b not supplied')
        parent = 'candidate-02-style' if 'candidate-02-style' in self.revisions else 'candidate-01-edit'
        details = self.build('candidate-03-font-b', parent, dict(operation='reinterpret',
                             changes=dict(font=sha(self.inputs['font_b']))), asset=self.inputs['font_b'])
        if details['revision']['semantic']['provider_sha256'] != sha(self.inputs['font_b']):
            raise Failure('current provider is not font B')
        return details

    def last_candidate(self):
        return [n for n in self.revisions if n.startswith('candidate-')][-1]

    def noop(self):
        parent = self.last_candidate()
        details = self.build('candidate-04-noop', parent, dict(operation='save'))
        before, after = self.result['revisions'][parent]['island'], details['revision']['island']
        if before != after:
            raise Failure('no-op save changed the canonical owned body')
        return dict(details, body_stable=True, operators=after['operators'],
                    pdf_size=[self.result['revisions'][parent]['pdf_size'], details['revision']['pdf_size']],
                    sidecar_size=[self.result['revisions'][parent]['sidecar_size'], details['revision']['sidecar_size']])

    def publish(self, name, parent):
        from pdfeditor import semantic_publication as publication
        source = self.revisions[parent]
        self.art.mkdir(exist_ok=True)
        result = publication.publish_semantic_bundle(source['pdf'], source['sidecar'], self.art / name)
        entries = sorted(p.name for p in (self.art / name).iterdir())
        if entries != ['document.pdf', 'shared-flow.json']:
            raise Failure('published bundle does not hold exactly the two artifacts')
        identical = sha(result['pdf']) == sha(source['pdf']) and sha(result['sidecar']) == sha(source['sidecar'])
        if not identical:
            raise Failure('published bytes differ from the candidate bytes')
        evidence = self.record(name, result['pdf'], result['sidecar'])
        return dict(candidate=parent, directory=self.rel(result['directory']), artifacts=entries,
                    staged_and_public_reopen=result['verification']['status'], candidate_bytes_identical=True,
                    revision=evidence)

    def fresh(self, name):
        code = ('import json,sys;from pdfeditor.semantic_layout import open_semantic_flow;'
                'r=open_semantic_flow(sys.argv[1],sys.argv[2]);'
                'print(json.dumps([r["status"],r.get("reason"),r.get("semantic"),r.get("authority")]))')
        revision = self.revisions[name]
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(ROOT), os.environ.get('PYTHONPATH')])))
        done = subprocess.run([sys.executable, '-c', code, str(revision['pdf']), str(revision['sidecar'])],
                              capture_output=True, text=True, cwd=ROOT, env=env, timeout=600)
        if done.returncode:
            raise Failure(f'fresh process failed for {name}: {done.stderr.strip()[-500:]}')
        status, reason, payload, authority = json.loads(done.stdout)
        if status != 'restored':
            raise Failure(f'{name} does not reopen in a fresh process: {reason}')
        if (payload, authority) != (revision['opened']['semantic'], revision['opened']['authority']):
            raise Failure(f'{name} fresh-process semantics differ')
        return dict(fresh_process='restored')

    def publish_a(self):
        details = self.publish('bundle-a', 'candidate-04-noop')
        details.update(self.fresh('bundle-a'))
        self.bundle_a = {p.name: sha(p) for p in (self.art / 'bundle-a').iterdir()}
        return details

    def edit_b(self):
        return self.build('candidate-05-edit-from-bundle-a', 'bundle-a', dict(operation='edit', **self.plan['next_edit']))

    def font_back(self):
        if 'font_b' not in self.inputs:
            raise Skip('no font B transition to undo')
        return self.build('candidate-06-font-a', 'candidate-05-edit-from-bundle-a', dict(
            operation='reinterpret', changes=dict(font=sha(self.inputs['font_a']))), asset=self.inputs['font_a'])

    def publish_b(self):
        parent = self.last_candidate()
        details = self.publish('bundle-b', parent)
        details.update(self.fresh('bundle-b'))
        if {p.name: sha(p) for p in (self.art / 'bundle-a').iterdir()} != self.bundle_a:
            raise Failure('bundle A changed after bundle B')
        return dict(details, bundle_a_unchanged=True)

    def negatives(self):
        """Expected refusals: a PASS here means the runtime refused, as it must."""
        from pdfeditor import semantic_layout as semantic
        a, b = self.revisions['bundle-a'], self.revisions['bundle-b']
        outcomes = {}
        for label, pdf, sidecar in (('A.pdf+B.json', a['pdf'], b['sidecar']), ('B.pdf+A.json', b['pdf'], a['sidecar'])):
            opened = semantic.open_semantic_flow(pdf, sidecar)
            outcomes[label] = opened.get('reason', 'restored')
            if opened['status'] == 'restored':
                raise Failure(f'mixed revision pair {label} was accepted')
        return dict(expected_refusals=outcomes)

    def continuity(self):
        final = self.revisions[[n for n in self.revisions if n.startswith('bundle-')][-1]]
        before, after = creation_evidence(self.baseline_state, self.sid), creation_evidence(final['state'], self.sid)
        if before != after:
            raise Failure('source creation evidence changed: ' + ', '.join(k for k in before if before[k] != after[k]))
        markers = {r['owner']['marker_id'] for r in self.result['revisions'].values()}
        created = {r['owner']['created_from_sha256'] for r in self.result['revisions'].values()}
        if len(markers) != 1 or len(created) != 1:
            raise Failure('owner identity changed across revisions')
        return dict(creation_evidence=after, owner_marker_constant=True, created_from_constant=True,
                    revisions=len(self.result['revisions']))

    def raster_mupdf(self):
        import pymupdf
        directory = self.art / 'raster'
        directory.mkdir(parents=True, exist_ok=True)
        names = dict(baseline='confirmed', changed='candidate-03-font-b' if 'candidate-03-font-b' in self.revisions
                     else 'candidate-01-edit', noop='candidate-04-noop', bundle_a='bundle-a', bundle_b='bundle-b')
        digests = {}
        for label, name in names.items():
            revision = self.revisions[name]
            page = self.result['target']['page']
            with pymupdf.open(revision['pdf']) as document:
                pixmap = document[page - 1].get_pixmap(dpi=144, alpha=False)
                digests[label] = hashlib.sha256(pixmap.samples).hexdigest()
                pixmap.save(directory / f'mupdf-{label}.png')
        parent = self.last_noop_parent()
        with pymupdf.open(self.revisions[parent]['pdf']) as document:
            before_noop = hashlib.sha256(document[self.result['target']['page'] - 1].get_pixmap(
                dpi=144, alpha=False).samples).hexdigest()
        checks = dict(noop_identical=before_noop == digests['noop'], candidate_published_identical=digests['noop'] ==
                      digests['bundle_a'], semantic_change_differs=digests['baseline'] != digests['changed'])
        if not (checks['noop_identical'] and checks['candidate_published_identical']):
            raise Failure('MuPDF raster differs across a no-op or publication: ' + canonical(checks))
        return dict(checks, sha256=digests, pngs=sorted(self.rel(p) for p in directory.glob('mupdf-*.png')))

    def last_noop_parent(self):
        names = list(self.revisions)
        return names[names.index('candidate-04-noop') - 1]

    def raster_poppler(self):
        renderer = shutil.which('pdftoppm')
        if renderer is None:
            raise Skip('Poppler (pdftoppm) unavailable')
        directory = self.art / 'raster'
        directory.mkdir(parents=True, exist_ok=True)
        page = str(self.result['target']['page'])
        digests = {}
        for label, name in (('before_noop', self.last_noop_parent()), ('noop', 'candidate-04-noop'),
                            ('bundle_a', 'bundle-a')):
            prefix = directory / f'poppler-{label}'
            subprocess.run([renderer, '-f', page, '-l', page, '-r', '144', '-png', '-singlefile',
                            str(self.revisions[name]['pdf']), str(prefix)], check=True, capture_output=True, timeout=600)
            digests[label] = sha(prefix.with_suffix('.png'))
        checks = dict(noop_identical=digests['before_noop'] == digests['noop'],
                      candidate_published_identical=digests['noop'] == digests['bundle_a'])
        if not all(checks.values()):
            raise Failure('Poppler raster differs across a no-op or publication: ' + canonical(checks))
        return dict(checks, sha256=digests)

    # --- underline mode (§28) ----------------------------------------------------------------------------------

    def ul(self, name):
        return self.result['revisions'][name]['underline']

    def u_build(self, name, parent, request, asset=None, *, expect, paint):
        """Build one revision and check its transition, version and observed paint transition.

        `expect` = (classification, next_version); `paint` = insert | replace | remove | none, judged from the
        observed underline groups of the parent and the candidate (output evidence, not the internal Plan).
        """
        details = self.build(name, parent, request, asset)
        transition = details['revision']['transition']
        if (transition['classification'], transition['next_version']) != expect:
            raise Failure(f'{name}: transition {transition} differs from the expected {expect}')
        if details['revision']['semantic']['version'] != expect[1]:
            raise Failure(f'{name} reopens as semantic version {details["revision"]["semantic"]["version"]}')
        before, after = bool(self.ul(parent)['paint_body_sha256']), bool(self.ul(name)['paint_body_sha256'])
        observed = {(False, True): 'insert', (True, True): 'replace', (True, False): 'remove',
                    (False, False): 'none'}[(before, after)]
        if observed != paint:
            raise Failure(f'{name}: observed paint transition {observed}, expected {paint}')
        if self.ul(name)['rectangle_count'] != self.last_plan['next_underline_rectangles']:
            raise Failure(f'{name}: written rectangles differ from the authorized plan')
        return dict(details, paint_transition=observed)

    def u_text_baseline(self):
        """A canonical version 2 text-only revision (L2 save of the confirmed state)."""
        details = self.u_build('u00-text-baseline', 'confirmed', dict(operation='save'), expect=('D', 2), paint='none')
        if 'decorations' in self.revisions['u00-text-baseline']['opened']['semantic']:
            raise Failure('a version 2 revision carries decorations')
        return details

    def u_add(self):
        spec = self.plan['underline_add']
        item = dict(kind='underline', start=spec['start'], end=spec['end'], start_affinity='outside',
                    end_affinity='outside', recipe=deepcopy(spec['recipe']))
        request = dict(operation='reinterpret', changes={'decorations': {'add': item}})
        details = self.u_build('u01-add', 'u00-text-baseline', request, expect=('E', 3), paint='insert')
        transition = details['revision']['transition']
        if transition['version'] != 2 or not transition['authority_preserved']:
            raise Failure('the first add is not version 2 → 3 with unchanged current authority')
        expected = [dict(item, id='current:0')]
        if self.ul('u01-add')['decorations'] != expected:
            raise Failure('added decoration differs from the canonical current:0 record')
        if self.ul('u01-add')['text_body_sha256'] != self.ul('u00-text-baseline')['text_body_sha256']:
            raise Failure('the text group changed when only an underline was added')
        return dict(details, version_transition=[transition['version'], transition['next_version']],
                    decorations=expected)

    def u_noop(self, name, parent):
        details = self.u_build(name, parent, dict(operation='save'), expect=('D', 3), paint='replace')
        a, b = self.result['revisions'][parent], self.result['revisions'][name]
        old, new = self.revisions[parent]['opened'], self.revisions[name]['opened']
        keys = ('body_sha256', 'text_body_sha256', 'paint_body_sha256', 'rectangle_count', 'rectangles_pdf',
                'decorations', 'raster_mupdf_sha256')
        checks = dict(pdf_bytes_identical=a['pdf_sha256'] == b['pdf_sha256'],
                      operators_identical=a['island']['operators'] == b['island']['operators'],
                      owner_block_identical=a['owner']['block_sha256'] == b['owner']['block_sha256'],
                      semantic_identical=(old['semantic'], old['authority'], old['derived']) ==
                      (new['semantic'], new['authority'], new['derived']),
                      underline_identical={k: a['underline'][k] for k in keys} == {k: b['underline'][k] for k in keys})
        if not all(v for k, v in checks.items() if k != 'pdf_bytes_identical'):
            raise Failure(f'{name} is not a canonical no-op: ' + canonical(checks))
        return dict(details, **checks)

    def u_noop_1(self):
        return self.u_noop('u02-noop-1', 'u01-add')

    def u_noop_2(self):
        return self.u_noop('u03-noop-2', 'u02-noop-1')

    def u_edit(self):
        from pdfeditor import semantic_paint as paint
        edit = self.plan['underline_edit']
        before = self.revisions['u03-noop-2']['opened']['semantic']
        details = self.u_build('u04-edit-remap', 'u03-noop-2', dict(operation='edit', **edit), expect=('D', 3),
                               paint='replace')
        expected = paint.remap(before['text'], before['decorations'], edit['start'], edit['end'], edit['text'])
        after = self.ul('u04-edit-remap')
        if after['decorations'] != expected:
            raise Failure('edit remap differs from the §27.7 contract: ' + canonical(after['decorations']))
        return dict(details, before_ranges=self.ul('u03-noop-2')['ranges'], after_ranges=after['ranges'],
                    before_rectangles=self.ul('u03-noop-2')['rectangles_pdf'], after_rectangles=after['rectangles_pdf'])

    def u_recipe(self):
        old = self.ul('u04-edit-remap')
        d = old['decorations'][0]
        request = dict(operation='reinterpret', changes={'decorations': {'recipe': dict(
            id=d['id'], start=d['start'], end=d['end'], recipe=deepcopy(self.plan['underline_recipe']))}})
        details = self.u_build('u05-recipe', 'u04-edit-remap', request, expect=('E', 3), paint='replace')
        new = self.ul('u05-recipe')
        same_text = (self.revisions['u05-recipe']['opened']['semantic']['text'] ==
                     self.revisions['u04-edit-remap']['opened']['semantic']['text'])
        horizontal = [r[0::2] for r in old['rectangles_pdf']] == [r[0::2] for r in new['rectangles_pdf']]
        vertical = [r[1::2] for r in old['rectangles_pdf']] != [r[1::2] for r in new['rectangles_pdf']]
        checks = dict(text_unchanged=same_text, text_group_unchanged=old['text_body_sha256'] == new['text_body_sha256'],
                      horizontal_endpoints_unchanged=horizontal, vertical_geometry_changed=vertical,
                      authority_preserved=details['revision']['transition']['authority_preserved'],
                      recipe_applied=new['recipes'][0] == self.plan['underline_recipe'])
        if not all(checks.values()):
            raise Failure('recipe change: ' + canonical(checks))
        return dict(details, **checks)

    def em_check(self, name):
        """Exact em geometry from the written bytes: Tm y − upper = rise + size·offset, upper − lower = size·thickness.

        Serialized decimals carry at most 6 places (ties to even), so each side is exact to 1e-6.
        """
        u = self.ul(name)
        style = self.revisions[name]['opened']['semantic']['style']
        size, rise = F(style['font_size']), F(style['rise'])
        recipe = u['recipes'][0]
        tm = [F(v) for v in u['text_tm_y']]
        errors = []
        for x0, yu, x1, yl in u['rectangles_pdf']:
            offset = min(abs(t - F(yu) - rise - size * F(recipe['offset_em'])) for t in tm)
            thickness = abs(F(yu) - F(yl) - size * F(recipe['thickness_em']))
            errors.append(max(offset, thickness))
        worst = max(errors, default=F(0))
        if worst > F(2, 10**6):
            raise Failure(f'{name}: em recipe geometry is off by {float(worst)} pt')
        return float(worst)

    def u_style(self):
        details = self.u_build('u06-style', 'u05-recipe', dict(operation='reinterpret',
                               changes=deepcopy(self.plan['underline_style'])), expect=('E', 3), paint='replace')
        old, new = self.ul('u05-recipe'), self.ul('u06-style')
        if (old['ranges'], old['recipes']) != (new['ranges'], new['recipes']):
            raise Failure('style change changed the decorations')
        drift = sorted(k for k in details['diff'] if not k.startswith('style.'))
        if drift:
            raise Failure('style change drifted unrelated semantics: ' + ', '.join(drift))
        return dict(details, decorations_preserved=True, em_geometry_max_error_pt=self.em_check('u06-style'))

    def u_font(self):
        if 'font_b' not in self.inputs:
            raise Skip('--font-b not supplied')
        details = self.u_build('u07-font-b', 'u06-style', dict(operation='reinterpret',
                               changes=dict(font=sha(self.inputs['font_b']))), self.inputs['font_b'],
                               expect=('E', 3), paint='replace')
        old, new = self.ul('u06-style'), self.ul('u07-font-b')
        checks = dict(provider_is_font_b=details['revision']['semantic']['provider_sha256'] == sha(self.inputs['font_b']),
                      recipe_preserved=old['recipes'] == new['recipes'] and old['ranges'] == new['ranges'],
                      vertical_geometry_unchanged=[r[1::2] for r in old['rectangles_pdf']] ==
                      [r[1::2] for r in new['rectangles_pdf']],
                      # Left edges sit on a written glyph origin of the new text group; widths stay positive.
                      endpoints_on_new_glyphs=all(r[0] in new['text_tm_x'] and F(r[2]) > F(r[0])
                                                  for r in new['rectangles_pdf']),
                      generated_font_is_font_b=self.generated_font_sources('u07-font-b') == {sha(self.inputs['font_b'])})
        if not all(checks.values()):
            raise Failure('font change: ' + canonical(checks))
        return dict(details, **checks, em_geometry_max_error_pt=self.em_check('u07-font-b'),
                    right_endpoints=[[r[2] for r in old['rectangles_pdf']], [r[2] for r in new['rectangles_pdf']]])

    def generated_font_sources(self, name):
        state = self.revisions[name]['state']
        fonts = state.get('generated_fonts', {}).get(str(self.result['target']['page']), {})
        return {r['provider']['source_sha256'] for r in fonts.values() if r.get('slot_id') == self.sid}

    def u_last(self):
        return 'u07-font-b' if 'u07-font-b' in self.revisions else 'u06-style'

    def u_publish(self, name, parent):
        details = self.publish(name, parent)
        details.update(self.fresh(name))
        a, b = self.result['revisions'][parent], self.result['revisions'][name]
        if (b['semantic']['version'], b['underline']['decorations']) != (a['semantic']['version'],
                                                                         a['underline']['decorations']):
            raise Failure(f'{name} does not restore the candidate version and decorations')
        raster = a['underline']['raster_mupdf_sha256'] == b['underline']['raster_mupdf_sha256']
        if not raster:
            raise Failure(f'{name} MuPDF raster differs from its candidate')
        return dict(details, version=b['semantic']['version'], decorations=b['underline']['decorations'],
                    candidate_published_raster_identical=raster)

    def u_publish_a(self):
        details = self.u_publish('bundle-a', self.u_last())
        self.bundle_a = {p.name: sha(p) for p in (self.art / 'bundle-a').iterdir()}
        return details

    def u_remove(self):
        bundle = self.revisions['bundle-a']
        if (bundle['pdf'].parent != self.art / 'bundle-a' or
                sorted(p.name for p in bundle['pdf'].parent.iterdir()) != ['document.pdf', 'shared-flow.json']):
            raise Failure('the removal does not start from the published bundle A')
        d = self.ul('bundle-a')['decorations'][0]
        request = dict(operation='reinterpret', changes={'decorations': {'remove': dict(
            id=d['id'], start=d['start'], end=d['end'])}})
        details = self.u_build('u08-remove-from-bundle-a', 'bundle-a', request, expect=('E', 3), paint='remove')
        new = self.ul('u08-remove-from-bundle-a')
        checks = dict(input=[self.rel(bundle['pdf']), self.rel(bundle['sidecar'])], decorations_empty=new['decorations'] == [],
                      version_stays_3=new['semantic_version'] == 3, paint_group_absent=new['paint_body_sha256'] is None,
                      text_group_unchanged=new['text_body_sha256'] == self.ul('bundle-a')['text_body_sha256'])
        if not all(checks.values()):
            raise Failure('removal: ' + canonical(checks))
        return dict(details, **checks)

    def u_publish_b(self):
        details = self.u_publish('bundle-b', 'u08-remove-from-bundle-a')
        if {p.name: sha(p) for p in (self.art / 'bundle-a').iterdir()} != self.bundle_a:
            raise Failure('bundle A changed after bundle B')
        b = self.ul('bundle-b')
        if (b['semantic_version'], b['decorations'], b['paint_body_sha256']) != (3, [], None):
            raise Failure('bundle B is not a version 3 text-only body without decorations')
        return dict(details, bundle_a_unchanged=True, text_only_body=True)

    def u_tamper(self):
        """Two expected refusals on validation copies (never on the bundles): a resealed decoration
        tamper and a paint-geometry tamper of the PDF. A PASS here means the runtime refused."""
        import pymupdf
        from pdfeditor import semantic_layout as semantic
        from pdfeditor.editable import _seal
        bundle = self.revisions['bundle-a']
        directory = self.art / 'tamper'
        directory.mkdir()
        outcomes = {}
        state = deepcopy(bundle['state'])
        state.pop('model_sha256')
        decoration = state['slots'][self.sid]['semantic']['payload']['decorations'][0]
        decoration['recipe']['thickness_em'] = '1/30' if decoration['recipe']['thickness_em'] != '1/30' else '1/40'
        (directory / 'decoration-tamper.json').write_text(json.dumps(_seal(state), indent=2) + '\n', encoding='utf-8')
        opened = semantic.open_semantic_flow(bundle['pdf'], directory / 'decoration-tamper.json')
        outcomes['resealed decoration recipe tamper'] = opened.get('reason', 'restored')
        if opened['status'] == 'restored':
            raise Failure('a resealed decoration tamper was accepted')
        body = owned_body(bundle['pdf'], bundle['state'], self.sid)
        x0, yu, x1, yl = self.ul('bundle-a')['rectangles_pdf'][0]
        line = f'{x0} {yu} m {x1} {yu} l {x1} {yl} l'.encode()
        if body.count(line) != 1:
            raise Failure('underline rectangle bytes not found once in the owned body')
        with pymupdf.open(bundle['pdf']) as document:
            xref = document[self.result['target']['page'] - 1].get_contents()[-1]
            data = document.xref_stream(xref)
            document.update_stream(xref, data.replace(line, f'{x0} {yu} m {x1} {yu} l {x1} {F(yl) - 1} l'.encode(), 1))
            document.save(directory / 'geometry-tamper.pdf')
        opened = semantic.open_semantic_flow(directory / 'geometry-tamper.pdf', bundle['sidecar'])
        outcomes['paint geometry tamper'] = opened.get('reason', 'restored')
        if opened['status'] == 'restored':
            raise Failure('a paint geometry tamper was accepted')
        return dict(expected_refusals=outcomes)

    def u_control(self):
        """Text-only control: the same text/style/font transitions on version 2, never decorated."""
        steps = [('control-01-edit', dict(operation='edit', **self.plan['underline_edit']), None),
                 ('control-02-style', dict(operation='reinterpret', changes=deepcopy(self.plan['underline_style'])), None)]
        if 'u07-font-b' in self.revisions:
            steps.append(('control-03-font-b', dict(operation='reinterpret', changes=dict(
                font=sha(self.inputs['font_b']))), self.inputs['font_b']))
        parent = 'u00-text-baseline'
        for name, request, asset in steps:
            self.u_build(name, parent, request, asset, expect=(request['operation'] == 'edit' and 'D' or 'E', 2),
                         paint='none')
            parent = name
        self.control = parent
        removed = self.ul('u08-remove-from-bundle-a') if 'u08-remove-from-bundle-a' in self.revisions else None
        return dict(revisions=[s[0] for s in steps], final=parent, text_group_identical_to_removed=
                    None if removed is None else removed['text_body_sha256'] == self.ul(parent)['text_body_sha256'])

    def u_raster_mupdf(self):
        directory = self.art / 'raster'
        directory.mkdir(parents=True, exist_ok=True)
        page = self.result['target']['page']
        names = ['u00-text-baseline', 'u01-add', 'u02-noop-1', 'u03-noop-2', 'u04-edit-remap', 'u05-recipe',
                 'u06-style', *(['u07-font-b'] if 'u07-font-b' in self.revisions else []), 'bundle-a',
                 'u08-remove-from-bundle-a', 'bundle-b', self.control]
        digests = {n: mupdf_raster(self.revisions[n]['pdf'], page, directory / f'mupdf-{n}.png') for n in names}
        if any(digests[n] != self.ul(n)['raster_mupdf_sha256'] for n in names):
            raise Failure('MuPDF raster is not deterministic between two renders')
        last = self.u_last()
        checks = dict(baseline_differs_from_add=digests['u00-text-baseline'] != digests['u01-add'],
                      add_equals_noop_1_and_2=digests['u01-add'] == digests['u02-noop-1'] == digests['u03-noop-2'],
                      recipe_differs_from_previous=digests['u05-recipe'] != digests['u04-edit-remap'],
                      style_differs_from_previous=digests['u06-style'] != digests['u05-recipe'],
                      font_differs_from_previous=(digests['u07-font-b'] != digests['u06-style']
                                                  if 'u07-font-b' in digests else None),
                      removed_equals_text_only_control=digests['u08-remove-from-bundle-a'] == digests[self.control],
                      candidate_a_equals_bundle_a=digests[last] == digests['bundle-a'],
                      candidate_b_equals_bundle_b=digests['u08-remove-from-bundle-a'] == digests['bundle-b'])
        if not all(v for v in checks.values() if v is not None):
            raise Failure('MuPDF raster expectations: ' + canonical(checks))
        return dict(checks, dpi=144, sha256=digests, control=self.control)

    def u_raster_poppler(self):
        renderer = shutil.which('pdftoppm')
        if renderer is None:
            raise Skip('Poppler (pdftoppm) unavailable')
        directory = self.art / 'raster'
        directory.mkdir(parents=True, exist_ok=True)
        page = str(self.result['target']['page'])
        digests = {}
        for name in ('u00-text-baseline', 'u01-add', 'u02-noop-1', 'u03-noop-2', self.u_last(), 'bundle-a',
                     'u08-remove-from-bundle-a', 'bundle-b', self.control):
            prefix = directory / f'poppler-{name}'
            subprocess.run([renderer, '-f', page, '-l', page, '-r', '144', '-png', '-singlefile',
                            str(self.revisions[name]['pdf']), str(prefix)], check=True, capture_output=True, timeout=600)
            digests[name] = sha(prefix.with_suffix('.png'))
        checks = dict(baseline_differs_from_add=digests['u00-text-baseline'] != digests['u01-add'],
                      add_equals_noop_1_and_2=digests['u01-add'] == digests['u02-noop-1'] == digests['u03-noop-2'],
                      candidate_a_equals_bundle_a=digests[self.u_last()] == digests['bundle-a'],
                      candidate_b_equals_bundle_b=digests['u08-remove-from-bundle-a'] == digests['bundle-b'],
                      removed_equals_text_only_control=digests['u08-remove-from-bundle-a'] == digests[self.control])
        if not all(checks.values()):
            raise Failure('Poppler raster expectations: ' + canonical(checks))
        return dict(checks, dpi=144, sha256=digests)

    def underline_lifecycle(self):
        return [
            ('text_baseline', self.u_text_baseline, ('confirm',)), ('add', self.u_add, ('text_baseline',)),
            ('noop_1', self.u_noop_1, ('add',)), ('noop_2', self.u_noop_2, ('noop_1',)),
            ('edit_remap', self.u_edit, ('noop_2',)), ('recipe', self.u_recipe, ('edit_remap',)),
            ('style', self.u_style, ('recipe',)), ('font', self.u_font, ('style',)),
            ('publish_a', self.u_publish_a, ('style',)), ('remove_from_bundle_a', self.u_remove, ('publish_a',)),
            ('publish_b', self.u_publish_b, ('remove_from_bundle_a',)), ('negatives', self.negatives, ('publish_b',)),
            ('tamper', self.u_tamper, ('publish_a',)), ('continuity', self.continuity, ('publish_b',)),
            ('text_only_control', self.u_control, ('text_baseline',)),
            ('raster_mupdf', self.u_raster_mupdf, ('publish_b', 'text_only_control')),
        ]

    # --- strikeout mode (§31/§32, semantic version 4) -----------------------------------------------------------

    def dl(self, name):
        return self.result['revisions'][name]['decoration_evidence']

    def s_text(self, name):
        return self.revisions[name]['opened']['semantic']['text']

    def s_sides(self, name):
        """Physical side of every written rectangle against its kind (§31.6): strikeout strictly above the
        serialized glyph baseline, underline at or below. One rectangle per decoration on this one-line text."""
        d = self.dl(name)
        kinds = d['kinds'] or []
        if d['rectangle_count'] != len(kinds):
            raise Failure(f'{name}: {d["rectangle_count"]} rectangles for {len(kinds)} decorations on a one-line text')
        expected = ['above' if k == 'strikeout' else 'at_or_below' for k in kinds]
        if d['rectangle_sides'] != expected:
            raise Failure(f'{name}: rectangle sides {d["rectangle_sides"]} differ from the kinds {kinds}')
        return dict(kinds=kinds, rectangle_sides=d['rectangle_sides'])

    def s_build(self, name, parent, request, asset=None, *, expect, paint):
        """Build one revision; check transition, version, observed paint transition, rectangle count and sides."""
        details = self.build(name, parent, request, asset)
        transition = details['revision']['transition']
        if (transition['classification'], transition['next_version']) != expect:
            raise Failure(f'{name}: transition {transition} differs from the expected {expect}')
        if details['revision']['semantic']['version'] != expect[1]:
            raise Failure(f'{name} reopens as semantic version {details["revision"]["semantic"]["version"]}')
        before, after = bool(self.dl(parent)['paint_body_sha256']), bool(self.dl(name)['paint_body_sha256'])
        observed = {(False, True): 'insert', (True, True): 'replace', (True, False): 'remove',
                    (False, False): 'none'}[(before, after)]
        if observed != paint:
            raise Failure(f'{name}: observed paint transition {observed}, expected {paint}')
        if self.dl(name)['rectangle_count'] != self.last_plan['next_decoration_rectangles']:
            raise Failure(f'{name}: written rectangles differ from the authorized plan')
        if self.dl(name)['decorations']:
            self.s_sides(name)
        return dict(details, paint_transition=observed)

    def s_item(self, kind, spec):
        return dict(kind=kind, start=spec['start'], end=spec['end'], start_affinity='outside', end_affinity='outside',
                    recipe=deepcopy(spec['recipe']))

    def s_text_baseline(self):
        details = self.s_build('s00-text-baseline', 'confirmed', dict(operation='save'), expect=('D', 2), paint='none')
        if 'decorations' in self.revisions['s00-text-baseline']['opened']['semantic']:
            raise Failure('a version 2 revision carries decorations')
        return details

    def s_add(self):
        item = self.s_item('strikeout', self.plan['strikeout_add'])
        request = dict(operation='reinterpret', changes={'decorations': {'add': item}})
        details = self.s_build('s01-add', 's00-text-baseline', request, expect=('E', 4), paint='insert')
        transition = details['revision']['transition']
        if transition['version'] != 2 or not transition['authority_preserved']:
            raise Failure('the first strikeout add is not version 2 → 4 with unchanged current authority')
        expected = [dict(item, id='current:0')]
        new, base = self.dl('s01-add'), self.dl('s00-text-baseline')
        if new['decorations'] != expected:
            raise Failure('added strikeout differs from the canonical current:0 record')
        if new['text_body_sha256'] != base['text_body_sha256']:
            raise Failure('the text group changed when only a strikeout was added')
        return dict(details, version_transition=[transition['version'], transition['next_version']],
                    decorations=expected, text_group_unchanged=True, separation=self.s_separation('s01-add'))

    def s_separation(self, name):
        """Serialized upper edge vs serialized glyph baseline per strikeout rectangle (PDF y-up)."""
        d = self.dl(name)
        rise = F(self.revisions[name]['opened']['semantic']['style']['rise'])
        rows = []
        for (x0, yu, x1, yl), kind in zip(d['rectangles_pdf'], d['kinds']):
            baseline = min((F(y) - rise for y in d['text_tm_y']), key=lambda b: abs(b - F(yu)))
            rows.append(dict(kind=kind, upper=yu, lower=yl, baseline=str(baseline),
                             upper_above_baseline=F(yu) > baseline, lower_above_baseline=F(yl) > baseline))
        if not all(r['upper_above_baseline'] for r in rows if r['kind'] == 'strikeout'):
            raise Failure(f'{name}: a strikeout is not physically above its baseline')
        return rows

    def s_noop(self, name, parent, *, require_pdf_bytes):
        details = self.s_build(name, parent, dict(operation='save'), expect=('D', 4), paint='replace')
        a, b = self.result['revisions'][parent], self.result['revisions'][name]
        old, new = self.revisions[parent]['opened'], self.revisions[name]['opened']
        keys = ('semantic_version', 'decorations', 'kinds', 'body_sha256', 'text_body_sha256', 'paint_body_sha256',
                'rectangle_count', 'rectangles_pdf', 'rectangle_sides', 'raster_mupdf_sha256')
        ops = lambda r: [(o.name, o.args) for o in self.body_operators(r)]  # noqa: E731
        checks = dict(pdf_bytes_identical=a['pdf_sha256'] == b['pdf_sha256'],
                      operator_sequence_identical=ops(parent) == ops(name),
                      owner_block_identical=a['owner']['block_sha256'] == b['owner']['block_sha256'],
                      semantic_identical=(old['semantic'], old['authority'], old['derived']) ==
                      (new['semantic'], new['authority'], new['derived']),
                      decoration_evidence_identical={k: a['decoration_evidence'][k] for k in keys} ==
                      {k: b['decoration_evidence'][k] for k in keys})
        required = [k for k in checks if k != 'pdf_bytes_identical' or require_pdf_bytes]
        if not all(checks[k] for k in required):
            raise Failure(f'{name} is not a canonical no-op: ' + canonical(checks))
        return dict(details, **checks, pdf_bytes_required=require_pdf_bytes)

    def body_operators(self, name):
        from pdfeditor.content_stream import operators
        revision = self.revisions[name]
        return list(operators(owned_body(revision['pdf'], revision['state'], self.sid)))

    def s_noop_1(self):
        return self.s_noop('s02-noop-1', 's01-add', require_pdf_bytes=True)

    def s_noop_2(self):
        return self.s_noop('s03-noop-2', 's02-noop-1', require_pdf_bytes=True)

    def s_edit(self):
        from pdfeditor import semantic_paint as paint
        edit = self.plan['strikeout_edit']
        before = self.revisions['s03-noop-2']['opened']['semantic']
        details = self.s_build('s04-edit-remap', 's03-noop-2', dict(operation='edit', **edit), expect=('D', 4),
                               paint='replace')
        # The production §27.7/§31.9 rule (kind-independent), never a harness-specific expectation.
        expected = paint.remap(before['text'], before['decorations'], edit['start'], edit['end'], edit['text'],
                               paint.MIXED_KINDS)
        after = self.dl('s04-edit-remap')
        if after['decorations'] != expected:
            raise Failure('edit remap differs from the production rule: ' + canonical(after['decorations']))
        if after['kinds'] != ['strikeout']:
            raise Failure('edit changed the decoration kind')
        return dict(details, edit=dict(edit, before_text=before['text'], after_text=self.s_text('s04-edit-remap')),
                    before_ranges=self.dl('s03-noop-2')['ranges'], after_ranges=after['ranges'],
                    before_rectangles=self.dl('s03-noop-2')['rectangles_pdf'], after_rectangles=after['rectangles_pdf'])

    def s_recipe(self):
        old = self.dl('s04-edit-remap')
        d = old['decorations'][0]
        request = dict(operation='reinterpret', changes={'decorations': {'recipe': dict(
            id=d['id'], start=d['start'], end=d['end'], recipe=deepcopy(self.plan['strikeout_recipe']))}})
        details = self.s_build('s05-recipe', 's04-edit-remap', request, expect=('E', 4), paint='replace')
        new = self.dl('s05-recipe')
        checks = dict(text_unchanged=self.s_text('s05-recipe') == self.s_text('s04-edit-remap'),
                      text_group_unchanged=old['text_body_sha256'] == new['text_body_sha256'],
                      horizontal_endpoints_unchanged=[r[0::2] for r in old['rectangles_pdf']] ==
                      [r[0::2] for r in new['rectangles_pdf']],
                      vertical_geometry_changed=[r[1::2] for r in old['rectangles_pdf']] !=
                      [r[1::2] for r in new['rectangles_pdf']],
                      kind_kept=new['kinds'] == ['strikeout'], version_4=new['semantic_version'] == 4,
                      authority_preserved=details['revision']['transition']['authority_preserved'],
                      recipe_applied=new['recipes'][0] == self.plan['strikeout_recipe'])
        if not all(checks.values()):
            raise Failure('recipe change: ' + canonical(checks))
        return dict(details, **checks, separation=self.s_separation('s05-recipe'))

    def s_em_check(self, name):
        """Exact em geometry from the written bytes, per rectangle and its own decoration recipe (to 1e-6):
        Tm y − upper = rise + size·offset (negative for a strikeout), upper − lower = size·thickness."""
        d = self.dl(name)
        style = self.revisions[name]['opened']['semantic']['style']
        size, rise = F(style['font_size']), F(style['rise'])
        tm = [F(v) for v in d['text_tm_y']]
        errors = []
        for (x0, yu, x1, yl), recipe in zip(d['rectangles_pdf'], d['recipes']):
            offset = min(abs(t - F(yu) - rise - size * F(recipe['offset_em'])) for t in tm)
            errors.append(max(offset, abs(F(yu) - F(yl) - size * F(recipe['thickness_em']))))
        worst = max(errors, default=F(0))
        if worst > F(2, 10**6):
            raise Failure(f'{name}: em recipe geometry is off by {float(worst)} pt')
        return float(worst)

    def s_style(self):
        details = self.s_build('s06-style', 's05-recipe', dict(operation='reinterpret',
                               changes=deepcopy(self.plan['strikeout_style'])), expect=('E', 4), paint='replace')
        old, new = self.dl('s05-recipe'), self.dl('s06-style')
        if (old['ranges'], old['recipes'], old['kinds']) != (new['ranges'], new['recipes'], new['kinds']):
            raise Failure('style change changed the decorations')
        drift = sorted(k for k in details['diff'] if not k.startswith('style.'))
        if drift:
            raise Failure('style change drifted unrelated semantics: ' + ', '.join(drift))
        return dict(details, decorations_preserved=True, em_geometry_max_error_pt=self.s_em_check('s06-style'),
                    right_endpoints=[[r[2] for r in old['rectangles_pdf']], [r[2] for r in new['rectangles_pdf']]],
                    separation=self.s_separation('s06-style'))

    def s_font(self):
        if 'font_b' not in self.inputs:
            raise Skip('--font-b not supplied')
        details = self.s_build('s07-font-b', 's06-style', dict(operation='reinterpret',
                               changes=dict(font=sha(self.inputs['font_b']))), self.inputs['font_b'],
                               expect=('E', 4), paint='replace')
        old, new = self.dl('s06-style'), self.dl('s07-font-b')
        checks = dict(provider_is_font_b=new['provider_sha256'] == sha(self.inputs['font_b']),
                      generated_font_is_font_b=self.generated_font_sources('s07-font-b') == {sha(self.inputs['font_b'])},
                      recipe_preserved=old['recipes'] == new['recipes'] and old['kinds'] == new['kinds'],
                      range_preserved=old['ranges'] == new['ranges'],
                      vertical_geometry_unchanged=[r[1::2] for r in old['rectangles_pdf']] ==
                      [r[1::2] for r in new['rectangles_pdf']],
                      endpoints_on_new_glyphs=all(r[0] in new['text_tm_x'] and F(r[2]) > F(r[0])
                                                  for r in new['rectangles_pdf']),
                      horizontal_endpoints_follow_font=[r[2] for r in old['rectangles_pdf']] !=
                      [r[2] for r in new['rectangles_pdf']],
                      version_4=new['semantic_version'] == 4)
        if not all(checks.values()):
            raise Failure('font change: ' + canonical(checks))
        return dict(details, **checks, em_geometry_max_error_pt=self.s_em_check('s07-font-b'),
                    right_endpoints=[[r[2] for r in old['rectangles_pdf']], [r[2] for r in new['rectangles_pdf']]])

    def s_last(self):
        return 's07-font-b' if 's07-font-b' in self.revisions else 's06-style'

    def s_publish(self, name, parent):
        details = self.publish(name, parent)
        details.update(self.fresh(name))
        a, b = self.result['revisions'][parent], self.result['revisions'][name]
        da, db = a['decoration_evidence'], b['decoration_evidence']
        checks = dict(pdf_identical=a['pdf_sha256'] == b['pdf_sha256'],
                      sidecar_identical=a['sidecar_sha256'] == b['sidecar_sha256'],
                      version_restored=(db['semantic_version'], b['semantic']['version']) == (da['semantic_version'],) * 2,
                      decorations_restored=db['decorations'] == da['decorations'],
                      owner_restored=a['owner'] == b['owner'],
                      provider_restored=da['provider_sha256'] == db['provider_sha256'] is not None,
                      canonical_body=b['semantic']['canonical'] is True and db['body_sha256'] == da['body_sha256'],
                      candidate_published_raster_identical=da['raster_mupdf_sha256'] == db['raster_mupdf_sha256'])
        if not all(checks.values()):
            raise Failure(f'{name}: ' + canonical(checks))
        return dict(details, **checks, version=db['semantic_version'], decorations=db['decorations'])

    def s_publish_a(self):
        details = self.s_publish('bundle-a', self.s_last())
        self.bundle_a = {p.name: sha(p) for p in (self.art / 'bundle-a').iterdir()}
        if self.dl('bundle-a')['kinds'] != ['strikeout'] or self.dl('bundle-a')['semantic_version'] != 4:
            raise Failure('bundle A is not a version 4 strikeout-only state')
        return details

    def s_remove(self):
        bundle = self.revisions['bundle-a']
        if (bundle['pdf'].parent != self.art / 'bundle-a' or
                sorted(p.name for p in bundle['pdf'].parent.iterdir()) != ['document.pdf', 'shared-flow.json']):
            raise Failure('the removal does not start from the published bundle A')
        d = self.dl('bundle-a')['decorations'][0]
        request = dict(operation='reinterpret', changes={'decorations': {'remove': dict(
            id=d['id'], start=d['start'], end=d['end'])}})
        details = self.s_build('s08-remove-from-bundle-a', 'bundle-a', request, expect=('E', 4), paint='remove')
        new = self.dl('s08-remove-from-bundle-a')
        checks = dict(input=[self.rel(bundle['pdf']), self.rel(bundle['sidecar'])], decorations_empty=new['decorations'] == [],
                      version_stays_4=new['semantic_version'] == 4, paint_group_absent=new['paint_body_sha256'] is None,
                      text_group_unchanged=new['text_body_sha256'] == self.dl('bundle-a')['text_body_sha256'])
        if not all(checks.values()):
            raise Failure('removal: ' + canonical(checks))
        return dict(details, **checks)

    def s_underline_add(self):
        item = self.s_item('underline', self.plan['mixed_underline'])
        details = self.s_build('s09-underline-add', 's08-remove-from-bundle-a', dict(operation='reinterpret', changes={
            'decorations': {'add': item}}), expect=('E', 4), paint='insert')
        new = self.dl('s09-underline-add')
        if new['decorations'] != [dict(item, id='current:0')] or new['semantic_version'] != 4:
            raise Failure('the underline add on version 4 is not the canonical current:0 record in version 4')
        return dict(details, decorations=new['decorations'], stays_version_4=True)

    def s_mixed_add(self):
        item = self.s_item('strikeout', self.plan['mixed_strikeout'])
        details = self.s_build('s10-mixed-strikeout-add', 's09-underline-add', dict(operation='reinterpret', changes={
            'decorations': {'add': item}}), expect=('E', 4), paint='replace')
        old, new = self.dl('s09-underline-add'), self.dl('s10-mixed-strikeout-add')
        expected = sorted([*old['decorations'], item], key=lambda d: (d['start'], d['end']))
        expected = [dict({k: v for k, v in d.items() if k != 'id'}, id=f'current:{i}') for i, d in enumerate(expected)]
        checks = dict(version_4=new['semantic_version'] == 4, one_decorations_list=new['decorations'] == expected,
                      canonical_ids=[d['id'] for d in new['decorations']] == [f'current:{i}' for i in
                                                                             range(len(expected))],
                      one_paint_group=new['body_split'] == 'v3 body grammar' and new['paint_body_sha256'] is not None,
                      paint_order_follows_decorations=new['kinds'] == [d['kind'] for d in expected],
                      underline_below_strikeout_above=new['rectangle_sides'] == [
                          'above' if d['kind'] == 'strikeout' else 'at_or_below' for d in expected],
                      underline_rectangle_unchanged=new['rectangles_pdf'][[d['kind'] for d in expected].index(
                          'underline')] == old['rectangles_pdf'][0],
                      text_group_unchanged=new['text_body_sha256'] == old['text_body_sha256'])
        if not all(checks.values()):
            raise Failure('mixed state: ' + canonical(checks))
        return dict(details, **checks, decorations=new['decorations'], rectangles=new['rectangles_pdf'],
                    separation=self.s_separation('s10-mixed-strikeout-add'))

    def s_mixed_noop(self):
        return self.s_noop('s11-mixed-noop', 's10-mixed-strikeout-add', require_pdf_bytes=False)

    def s_mixed_remove(self):
        old = self.dl('s11-mixed-noop')
        d = next(x for x in old['decorations'] if x['kind'] == 'strikeout')
        details = self.s_build('s12-mixed-strikeout-remove', 's11-mixed-noop', dict(operation='reinterpret', changes={
            'decorations': {'remove': dict(id=d['id'], start=d['start'], end=d['end'])}}), expect=('E', 4),
            paint='replace')
        new, before = self.dl('s12-mixed-strikeout-remove'), self.dl('s09-underline-add')
        checks = dict(version_stays_4=new['semantic_version'] == 4, underline_only=new['kinds'] == ['underline'],
                      underline_record_kept=new['decorations'] == before['decorations'],
                      same_body_as_underline_only_state=new['body_sha256'] == before['body_sha256'],
                      text_group_unchanged=new['text_body_sha256'] == old['text_body_sha256'])
        if not all(checks.values()):
            raise Failure('mixed strikeout removal: ' + canonical(checks))
        return dict(details, **checks, removed=d)

    def s_publish_b(self):
        details = self.s_publish('bundle-b', 's12-mixed-strikeout-remove')
        if {p.name: sha(p) for p in (self.art / 'bundle-a').iterdir()} != self.bundle_a:
            raise Failure('bundle A changed after bundle B')
        b = self.dl('bundle-b')
        if (b['semantic_version'], b['kinds']) != (4, ['underline']):
            raise Failure('bundle B is not a version 4 underline-only state')
        return dict(details, bundle_a_unchanged=True, underline_only=True, no_strikeout=True)

    def s_v3_control(self):
        """Small route control from the same version 2 baseline: underline add (2 → 3), then a disjoint
        strikeout add (3 → 4). The version 3 revision is never rewritten."""
        first = self.s_item('underline', self.plan['control_underline'])
        self.s_build('v3c-01-underline-add', 's00-text-baseline', dict(operation='reinterpret', changes={
            'decorations': {'add': first}}), expect=('E', 3), paint='insert')
        v3 = self.revisions['v3c-01-underline-add']
        v3_files = (sha(v3['pdf']), sha(v3['sidecar']))
        second = self.s_item('strikeout', self.plan['control_strikeout'])
        details = self.s_build('v3c-02-strikeout-add', 'v3c-01-underline-add', dict(operation='reinterpret', changes={
            'decorations': {'add': second}}), expect=('E', 4), paint='replace')
        from pdfeditor import semantic_layout as semantic
        a, b = self.dl('v3c-01-underline-add'), self.dl('v3c-02-strikeout-add')
        reopened = semantic.open_semantic_flow(v3['pdf'], v3['sidecar'])
        index = b['kinds'].index('underline')
        checks = dict(v2_to_v3=self.result['revisions']['v3c-01-underline-add']['transition']['version'] == 2 and
                      a['semantic_version'] == 3,
                      v3_to_v4=details['revision']['transition']['version'] == 3 and b['semantic_version'] == 4,
                      underline_exact=b['decorations'][index] == a['decorations'][0] and
                      b['rectangles_pdf'][index] == a['rectangles_pdf'][0],
                      mixed_canonical=self.result['revisions']['v3c-02-strikeout-add']['semantic']['canonical'] is True,
                      mixed_sides=b['rectangle_sides'] == ['above' if k == 'strikeout' else 'at_or_below'
                                                           for k in b['kinds']],
                      no_v3_rewrite=(sha(v3['pdf']), sha(v3['sidecar'])) == v3_files and
                      reopened['status'] == 'restored' and reopened['version'] == 3)
        if not all(checks.values()):
            raise Failure('v3 → v4 control: ' + canonical(checks))
        return dict(details, **checks, decorations=b['decorations'], rectangles=b['rectangles_pdf'])

    def s_negatives(self):
        """Expected stale-pair refusals: publications A/B and the mixed/underline-only candidates."""
        from pdfeditor import semantic_layout as semantic
        pairs = [('A.pdf+B.json', 'bundle-a', 'bundle-b'), ('B.pdf+A.json', 'bundle-b', 'bundle-a'),
                 ('mixed.pdf+underline-only.json', 's10-mixed-strikeout-add', 's12-mixed-strikeout-remove'),
                 ('underline-only.pdf+mixed.json', 's12-mixed-strikeout-remove', 's10-mixed-strikeout-add')]
        outcomes = {}
        for label, pdf, sidecar in pairs:
            opened = semantic.open_semantic_flow(self.revisions[pdf]['pdf'], self.revisions[sidecar]['sidecar'])
            outcomes[label] = opened.get('reason', 'restored')
            if opened['status'] == 'restored':
                raise Failure(f'stale revision pair {label} was accepted')
        return dict(expected_refusals=outcomes)

    def s_refusals(self):
        """Expected R refusals through the planner and the writer, without a write: physical separation,
        recipe sign per kind, overlap across kinds. A PASS here means the runtime refused each one."""
        from pdfeditor import semantic_layout as semantic
        from pdfeditor import semantic_writer as writer
        from pdfeditor.backend import PdfError
        base, mixed = 's00-text-baseline', 's09-underline-add'
        cases = [
            ('strikeout separation (-1/1000000000 em)', base,
             dict(kind='strikeout', start=0, end=3, recipe={'offset_em': '-1/1000000000', 'thickness_em': '1/20'})),
            ('strikeout with a positive offset', base,
             dict(kind='strikeout', start=0, end=3, recipe={'offset_em': '3/10', 'thickness_em': '1/20'})),
            ('underline with a negative offset', base,
             dict(kind='underline', start=0, end=3, recipe={'offset_em': '-13/120', 'thickness_em': '7/120'})),
            ('overlap: strikeout on the underline range', mixed,
             dict(kind='strikeout', start=0, end=1, recipe=dict(STRIKEOUT_RECIPE))),
            ('overlap: strikeout nesting the underline', mixed,
             dict(kind='strikeout', start=0, end=2, recipe=dict(STRIKEOUT_RECIPE))),
        ]
        outcomes = {}
        for label, parent, spec in cases:
            item = dict(spec, start_affinity='outside', end_affinity='outside')
            request = dict(operation='reinterpret', changes={'decorations': {'add': item}})
            source = self.revisions[parent]
            before = (sha(source['pdf']), sha(source['sidecar']))
            workspace = self.art / 'refusals' / f'case-{len(outcomes)}'
            workspace.mkdir(parents=True)
            reasons = []
            for call in (lambda: semantic.plan_semantic_transition(source['pdf'], source['sidecar'], request),
                         lambda: writer.build_semantic_candidate(source['pdf'], source['sidecar'], request,
                                                                 workspace=workspace)):
                try:
                    call()
                except PdfError as error:
                    reasons.append(str(error))
                else:
                    raise Failure(f'{label} was accepted')
            if any(workspace.iterdir()) or (sha(source['pdf']), sha(source['sidecar'])) != before:
                raise Failure(f'{label}: a refused request wrote a candidate or changed its input')
            outcomes[label] = dict(planner=reasons[0], writer=reasons[1], parent=parent)
        return dict(expected_refusals=outcomes)

    def s_tamper(self):
        """Expected refusals on copies of bundle A (never on the bundles): two resealed semantic tampers, a
        paint-geometry tamper of the PDF, and the same geometry tamper with the owner witness rebound in memory so
        that the production island check reaches its canonical-body comparison."""
        import pymupdf
        from pdfeditor import semantic_layout as semantic
        from pdfeditor import semantic_paint as paint
        from pdfeditor import source_ownership as owned
        from pdfeditor.backend import PdfError
        from pdfeditor.content_stream import ContentPage
        from pdfeditor.editable import _seal
        bundle = self.revisions['bundle-a']
        directory = self.art / 'tamper'
        directory.mkdir()
        outcomes = {}
        for label, change in (('resealed kind change (strikeout → underline)', lambda d: d.update(kind='underline')),
                              ('resealed recipe sign change', lambda d: d['recipe'].update(
                                  offset_em=str(-F(d['recipe']['offset_em']))))):
            state = deepcopy(bundle['state'])
            state.pop('model_sha256')
            change(state['slots'][self.sid]['semantic']['payload']['decorations'][0])
            path = directory / ('semantic-' + str(len(outcomes)) + '.json')
            path.write_text(json.dumps(_seal(state), indent=2) + '\n', encoding='utf-8')
            opened = semantic.open_semantic_flow(bundle['pdf'], path)
            outcomes[label] = opened.get('reason', 'restored')
            if opened['status'] == 'restored':
                raise Failure(f'{label} was accepted')
        body = owned_body(bundle['pdf'], bundle['state'], self.sid)
        x0, yu, x1, yl = self.dl('bundle-a')['rectangles_pdf'][0]
        line = f'{x0} {yu} m {x1} {yu} l {x1} {yl} l {x0} {yl} l'.encode()
        if body.count(line) != 1:
            raise Failure('strikeout rectangle bytes not found once in the owned body')
        tampered = directory / 'geometry-tamper.pdf'
        from pdfeditor.semantic_island import decimal
        lower = decimal(F(yl) - 1)  # the whole lower edge 1 pt down: still one axis-aligned rectangle
        with pymupdf.open(bundle['pdf']) as document:
            xref = document[self.result['target']['page'] - 1].get_contents()[-1]
            data = document.xref_stream(xref)
            moved = f'{x0} {yu} m {x1} {yu} l {x1} {lower} l {x0} {lower} l'.encode()
            document.update_stream(xref, data.replace(line, moved, 1))
            document.save(tampered)
        opened = semantic.open_semantic_flow(tampered, bundle['sidecar'])
        outcomes['paint geometry tamper'] = opened.get('reason', 'restored')
        if opened['status'] == 'restored':
            raise Failure('a paint geometry tamper was accepted')
        # Rebound witness (in memory only): reach the production canonical-body comparison of `_island`.
        value = deepcopy(bundle['state'])
        slot = value['slots'][self.sid]
        content = ContentPage(tampered, self.result['target']['page'])
        try:  # the tampered body is still grammatical, so the witness can be rebound to it
            span = owned.inventory(content.streams[-content.page.xref])[slot['source_output']['marker_id']]
            slot['source_output']['current'] = owned.witness(content, slot['source_output'], span,
                                                             **owned.injected(paint.body_grammar))
        finally:
            content.close()
        payload, current = slot['semantic']['payload'], slot['semantic']['current']
        font = semantic._asset(value, slot, payload, current)
        try:
            _, plan = semantic._derive(value, slot, payload, font)
            semantic._island(tampered, value, self.sid, payload, plan, current, font, payload['decorations'],
                             slot['semantic']['version'])
        except PdfError as error:
            outcomes['paint geometry tamper, owner witness rebound (production island check)'] = str(error)
        else:
            raise Failure('a rebound paint geometry tamper passed the production island check')
        finally:
            font.font.close()
        return dict(expected_refusals=outcomes)

    def s_control(self):
        """Text-only control: the same edit/style/font transitions on version 2, never decorated."""
        steps = [('control-01-edit', dict(operation='edit', **self.plan['strikeout_edit']), None),
                 ('control-02-style', dict(operation='reinterpret', changes=deepcopy(self.plan['strikeout_style'])), None)]
        if 's07-font-b' in self.revisions:
            steps.append(('control-03-font-b', dict(operation='reinterpret', changes=dict(
                font=sha(self.inputs['font_b']))), self.inputs['font_b']))
        parent = 's00-text-baseline'
        for name, request, asset in steps:
            self.s_build(name, parent, request, asset, expect=(request['operation'] == 'edit' and 'D' or 'E', 2),
                         paint='none')
            parent = name
        self.control = parent
        removed = self.dl('s08-remove-from-bundle-a') if 's08-remove-from-bundle-a' in self.revisions else None
        return dict(revisions=[s[0] for s in steps], final=parent, text_group_identical_to_removed=
                    None if removed is None else removed['text_body_sha256'] == self.dl(parent)['text_body_sha256'])

    S_RASTER = ['s00-text-baseline', 's01-add', 's02-noop-1', 's03-noop-2', 's04-edit-remap', 's05-recipe', 's06-style',
                's07-font-b', 'bundle-a', 's08-remove-from-bundle-a', 's09-underline-add', 's10-mixed-strikeout-add',
                's11-mixed-noop', 's12-mixed-strikeout-remove', 'bundle-b']

    def s_raster_checks(self, d):
        last = self.s_last()
        return dict(text_only_differs_from_strikeout=d['s00-text-baseline'] != d['s01-add'],
                    strikeout_equals_noop_1_and_2=d['s01-add'] == d['s02-noop-1'] == d['s03-noop-2'],
                    recipe_differs_from_previous=d['s05-recipe'] != d['s04-edit-remap'] if 's05-recipe' in d else None,
                    style_differs_from_previous=d['s06-style'] != d['s05-recipe'] if 's06-style' in d else None,
                    font_differs_from_previous=d['s07-font-b'] != d['s06-style'] if 's07-font-b' in d else None,
                    removed_equals_text_only_control=d['s08-remove-from-bundle-a'] == d[self.control],
                    mixed_differs_from_underline_only=d['s10-mixed-strikeout-add'] != d['s09-underline-add'],
                    mixed_differs_from_strikeout_only=d['s10-mixed-strikeout-add'] != d['bundle-a'],
                    mixed_equals_mixed_noop=d['s11-mixed-noop'] == d['s10-mixed-strikeout-add'],
                    strikeout_removal_returns_to_underline_only=d['s12-mixed-strikeout-remove'] == d['s09-underline-add'],
                    candidate_a_equals_bundle_a=d[last] == d['bundle-a'],
                    candidate_b_equals_bundle_b=d['s12-mixed-strikeout-remove'] == d['bundle-b'])

    def s_raster_mupdf(self):
        directory = self.art / 'raster'
        directory.mkdir(parents=True, exist_ok=True)
        page = self.result['target']['page']
        names = [n for n in self.S_RASTER if n in self.revisions] + [self.control]
        digests = {n: mupdf_raster(self.revisions[n]['pdf'], page, directory / f'mupdf-{n}.png') for n in names}
        if any(digests[n] != self.dl(n)['raster_mupdf_sha256'] for n in names):
            raise Failure('MuPDF raster is not deterministic between two renders')
        checks = self.s_raster_checks(digests)
        if not all(v for v in checks.values() if v is not None):
            raise Failure('MuPDF raster expectations: ' + canonical(checks))
        return dict(checks, dpi=144, sha256=digests, control=self.control)

    def s_raster_poppler(self):
        renderer = shutil.which('pdftoppm')
        if renderer is None:
            raise Skip('Poppler (pdftoppm) unavailable')
        directory = self.art / 'raster'
        directory.mkdir(parents=True, exist_ok=True)
        page = str(self.result['target']['page'])
        digests = {}
        for name in [n for n in self.S_RASTER if n in self.revisions] + [self.control]:
            prefix = directory / f'poppler-{name}'
            subprocess.run([renderer, '-f', page, '-l', page, '-r', '144', '-png', '-singlefile',
                            str(self.revisions[name]['pdf']), str(prefix)], check=True, capture_output=True, timeout=600)
            digests[name] = sha(prefix.with_suffix('.png'))
        # Identities only, plus the differences the contract guarantees in pixels (§32.6 Poppler list).
        full = self.s_raster_checks(digests)
        keys = ('text_only_differs_from_strikeout', 'strikeout_equals_noop_1_and_2', 'removed_equals_text_only_control',
                'mixed_differs_from_underline_only', 'mixed_equals_mixed_noop',
                'strikeout_removal_returns_to_underline_only', 'candidate_a_equals_bundle_a',
                'candidate_b_equals_bundle_b')
        checks = {k: full[k] for k in keys}
        if not all(checks.values()):
            raise Failure('Poppler raster expectations: ' + canonical(checks))
        return dict(checks, dpi=144, sha256=digests)

    def strikeout_lifecycle(self):
        return [
            ('text_baseline', self.s_text_baseline, ('confirm',)), ('add', self.s_add, ('text_baseline',)),
            ('noop_1', self.s_noop_1, ('add',)), ('noop_2', self.s_noop_2, ('noop_1',)),
            ('edit_remap', self.s_edit, ('noop_2',)), ('recipe', self.s_recipe, ('edit_remap',)),
            ('style', self.s_style, ('recipe',)), ('font', self.s_font, ('style',)),
            ('publish_a', self.s_publish_a, ('style',)), ('remove_from_bundle_a', self.s_remove, ('publish_a',)),
            ('underline_add', self.s_underline_add, ('remove_from_bundle_a',)),
            ('mixed_strikeout_add', self.s_mixed_add, ('underline_add',)),
            ('mixed_noop', self.s_mixed_noop, ('mixed_strikeout_add',)),
            ('mixed_strikeout_remove', self.s_mixed_remove, ('mixed_noop',)),
            ('publish_b', self.s_publish_b, ('mixed_strikeout_remove',)),
            ('v3_to_v4_control', self.s_v3_control, ('text_baseline',)),
            ('negatives', self.s_negatives, ('publish_b',)), ('refusals', self.s_refusals, ('underline_add',)),
            ('tamper', self.s_tamper, ('publish_a',)), ('continuity', self.continuity, ('publish_b',)),
            ('text_only_control', self.s_control, ('text_baseline',)),
            ('raster_mupdf', self.s_raster_mupdf, ('publish_b', 'text_only_control')),
        ]

    def preserved(self):
        after = {k: sha(v) for k, v in self.inputs.items()}
        if after != self.before:
            raise Failure('an input file changed: ' + ', '.join(k for k in after if after[k] != self.before[k]))
        return dict(inputs_unchanged=True, sha256=after)

    def execute(self):
        self.out.mkdir(parents=True)
        (self.out / 'logs').mkdir()
        self.art.mkdir()
        self.save()
        lifecycle = [
            ('preflight', self.preflight, ()), ('baseline', self.baseline, ('preflight',)),
            ('confirm', self.confirm, ('baseline',))]
        if self.mode == 'underline':
            lifecycle += self.underline_lifecycle()
            optional = ('raster_poppler', self.u_raster_poppler, ('publish_b', 'text_only_control'))
        elif self.mode == 'strikeout':
            lifecycle += self.strikeout_lifecycle()
            optional = ('raster_poppler', self.s_raster_poppler, ('publish_b', 'text_only_control'))
        else:
            optional = ('raster_poppler', self.raster_poppler, ('publish_b',))
            lifecycle += self.text_lifecycle()
        try:
            for name, function, needs in lifecycle:
                self.stage(name, function, needs=needs)
            self.stage(optional[0], optional[1], required=False, needs=optional[2])
        finally:
            if hasattr(self, 'before'):
                self.stage('inputs_preserved', self.preserved)
            self.result['verdict'] = verdict(self.result['stages'])
            self.result['finished_at'] = datetime.now(timezone.utc).isoformat()
            self.save()
            (self.out / 'report.md').write_text(report(self.result), encoding='utf-8')
        return EXIT[self.result['verdict']]

    def text_lifecycle(self):
        return [
            ('edit', self.edit, ('confirm',)),
            ('style', self.style, ('edit',)), ('font', self.font, ('edit',)),
            ('noop', self.noop, ('edit',)), ('publish_a', self.publish_a, ('noop',)),
            ('edit_from_bundle_a', self.edit_b, ('publish_a',)), ('font_back', self.font_back, ('edit_from_bundle_a',)),
            ('publish_b', self.publish_b, ('edit_from_bundle_a',)), ('negatives', self.negatives, ('publish_b',)),
            ('continuity', self.continuity, ('publish_b',)),
            ('raster_mupdf', self.raster_mupdf, ('publish_b',)),
        ]


def verdict(stages):
    required = [s for s in stages if s['required']]
    if any(s['status'] == FAIL for s in stages):
        return 'FAIL'
    refused = [s for s in required if s['status'] == REFUSED]
    if refused:
        return 'UNSUPPORTED_TARGET' if refused[0]['id'] in ('preflight', 'baseline', 'confirm') else 'REFUSED'
    if any(s['status'] != PASS for s in required):
        return 'INCOMPLETE'
    return 'PASS'


def report(result):
    env = result.get('environment') or {}
    windows = result.get('windows_execution')
    lines = [
        '# Semantic lifecycle external validation report', '',
        f'> {DISCLAIMER}', '',
        f'- **Verdict:** `{result["verdict"]}`',
        f'- **Windows execution:** {"yes — this run executed on Windows" if windows else "NO — not Windows evidence"}',
        f'- **Executed:** {result["executed_at"]} → {result.get("finished_at")}',
        f'- **Platform:** {(env.get("os") or {}).get("platform", "unavailable")}; Python {env.get("python")}; '
        f'PyMuPDF {env.get("pymupdf")}; fontTools {env.get("fonttools")}; uharfbuzz {env.get("uharfbuzz")}; '
        f'pypdf {env.get("pypdf")}',
        f'- **Poppler:** {(env.get("poppler") or {}).get("version") or "unavailable"}; '
        f'**LibreOffice:** {env.get("libreoffice", "unavailable")}',
        f'- **Repository:** `{(env.get("repository") or {}).get("commit")}` '
        f'(dirty: {(env.get("repository") or {}).get("dirty")}); runtime digest '
        f'`{(env.get("repository") or {}).get("runtime_digest")}`', '']
    inputs = result.get('inputs') or {}
    if inputs:
        pdf = inputs['pdf']
        lines += ['## Inputs', '', '| Input | File | SHA-256 |', '|---|---|---|',
                  f'| PDF | `{pdf["filename"]}` ({pdf["page_count"]} pages; producer {pdf["producer"]!r}; '
                  f'LibreOffice claimed: {pdf["libreoffice_claimed"]}) | `{pdf["sha256"]}` |',
                  f'| sidecar | `{inputs["sidecar"]["filename"]}` | `{inputs["sidecar"]["sha256"]}` |']
        lines += [f'| {k} | `{v["filename"]}` | `{v["sha256"]}` |' for k, v in inputs['fonts'].items()]
        lines.append('')
    if result.get('target'):
        t = result['target']
        lines += ['## Target', '', f'slot `{t["slot_id"]}`, page {t["page"]}, sidecar `{t["sidecar_schema"]}`, '
                  f'source font `{t["source_font_name"]}`, generated fonts {t["generated_fonts"]}', '']
    lines += ['## Stages', '', '| Stage | Required | Status | Note |', '|---|---|---|---|']
    for s in result['stages']:
        note = (s['error'] or '').replace('|', '\\|').replace('\n', ' ')[:160]
        lines.append(f'| `{s["id"]}` | {"yes" if s["required"] else "no"} | **{s["status"]}** | {note} |')
    if result['revisions']:
        lines += ['', '## Revisions', '',
                  '| Revision | Text | Size | Tracking | Tw | Edges | Font | Provenance | Body ops | PDF SHA |',
                  '|---|---|---|---|---|---|---|---|---|---|']
        for name, r in result['revisions'].items():
            m = r['semantic']
            lines.append(f'| `{name}` | `{m["text"]}` | {m["font_size"]} | {m["tracking"]} | {m["word_spacing"]} | '
                         f'{m["edges"]} | `{m["font_sha256"][:12]}` | {m["provenance"]} | {r["island"]["operators"]} | '
                         f'`{r["pdf_sha256"][:12]}` |')
    if result.get('mode') == 'underline':
        lines += underline_report(result, windows)
    if result.get('mode') == 'strikeout':
        lines += strikeout_report(result, windows)
    candidates = ('`u0N-*/`, `control-0N-*/` (semantic-candidate-*), `bundle-a/`, `bundle-b/`, `tamper/`, `raster/`'
                  if result.get('mode') == 'underline' else
                  '`s0N-*/`, `s1N-*/`, `v3c-0N-*/`, `control-0N-*/` (semantic-candidate-*), `bundle-a/`, `bundle-b/`, '
                  '`refusals/`, `tamper/`, `raster/`' if result.get('mode') == 'strikeout' else
                  'candidates (`candidate-*/semantic-candidate-*/`), `bundle-a/`, `bundle-b/`, `raster/`')
    lines += ['', '## Artifacts', '', '- `result.json` — machine-readable result (schema in the README)',
              f'- `artifacts/` — {candidates}',
              '- `logs/harness.log` — stage log with tracebacks of refused/failed stages', '']
    return '\n'.join(lines)


def underline_report(result, windows):
    """Underline section (mode underline): version transition, decoration lifecycle, checks, final state."""
    stages = {s['id']: s for s in result['stages']}
    details = lambda name: stages.get(name, {}).get('details') or {}  # noqa: E731
    flag = lambda value: ('yes' if value is True else '**no**' if value is False else 'n/a' if value is None  # noqa: E731
                          else value)
    lines = ['', '## Underline lifecycle', '', f'> {result.get("underline_disclaimer", UNDERLINE_DISCLAIMER)}', '',
             f'- **Validation kind:** `{result.get("validation_kind")}`; **Windows underline execution:** '
             f'{"yes — this run executed on Windows" if windows else "NO — not Windows evidence"}',
             f'- **Semantic version transition:** {details("add").get("version_transition")} (explicit '
             f'`decorations.add`)', '']
    lines += ['| Revision | Transition | Version | Decorations | Rectangles (PDF x0 yu x1 yl) | Paint group | '
              'Text group | MuPDF |', '|---|---|---|---|---|---|---|---|']
    for name, r in result['revisions'].items():
        u, t = r.get('underline'), r.get('transition') or {}
        if not u:
            continue
        ranges = ', '.join(f'[{a},{b})' for a, b in u['ranges'] or []) or ('—' if u['ranges'] is None else 'none')
        rects = '; '.join(' '.join(v) for v in u['rectangles_pdf']) or '—'
        lines.append(f'| `{name}` | {t.get("classification", "")} {t.get("version", "")}→{t.get("next_version", "")} | '
                     f'{u["semantic_version"]} | {ranges} | {rects} | '
                     f'`{(u["paint_body_sha256"] or "none")[:12]}` | `{(u["text_body_sha256"] or "?")[:12]}` | '
                     f'`{u["raster_mupdf_sha256"][:12]}` |')
    checks = [
        ('no-op #1 / #2', [details('noop_1').get(k) and details('noop_2').get(k) for k in
                           ('pdf_bytes_identical', 'owner_block_identical', 'semantic_identical', 'underline_identical')],
         'PDF bytes, owner block, semantic state, underline evidence identical'),
        ('edit remap', [details('edit_remap').get('before_ranges'), details('edit_remap').get('after_ranges')],
         'before → after ranges'),
        ('recipe', [details('recipe').get(k) for k in ('horizontal_endpoints_unchanged', 'vertical_geometry_changed',
                                                      'authority_preserved')], 'x unchanged, y changed, authority kept'),
        ('style', [details('style').get('decorations_preserved'), details('style').get('em_geometry_max_error_pt')],
         'decorations kept; max em geometry error (pt)'),
        ('font', [details('font').get(k) for k in ('provider_is_font_b', 'recipe_preserved',
                                                    'vertical_geometry_unchanged', 'generated_font_is_font_b')],
         'provider B, recipe kept, y unchanged, generated font B'),
        ('publication A', [details('publish_a').get(k) for k in ('candidate_bytes_identical', 'fresh_process',
                                                                 'candidate_published_raster_identical')],
         'bytes, fresh process, raster'),
        ('remove (from bundle A)', [details('remove_from_bundle_a').get(k) for k in
                                    ('decorations_empty', 'version_stays_3', 'paint_group_absent', 'text_group_unchanged')],
         'decorations [], version 3, no paint group, text group kept'),
        ('publication B', [details('publish_b').get(k) for k in ('candidate_bytes_identical', 'fresh_process',
                                                                 'bundle_a_unchanged', 'text_only_body')],
         'bytes, fresh process, bundle A immutable, text-only'),
    ]
    lines += ['', '| Check | Result | Meaning |', '|---|---|---|']
    for label, values, meaning in checks:
        text = ', '.join(str(flag(v)) for v in values).replace('|', '\\|')
        lines.append(f'| {label} | {text[:300]} | {meaning} |')
    for stage, renderer in (('raster_mupdf', 'MuPDF'), ('raster_poppler', 'Poppler')):
        entry = stages.get(stage)
        if not entry:
            continue
        if entry['status'] == SKIPPED:
            lines.append(f'| {renderer} 144 dpi | SKIPPED | {entry["error"]} |')
            continue
        values = {k: v for k, v in (entry['details'] or {}).items() if isinstance(v, bool) or v is None}
        lines.append(f'| {renderer} 144 dpi | {", ".join(f"{k}: {flag(v)}" for k, v in values.items())} | '
                     f'{entry["status"]} |')
    refusals = {**(details('negatives').get('expected_refusals') or {}), **(details('tamper').get('expected_refusals') or {})}
    if refusals:
        lines += ['', 'Expected refusals (the runtime must refuse each):', '']
        lines += [f'- {label}: `{reason}`' for label, reason in refusals.items()]
    final = next((r['underline'] for n, r in reversed(list(result['revisions'].items()))
                  if n.startswith('bundle-') and r.get('underline')), None)
    if final:
        lines += ['', f'- **Final state (last bundle):** semantic version {final["semantic_version"]}, decorations '
                  f'{final["decorations"]}, paint group {"absent" if final["paint_body_sha256"] is None else "present"}']
    return lines


def strikeout_report(result, windows):
    """Strikeout section (mode strikeout): versions, decoration lifecycle with kinds and sides, checks, refusals."""
    stages = {s['id']: s for s in result['stages']}
    details = lambda name: stages.get(name, {}).get('details') or {}  # noqa: E731
    flag = lambda value: ('yes' if value is True else '**no**' if value is False else 'n/a' if value is None  # noqa: E731
                          else value)
    lines = ['', '## Strikeout lifecycle', '', f'> {result.get("strikeout_disclaimer", STRIKEOUT_DISCLAIMER)}', '',
             f'- **Validation kind:** `{result.get("validation_kind")}`; **strikeout runtime:** '
             f'{result.get("strikeout_runtime")}; **Windows strikeout execution:** '
             f'{"yes — this run executed on Windows" if windows else "NO — not Windows evidence"}',
             f'- **Semantic version transition:** {details("add").get("version_transition")} (explicit strikeout '
             f'`decorations.add`)', '']
    lines += ['| Revision | Transition | Version | Decorations (kind range) | Rectangles (PDF x0 yu x1 yl) | Sides | '
              'Paint group | Text group | MuPDF |', '|---|---|---|---|---|---|---|---|---|']
    for name, r in result['revisions'].items():
        d, t = r.get('decoration_evidence'), r.get('transition') or {}
        if not d:
            continue
        decorations = ', '.join(f'{x["kind"]} [{x["start"]},{x["end"]})' for x in d['decorations'] or []) or (
            '—' if d['decorations'] is None else 'none')
        rects = '; '.join(' '.join(v) for v in d['rectangles_pdf']) or '—'
        sides = ', '.join(s or '?' for s in d['rectangle_sides']) or '—'
        lines.append(f'| `{name}` | {t.get("classification", "")} {t.get("version", "")}→{t.get("next_version", "")} | '
                     f'{d["semantic_version"]} | {decorations} | {rects} | {sides} | '
                     f'`{(d["paint_body_sha256"] or "none")[:12]}` | `{(d["text_body_sha256"] or "?")[:12]}` | '
                     f'`{d["raster_mupdf_sha256"][:12]}` |')
    noop_keys = ('pdf_bytes_identical', 'operator_sequence_identical', 'owner_block_identical', 'semantic_identical',
                 'decoration_evidence_identical')
    checks = [
        ('no-op #1 / #2', [details('noop_1').get(k) and details('noop_2').get(k) for k in noop_keys],
         'PDF bytes, operators, owner block, semantic state, decoration evidence identical'),
        ('edit remap', [details('edit_remap').get('before_ranges'), details('edit_remap').get('after_ranges')],
         'before → after ranges (production rule)'),
        ('recipe', [details('recipe').get(k) for k in ('horizontal_endpoints_unchanged', 'vertical_geometry_changed',
                                                      'kind_kept', 'authority_preserved')],
         'x unchanged, y changed, kind kept, authority kept'),
        ('style', [details('style').get('decorations_preserved'), details('style').get('em_geometry_max_error_pt')],
         'decorations kept; max em geometry error (pt)'),
        ('font', [details('font').get(k) for k in ('provider_is_font_b', 'generated_font_is_font_b', 'recipe_preserved',
                                                    'range_preserved', 'vertical_geometry_unchanged',
                                                    'horizontal_endpoints_follow_font')],
         'provider B, generated font B, recipe/range kept, y unchanged, x follows B'),
        ('publication A', [details('publish_a').get(k) for k in ('pdf_identical', 'sidecar_identical', 'fresh_process',
                                                                 'version_restored', 'decorations_restored',
                                                                 'owner_restored', 'provider_restored', 'canonical_body',
                                                                 'candidate_published_raster_identical')],
         'bytes, fresh process, version, decorations, owner, provider, canonical, raster'),
        ('remove (from bundle A)', [details('remove_from_bundle_a').get(k) for k in
                                    ('decorations_empty', 'version_stays_4', 'paint_group_absent', 'text_group_unchanged')],
         'decorations [], version 4, no paint group, text group kept'),
        ('mixed v4', [details('mixed_strikeout_add').get(k) for k in
                      ('version_4', 'one_decorations_list', 'canonical_ids', 'one_paint_group',
                       'paint_order_follows_decorations', 'underline_below_strikeout_above')],
         'v4, one list, current:i, one group, order, underline below / strikeout above'),
        ('mixed no-op', [details('mixed_noop').get(k) for k in noop_keys], 'as no-op #1/#2 (PDF bytes recorded)'),
        ('mixed strikeout remove', [details('mixed_strikeout_remove').get(k) for k in
                                    ('version_stays_4', 'underline_only', 'same_body_as_underline_only_state')],
         'v4, underline only, body equals the underline-only state'),
        ('publication B', [details('publish_b').get(k) for k in ('pdf_identical', 'sidecar_identical', 'fresh_process',
                                                                 'bundle_a_unchanged', 'underline_only')],
         'bytes, fresh process, bundle A immutable, underline only'),
        ('v3 → v4 control', [details('v3_to_v4_control').get(k) for k in
                             ('v2_to_v3', 'v3_to_v4', 'underline_exact', 'mixed_canonical', 'no_v3_rewrite')],
         'v2→v3, v3→v4, underline exact, canonical, v3 not rewritten'),
    ]
    lines += ['', '| Check | Result | Meaning |', '|---|---|---|']
    for label, values, meaning in checks:
        text = ', '.join(str(flag(v)) for v in values).replace('|', '\\|')
        lines.append(f'| {label} | {text[:300]} | {meaning} |')
    for stage, renderer in (('raster_mupdf', 'MuPDF'), ('raster_poppler', 'Poppler')):
        entry = stages.get(stage)
        if not entry:
            continue
        if entry['status'] == SKIPPED:
            lines.append(f'| {renderer} 144 dpi | SKIPPED | {entry["error"]} |')
            continue
        values = {k: v for k, v in (entry['details'] or {}).items() if isinstance(v, bool) or v is None}
        lines.append(f'| {renderer} 144 dpi | {", ".join(f"{k}: {flag(v)}" for k, v in values.items())} | '
                     f'{entry["status"]} |')
    separation = details('add').get('separation') or []
    if separation:
        lines += ['', 'Physical separation (§31.6, PDF y-up, serialized): ' + '; '.join(
            f'{r["kind"]} upper {r["upper"]} > baseline {r["baseline"]}: {flag(r["upper_above_baseline"])}'
            for r in separation)]
    refusals = {**(details('negatives').get('expected_refusals') or {}), **(details('tamper').get('expected_refusals') or {})}
    rules = details('refusals').get('expected_refusals') or {}
    if refusals or rules:
        lines += ['', 'Expected refusals (the runtime must refuse each):', '']
        lines += [f'- {label}: `{reason}`' for label, reason in refusals.items()]
        lines += [f'- {label} (planner): `{r["planner"]}`' for label, r in rules.items()]
    final = next((r['decoration_evidence'] for n, r in reversed(list(result['revisions'].items()))
                  if n.startswith('bundle-') and r.get('decoration_evidence')), None)
    if final:
        lines += ['', f'- **Final state (last bundle):** semantic version {final["semantic_version"]}, decorations '
                  f'{final["decorations"]}, paint group {"absent" if final["paint_body_sha256"] is None else "present"}']
    return lines


def usage(message):
    print('error: ' + message, file=sys.stderr)
    return EXIT['USAGE']


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m evaluations.semantic_lifecycle.windows_validation')
    sub = parser.add_subparsers(dest='command', required=True)
    synthetic = sub.add_parser('prepare-synthetic', help='write source.pdf + spec.json (in-scope control or '
                                                         'the LibreOffice page shape)')
    synthetic.add_argument('--output-dir', required=True)
    synthetic.add_argument('--libreoffice-shape', action='store_true')
    fonts = sub.add_parser('prepare-fonts', help='write font-a.ttf/font-b.ttf (synthetic, or unhinted A/B/space '
                                                 'subsets of installed fonts)')
    fonts.add_argument('--output-dir', required=True)
    fonts.add_argument('--from-a')
    fonts.add_argument('--from-b')
    prep = sub.add_parser('prepare', help='explicit spec → shared-flow v2 owner sidecar for one source slot')
    prep.add_argument('--source-pdf', required=True)
    prep.add_argument('--spec', required=True)
    prep.add_argument('--font-a', required=True)
    prep.add_argument('--output-dir', required=True)
    run = sub.add_parser('run', help='run the lifecycle and write result.json, report.md, artifacts/, logs/')
    run.add_argument('--pdf', required=True)
    run.add_argument('--sidecar', required=True)
    run.add_argument('--font-a', required=True)
    run.add_argument('--font-b')
    run.add_argument('--output-dir', required=True)
    run.add_argument('--slot-id')
    run.add_argument('--plan', help='JSON overriding edit/style/next_edit (text), underline_* (underline) or '
                                    'strikeout_*/mixed_*/control_* (strikeout) requests')
    run.add_argument('--mode', choices=('text', 'underline', 'strikeout'), default='text',
                     help='text (default): the §24 text-only lifecycle; underline: the §28 underline lifecycle; '
                          'strikeout: the §32 strikeout (semantic version 4) lifecycle')
    args = parser.parse_args(argv)
    if args.command == 'prepare-synthetic':
        return prepare_synthetic(args)
    if args.command == 'prepare-fonts':
        return prepare_fonts(args)
    if args.command == 'prepare':
        return prepare(args)
    missing = [p for p in (args.pdf, args.sidecar, args.font_a, args.font_b, args.plan) if p and not Path(p).is_file()]
    if missing:
        return usage('missing input file(s): ' + ', '.join(missing))
    if Path(args.output_dir).exists():
        return usage(f'output directory exists (results are never overwritten): {args.output_dir}')
    code = Run(args).execute()
    print(f'verdict: {load(Path(args.output_dir) / "result.json")["verdict"]} (exit {code}); '
          f'report: {Path(args.output_dir) / "report.md"}')
    return code


if __name__ == '__main__':
    sys.exit(main())
