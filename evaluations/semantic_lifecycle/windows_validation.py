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

Stage statuses: PASS (expected success), REFUSED (runtime refused for a scope or
safety reason), SKIPPED (not applicable or a prerequisite did not pass), FAIL
(behaviour differs from the expectation). Exit codes: 0 PASS, 1 FAIL, 2 usage
error / output collision, 3 REFUSED or UNSUPPORTED_TARGET, 4 INCOMPLETE.

The PR that added this harness did not execute it on Windows; a result is
Windows evidence only when `result.json` says `windows_execution: true`.
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


# --- the run -----------------------------------------------------------------------------------------------------

class Run:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.output_dir)
        self.art = self.out / 'artifacts'
        self.inputs = {'pdf': Path(args.pdf), 'sidecar': Path(args.sidecar), 'font_a': Path(args.font_a)}
        if args.font_b:
            self.inputs['font_b'] = Path(args.font_b)
        self.result = dict(schema_version=SCHEMA_VERSION, executed_at=datetime.now(timezone.utc).isoformat(),
                           disclaimer=DISCLAIMER, stages=[], revisions={}, verdict=None)
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
        plan = deepcopy(DEFAULT_PLAN)
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
            ('confirm', self.confirm, ('baseline',)), ('edit', self.edit, ('confirm',)),
            ('style', self.style, ('edit',)), ('font', self.font, ('edit',)),
            ('noop', self.noop, ('edit',)), ('publish_a', self.publish_a, ('noop',)),
            ('edit_from_bundle_a', self.edit_b, ('publish_a',)), ('font_back', self.font_back, ('edit_from_bundle_a',)),
            ('publish_b', self.publish_b, ('edit_from_bundle_a',)), ('negatives', self.negatives, ('publish_b',)),
            ('continuity', self.continuity, ('publish_b',)),
            ('raster_mupdf', self.raster_mupdf, ('publish_b',)),
        ]
        try:
            for name, function, needs in lifecycle:
                self.stage(name, function, needs=needs)
            self.stage('raster_poppler', self.raster_poppler, required=False, needs=('publish_b',))
        finally:
            if hasattr(self, 'before'):
                self.stage('inputs_preserved', self.preserved)
            self.result['verdict'] = verdict(self.result['stages'])
            self.result['finished_at'] = datetime.now(timezone.utc).isoformat()
            self.save()
            (self.out / 'report.md').write_text(report(self.result), encoding='utf-8')
        return EXIT[self.result['verdict']]


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
    lines += ['', '## Artifacts', '', '- `result.json` — machine-readable result (schema in the README)',
              '- `artifacts/` — candidates (`candidate-*/semantic-candidate-*/`), `bundle-a/`, `bundle-b/`, `raster/`',
              '- `logs/harness.log` — stage log with tracebacks of refused/failed stages', '']
    return '\n'.join(lines)


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
    run.add_argument('--plan', help='JSON overriding edit/style/next_edit requests')
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
