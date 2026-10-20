"""Explicit two-owner editing of consecutive leaf Spans with joint ActualText.

This is not the single-Span contract widened, B1 page-flow or B2 same-owner
aggregation. The caller approves two complete, consecutive leaf Spans of one
/P, each with its own fixed rectangle, and optionally the whitespace leaf that
follows them. One logical edit and an explicit new owner boundary decide the
text of each owner. Characters may cross the boundary only forward, from the
first owner's tail to the second owner's head; they are re-shaped with the
SHA-pinned original embedded program and must keep their GID. The follower
keeps every semantic value and may only be translated along its line.
"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile

from .backend import PdfError
from .content_stream import ContentPage, number
from .editable import _publish
from .model import Rect
from .mutation import Mutation
from .paragraph import plan_paragraph_edit
from .replay import ensure_destination
from .selection import make_selection, source_sha
from .source_ownership import adjacent_span_witness, observe_adjacent_owners
from .span_edit import _font_binding, _observe, _provider, _seal
from .transaction import Plan, Transaction

SCHEMA = 'pdfengine-adjacent-span-actualtext-1'
PROVIDER = 'adjacent-span-provider'
ROLES = ('first', 'second')
# Visual appearance a relocated character must keep in its new owner.
APPEARANCE = ('font_size', 'horizontal_scale', 'baseline_shift', 'fill', 'observed_color')


def _rect(region, what):
    if (not isinstance(region, dict) or set(region) != {'x0', 'y0', 'x1', 'y1'}
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in region.values())):
        raise PdfError('adjacent Span ' + what + ' requires a finite explicit rectangle')
    rect = Rect(**region)
    if rect.width <= 0 or rect.height <= 0:
        raise PdfError('adjacent Span ' + what + ' rectangle is empty')
    return rect


def _clip_rect(clip, page_height):
    x, y, w, h = clip[0]['path'][0]['args']
    return Rect(min(x, x + w), page_height - max(y, y + h), max(x, x + w), page_height - min(y, y + h))


def _follower_region(content, joint, follower):
    """The approved follower rectangle grants no foreign glyph or paint capacity."""
    rect = _rect(follower.get('region'), 'follower')
    value = joint['follower']
    if not rect.contains(Rect(*value['bbox']), .001) or not Rect(*content.page.rect).contains(rect, .001):
        raise PdfError('adjacent Span follower rectangle must hold the follower inside the page')
    owned = set(value['glyph_ids']) | set(joint['second']['glyph_ids'])
    if any(i not in owned and rect.intersects(Rect(*g['bbox'])) for i, g in enumerate(content.actual)):
        raise PdfError('adjacent Span follower rectangle adds foreign glyph capacity')
    if any(rect.intersects(Rect(*d['rect'])) for d in content.page.get_drawings()):
        raise PdfError('adjacent Span follower rectangle adds foreign paint capacity')


def _observe_pair(source, owners, follower, bindings=(None, None)):
    """Each owner's single-leaf proof plus the joint adjacency (and follower) proof."""
    if not isinstance(owners, (list, tuple)) or len(owners) != 2:
        raise PdfError('adjacent Span needs exactly two owners')
    pages = {o['selection']['page'] for o in owners}
    if len(pages) != 1:
        raise PdfError('adjacent Span owners must be on one page')
    follower_ids = frozenset(follower['glyph_ids']) if follower is not None else frozenset()
    states = []
    for index, (owner, binding) in enumerate(zip(owners, bindings)):
        _rect(owner['region'], 'owner')
        proof, paragraph = _observe(source, owner['selection'], owner['region'], binding,
                                    follower=follower_ids if index == 1 else frozenset())
        state = dict(selection=owner['selection'], region=owner['region'], witness=proof, paragraph=paragraph)
        if binding is not None:
            state['font_binding'] = binding
        states.append(state)
    content = ContentPage(source, pages.pop())
    try:
        joint = adjacent_span_witness(content, states[0]['witness']['glyph_ids'], states[1]['witness']['glyph_ids'],
                                      sorted(follower_ids))
        if follower is not None:
            _follower_region(content, joint, follower)
    finally:
        content.close()
    return states, joint


def _split(first, second, edit, boundary):
    """Per-owner edits and the provenance of every resulting character.

    Exactly one internal edit lies inside one owner. ``boundary`` is the
    approved length of the first owner's new text; characters cross it only
    from the first owner's tail into the second owner's head.
    """
    if (not isinstance(edit, dict) or set(edit) != {'start', 'end', 'text'} or type(edit['start']) is not int
            or type(edit['end']) is not int or not isinstance(edit['text'], str)):
        raise PdfError('adjacent Span supports one explicit edit')
    logical, cut = first + second, len(first)
    start, end, text = edit['start'], edit['end'], edit['text']
    if not 0 < start <= end < len(logical) or (start == end and not text):
        raise PdfError('adjacent Span supports one internal edit only')
    if start < cut and end <= cut:
        owner = 0
    elif start > cut:
        owner = 1
    else:
        raise PdfError('adjacent Span edit must lie inside one owner, away from the owner boundary')
    result = logical[:start] + text + logical[end:]
    delta = len(text) - (end - start)
    if type(boundary) is not int or not 0 < boundary < len(result):
        raise PdfError('adjacent Span needs an explicit boundary leaving both owners nonempty')
    sources = [i if i < start else None if i < start + len(text) else i - delta for i in range(len(result))]
    if any(s is None and (i >= boundary) != bool(owner) for i, s in enumerate(sources)):
        raise PdfError('new text must stay inside its edited owner')
    if any(s is not None and s >= cut for s in sources[:boundary]):
        raise PdfError('characters cannot move into the first owner')
    relocated = [s for s in sources[boundary:] if s is not None and s < cut]
    count = len(relocated)
    if relocated != list(range(cut - count, cut)):
        raise PdfError('only the first owner\'s tail may move into the second owner')
    first_edits, second_edits = [], []
    if owner == 0 and count and end == cut - count:
        first_edits.append(dict(start=start, end=cut, text=text))
    else:
        if owner == 0:
            first_edits.append(dict(start=start, end=end, text=text))
        if count:
            first_edits.append(dict(start=cut - count, end=cut, text=''))
    if count:
        second_edits.append(dict(start=0, end=0, text=first[cut - count:]))
    if owner == 1:
        second_edits.append(dict(start=start - cut, end=end - cut, text=text))
    kinds = []
    for i, s in enumerate(sources):
        role = ROLES[i >= boundary]
        if s is None:
            kinds.append(dict(owner=role, kind='inserted', source=None))
        else:
            source_role = ROLES[s >= cut]
            kinds.append(dict(owner=role, kind='retained' if source_role == role else 'relocated',
                              source=dict(owner=source_role, offset=s - (cut if s >= cut else 0))))
    return dict(owner=ROLES[owner], edits=[first_edits, second_edits], after=[result[:boundary], result[boundary:]],
                relocated=first[cut - count:], inserted=text, provenance=kinds, logical_after=result)


def _style_at(paragraph, offset):
    return next(s['style_id'] for s in paragraph['spans'] if s['start'] <= offset < s['end'])


def _appearance_of(paragraph, offset):
    ident = _style_at(paragraph, offset)
    style = next(s for s in paragraph['styles'] if s['id'] == ident)
    return {k: style[k] for k in APPEARANCE}, style['font_xref']


def _plan_owner(transaction, model, index, edits, font):
    state = model['owners'][index]
    page = transaction.page(model['page'])
    paragraph = state['paragraph']; region = Rect(**state['region']); proof = state['witness']
    requested = []
    for edit in edits:
        value = dict(edit, style_id=_style_at(paragraph, edit['start']))
        if edit['text']:
            value['font_id'] = PROVIDER
        requested.append(value)
    plan = plan_paragraph_edit(page, paragraph, requested, owner='adjacent-span:' + str(proof['owner']['xref']),
        fonts={PROVIDER: font} if font is not None else None,
        width=region.x1 - paragraph['layout_suggestion']['x'], max_bottom=region.y1)
    if plan._report['new_line_count'] != 1:
        raise PdfError('adjacent Span owner cannot wrap')
    if any(box.x0 < region.x0 or box.y0 < region.y0 or box.x1 > region.x1 or box.y1 > region.y1
           for box in plan.final_rects):
        raise PdfError('adjacent Span edited ink exceeds the approved rectangle')
    starts = {e.operator.start: e.operator.end for e in page.content.selected_events(set(proof['glyph_ids']))}
    if any(starts.get(m.start) != m.end for m in plan.mutations):
        raise PdfError('adjacent Span mutation escapes its selected text operators')
    plan.adjacent_span_witness = model['witness']
    plan.adjacent_span_role = ROLES[index]
    return plan


class FollowerPlan(Plan):
    """Translate one approved whitespace leaf by rewriting only its Tm operands."""
    kind = 'adjacent-span-follower'

    def __init__(self, page, model, dx):
        super().__init__('adjacent-span-follower:' + str(model['witness']['follower']['owner']['xref']))
        value = model['witness']['follower']
        self.region = Rect(**model['follower']['region'])
        self.clip = _clip_rect(value['clip'], page.content.page.rect.height)
        start, end = value['tm_range']
        tokens = page.content.streams[-page.content.page.xref][start:end].split()
        tokens[4] = number(value['tm'][4] + dx)
        self.mutations = [Mutation(start, end, b' '.join(tokens), kind='follower-move', owner=self.owner)]
        before = Rect(*value['bbox'])
        after = Rect(before.x0 + dx, before.y0, before.x1 + dx, before.y1)
        self.moved = {i: (dx, 0) for i in value['glyph_ids']}
        self.final_rects = [after]
        self.affected = before.union(after)
        self.adjacent_span_witness = model['witness']
        self.adjacent_span_role = 'follower'
        self._report = dict(owner=value['owner'], mcid=value['mcid'], dx=dx,
                            before=list(before.tuple()), after=list(after.tuple()))

    def check(self, page, *, exclude_glyphs, paint_changes):
        box = self.final_rects[0]
        if not self.region.contains(box, .001) or not self.clip.contains(box, .001):
            raise PdfError('adjacent Span follower leaves its approved rectangle or clip')
        if not Rect(*page.content.page.rect).contains(box, .001):
            raise PdfError('adjacent Span follower leaves the page')
        for i, glyph in enumerate(page.content.actual):
            if i not in self.moved and i not in exclude_glyphs and box.intersects(Rect(*glyph['bbox'])):
                raise PdfError('adjacent Span follower collides with a foreign glyph')
        if any(box.intersects(Rect(*d['rect'])) for d in page.content.page.get_drawings()):
            raise PdfError('adjacent Span follower collides with foreign paint')

    def report(self, result):
        return dict(self._report)


def propose_adjacent_span(source, first, second, *, follower=None):
    """Read-only proposal; every rectangle and the follower are caller authority.

    ``first``/``second`` are dict(selection, region); ``follower`` is
    dict(glyph_ids, region) for the following whitespace leaf, or None.
    """
    owners = [dict(selection=o['selection'], region=o['region']) for o in (first, second)]
    if follower is not None:
        follower = dict(glyph_ids=sorted(follower['glyph_ids']), region=follower['region'])
    states, joint = _observe_pair(source, owners, follower)
    value = dict(schema=SCHEMA, status='proposed', pdf_sha256=source_sha(source), page=owners[0]['selection']['page'],
                 owners=states, follower=follower, witness=joint)
    with Transaction(source) as transaction:
        plans = [_plan_owner(transaction, value, i, [], None) for i in (0, 1)]
        page = transaction.page(value['page'])
        page.check()
        for plan, state in zip(plans, states):
            original = [page.content.actual[i] for i in state['witness']['glyph_ids']]
            if len(plan.new_glyphs) != len(original) or any(
                    a['unicode'] != b['unicode'] or a['glyph_id'] != b['gid']
                    or max(abs(x - y) for x, y in zip(a['origin'], b['origin'])) > .002
                    for a, b in zip(plan.new_glyphs, original)):
                raise PdfError('adjacent Span no-edit layout does not reproduce the source')
        import pymupdf
        from .composition import _pixels_equal
        from .pdf_save import program_pdf_bytes
        encoded = program_pdf_bytes(source, value['page'] - 1, page.program.apply())
        with pymupdf.open(stream=encoded, filetype='pdf') as rendered:
            if not all(_pixels_equal(transaction.document[i], rendered[i]) for i in range(len(rendered))):
                raise PdfError('adjacent Span no-edit writer pixels differ from the source')
    value['reproduction'] = 'source-replay-and-no-edit-writer-pixels-proved'
    return _seal(value)


def accept_adjacent_span(source, proposal):
    current = propose_adjacent_span(source, *[dict(selection=o['selection'], region=o['region'])
                                              for o in proposal['owners']], follower=proposal['follower'])
    if current != proposal:
        raise PdfError('adjacent Span proposal is stale or modified')
    current['status'] = 'confirmed'
    return _seal(current)


def _restore(source, model):
    if (model.get('schema') != SCHEMA or model.get('status') != 'confirmed' or _seal(model) != model
            or source_sha(source) != model.get('pdf_sha256')):
        raise PdfError('adjacent Span state needs current-revision confirmation')
    owners = [dict(selection=o['selection'], region=o['region']) for o in model['owners']]
    states, joint = _observe_pair(source, owners, model['follower'],
                                  tuple(o.get('font_binding') for o in model['owners']))
    if (any(s['witness'] != o['witness'] or s['paragraph'] != o['paragraph'] for s, o in zip(states, model['owners']))
            or joint != model['witness']):
        raise PdfError('adjacent Span current ownership or structure differs')
    return deepcopy(model)


def open_adjacent_span(source, model):
    try:
        return dict(status='restored', state=_restore(source, model))
    except (PdfError, KeyError, TypeError, ValueError) as exc:
        return dict(status='needs_confirmation', reason=str(exc))


def _qualify(source, state, split, recipe, stack):
    """The pinned provider and every relocated glyph's appearance and identity."""
    if split is None:
        if recipe is not None:
            raise PdfError('adjacent Span no-op does not take a font provider')
        return None
    first, second = (o['paragraph'] for o in state['owners'])
    count = len(split['relocated'])
    if count:
        cut = len(first['text'])
        destination = _appearance_of(second, 0)
        if any(_appearance_of(first, i) != destination for i in range(cut - count, cut)):
            raise PdfError('relocated characters would change appearance or font program in the second owner')
    receiving = {split['owner']} if split['inserted'] else set()
    if count:
        receiving.add('second')
    needed = split['inserted'] + split['relocated']
    if not needed:
        if recipe is not None:
            raise PdfError('adjacent Span deletion does not take a font provider')
        return None
    programs = set()
    for role in receiving:
        owner = state['owners'][ROLES.index(role)]
        programs.add((owner.get('font_binding') or _font_binding(source, owner['paragraph']))['source_program_sha256'])
    if len(programs) != 1:
        raise PdfError('receiving owners do not share one original embedded program')
    owner = state['owners'][ROLES.index(sorted(receiving)[0])]
    return stack.enter_context(_provider(source, owner, [dict(text=needed)], recipe))


def _follower_shift(source, state, plan):
    """Translation that keeps the follower's exact pen relation to the second owner."""
    with Transaction(source) as probe:
        unchanged = _plan_owner(probe, state, 1, [], None)._report['lines'][0]
    edited = plan._report['lines'][0]
    if abs(edited['x'] - unchanged['x']) > 1e-9:
        raise PdfError('adjacent Span second owner moved its line start')
    return edited['width'] - unchanged['width']


def _provenance(content, state, split, plans):
    """Audit records: retained, relocated and inserted glyphs are distinct."""
    glyph_plans = [plan._report['glyph_plan'] for plan in plans]
    records, cursor = [], [0, 0]
    first_ids = state['owners'][0]['witness']['glyph_ids']
    second_ids = state['owners'][1]['witness']['glyph_ids']
    for kind in split['provenance']:
        index = ROLES.index(kind['owner'])
        glyph = glyph_plans[index][cursor[index]]; cursor[index] += 1
        value = dict(kind, unicode=glyph['unicode'], gid=glyph['glyph_id'], provider=glyph['provider'],
                     font_resource=glyph['font_resource'], code=glyph['code'], origin=glyph['origin'])
        if kind['source'] is not None:
            ids = first_ids if kind['source']['owner'] == 'first' else second_ids
            actual = content.actual[ids[kind['source']['offset']]]
            value['source_glyph'] = dict(gid=actual['gid'], unicode=actual['unicode'], origin=list(actual['origin']))
            if actual['unicode'] != glyph['unicode'] or actual['gid'] != glyph['glyph_id']:
                raise PdfError('relocated or retained glyph identity differs from its source glyph')
        if kind['kind'] == 'retained' and glyph['provider'] != 'original':
            raise PdfError('retained glyph lost its original code')
        if kind['kind'] != 'retained' and glyph['provider'] != PROVIDER:
            raise PdfError('relocated or inserted glyph was not shaped by the pinned provider')
        records.append(value)
    if cursor != [len(g) for g in glyph_plans]:
        raise PdfError('adjacent Span glyph plan differs from the approved text')
    return records


def _plan_all(transaction, state, split, font, source):
    plans = [_plan_owner(transaction, state, i, split['edits'][i], font) for i in (0, 1)]
    page = transaction.page(state['page'])
    for plan, wanted in zip(plans, split['after']):
        if plan._report['after'] != wanted:
            raise PdfError('adjacent Span owner plan differs from the approved split')
    follower = None
    if state['follower'] is not None:
        follower = page.add(FollowerPlan(page, state, _follower_shift(source, state, plans[1])))
    page.check()
    return plans, follower, _provenance(page.content, state, split, plans)


def plan_adjacent_span_edit(source, model, edit, *, boundary, provider=None):
    """Read-only split, shaping, capacity, follower and provenance proof."""
    state = _restore(source, model)
    split = _split(*(o['witness']['text'] for o in state['owners']), edit, boundary)
    with ExitStack() as stack:
        font = _qualify(source, state, split, provider, stack)
        with Transaction(source) as transaction:
            plans, follower, provenance = _plan_all(transaction, state, split, font, source)
            return dict(owners=[deepcopy(p._report) for p in plans], split=split, provenance=provenance,
                        follower=follower.report(None) if follower is not None else None)


def edit_adjacent_span(source, model, output, model_output, edit=None, *, boundary=None, provider=None):
    """One joint edit (or an unchanged-program no-op) saved with both ActualTexts.

    PDF and sidecar are published together only after the serialized PDF
    re-proves both owners, their parent relation and the follower.
    """
    state = _restore(source, model)
    texts = [o['witness']['text'] for o in state['owners']]
    split = _split(*texts, edit, boundary) if edit is not None or boundary is not None else None
    output, model_output = ensure_destination(output, source), ensure_destination(model_output, source)
    if output == model_output:
        raise PdfError('adjacent Span needs distinct PDF and sidecar destinations')
    output.parent.mkdir(parents=True, exist_ok=True)
    provenance, shift = None, None
    with tempfile.TemporaryDirectory(prefix='.adjacent-span-', dir=output.parent) as directory:
        root = Path(directory); target = root / 'span.pdf'
        with ExitStack() as stack:
            font = _qualify(source, state, split, provider, stack)
            with Transaction(source) as transaction:
                if split is not None:
                    plans, follower, provenance = _plan_all(transaction, state, split, font, source)
                    shift = follower._report['dx'] if follower is not None else None
                    for owner, plan in zip(state['owners'], plans):
                        if plan.font_records:
                            binding = owner.setdefault('font_binding', _font_binding(source, owner['paragraph']))
                            binding['generated'].update(deepcopy(plan.font_records))
                else:
                    transaction.page(state['page']).add(Plan('adjacent-span-noop'))
                result = transaction.commit(target)
                result.close()
        wanted = split['after'] if split is not None else texts
        refs = [o['witness']['owner'] for o in state['owners']]
        old_follower = state['witness']['follower']
        ids = observe_adjacent_owners(target, state['page'], refs, old_follower['owner'] if old_follower else None)
        owners = [dict(selection=make_selection(target, state['page'], glyph_ids=i,
                                                explicit_width=o['selection']['explicitly_supplied_width']),
                       region=o['region']) for i, o in zip(ids, state['owners'])]
        follower = dict(state['follower'], glyph_ids=sorted(ids[2])) if old_follower else None
        states, joint = _observe_pair(target, owners, follower, tuple(o.get('font_binding') for o in state['owners']))
        old = state['witness']
        if ([s['witness']['text'] for s in states] != wanted
                or any(joint[r][k] != old[r][k] for r in ROLES for k in ('owner', 'mcid', 'clip'))
                or any(joint[k] != old[k] for k in ('parent', 'children', 'structure'))):
            raise PdfError('adjacent Span saved semantics/structure/clip differ from the plan')
        if old_follower is not None:
            new = joint['follower']; dx = shift or 0
            if ({k: new[k] for k in ('owner', 'mcid', 'tag', 'properties', 'text', 'actual_text', 'clip')}
                    != {k: old_follower[k] for k in ('owner', 'mcid', 'tag', 'properties', 'text', 'actual_text', 'clip')}
                    or max(abs(a - b) for a, b in zip(new['bbox'], (old_follower['bbox'][0] + dx, old_follower['bbox'][1],
                                                                    old_follower['bbox'][2] + dx, old_follower['bbox'][3]))) > .002):
                raise PdfError('adjacent Span saved follower differs from its planned translation')
        for s, o in zip(states, state['owners']):
            if 'font_binding' in o:
                s['font_binding'] = o['font_binding']
        state.update(pdf_sha256=source_sha(target), owners=states, witness=joint, follower=follower)
        state = _seal(state)
        _restore(target, state)
        if source_sha(source) != model['pdf_sha256']:
            raise PdfError('adjacent Span source changed during save')
        sidecar = root / 'span.json'
        sidecar.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        _publish(root, [(target, output), (sidecar, model_output)])
    return dict(status='saved', state=state, saves=1, after=wanted, provenance=provenance, follower_dx=shift)
