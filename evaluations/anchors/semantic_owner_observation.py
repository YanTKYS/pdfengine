"""B1-L-O evidence: stored single-slot shared-flow owner + semantic payload lifecycle.

Uses the unmodified runtime (confirm_shared_flow, edit_shared_flow,
open_shared_flow, source_ownership, _publish). Every lifecycle starts from a
shared-flow sidecar that was saved to disk and reloaded. The current runtime
`_source_body` writer stands in for the future L2 canonical island writer: this
evidence is about owner/semantic co-binding and two-artifact publication, not
canonical placement (covered by the §18/§19 fixtures). No runtime is changed.
"""
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.content_stream import ContentPage
from pdfeditor.editable import _publish
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow
from pdfeditor.story_flow import confirm_story
from pdfeditor import source_ownership as owned
from evaluations.anchors.measurement_observation import synthetic_font
from evaluations.anchors.semantic_binding import font_policy, require, sha
from evaluations.anchors import authorized_publication as p4
from evaluations.anchors import semantic_owner_binding as model
from evaluations.continuation.source_slot_accumulation import runtime_digest

ROOT = Path(__file__).resolve().parents[2]
BASE = '1f5a7f0d70d4365eb0d6fac7cbebd647a3dd6ace'
TARGET = {'document': 'caller-document', 'paragraph': 'A'}
NAMES = ('document.pdf', 'shared-flow.json')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def flow(root, flow_id='semantic-flow', *, text=b'XY'):
    """Single paragraph, single source slot, one region, one body style, no continuation."""
    root.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_attributed import source_pdf as fixture_pdf
    source = fixture_pdf(root, b'BT /Regular 12 Tf 20 200 Td (' + text + b') Tj ET')
    asset = root / 'asset.ttf'; asset.write_bytes(synthetic_font())
    p = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(len(text))), explicit_width=150))
    story = confirm_story(source, {'part': dict(page=1, bounds=[18, 40, 200, 220], paragraph=p, paint_relations=[],
        layout=dict(x=20, baseline=200, width=150, max_bottom=220, min_line_height=22, first_line_indent=0))},
        paragraph_id='A', chain=['part'], protected_regions={},
        styles={'body': dict(provider=dict(path=str(asset)), provider_relation='substituted')},
        style_assignments={'part': {s['id']: 'body' for s in p['styles']}}, typing_style_id='body')
    state = confirm_shared_flow(source, {'A': story}, flow_id=flow_id, paragraph_order=['A'],
        regions={'R': dict(page=1, bounds=[18, 40, 200, 220], x=20, width=150, first_baseline=200)},
        region_order=['R'], slot_regions={'A': {'part': 'R'}},
        paragraph_policies={'A': dict(min_line_height=22, first_line_indent=0, keep_together=False,
            break_before='auto', break_after='auto', empty=dict(kind='reserve-line', ascent=10, descent=3))},
        follows=[], protected_regions={})
    dump(root / 'confirmed.json', state)
    # The first ordinary v2 save creates the owned source island (runtime contract).
    edit_shared_flow(source, state, root / 'owned.pdf', root / 'owned.json',
                     {'A': dict(edits=[dict(start=0, end=len(text), text='A B', style_id='body')])})
    return source, root / 'owned.pdf', root / 'owned.json', asset


def payload(text='A B', asset_bytes=None):
    return dict(text=text, style=dict(font_size='12', horizontal_scale='1', rise='0', tracking='0', word_spacing='0',
                spacing_intent='confirmed-inline'), font=dict(sha=sha(asset_bytes), policy=font_policy()),
                body_style_id='body', edges=[], region_id='R')


def refused(call):
    try:
        call()
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return dict(refused=True, reason=str(exc)[:160])
    return dict(refused=False, reason='unexpected acceptance')


def p4_verified(pdf, state, asset_bytes):
    """Express the stored v3 slot as the §19 P4 model input; physical = full design opener."""
    sid = next(iter(state['slots']))
    semantic = model.model_semantic(state, sid)
    derived, _ = model.derive(semantic, asset_bytes)
    record = p4.seal(dict(version=1, target=TARGET, semantic=semantic, derived=derived, pdf_sha=sha(Path(pdf).read_bytes()),
                          owner=dict(domain='source-output', id=state['slots'][sid]['source_output']['marker_id'])))
    return p4.verify_current(Path(pdf).read_bytes(), record, asset_bytes, TARGET,
                             lambda *_: model.open_semantic_flow(pdf, state, asset_bytes))


def candidate(work, pdf, state, request, asset_bytes, *, tamper=None):
    """S0–S7: verify old → authorize → runtime owned-body rewrite → rebind → bind → reseal → validate."""
    old = model.open_semantic_flow(pdf, state, asset_bytes)
    verified = p4_verified(pdf, state, asset_bytes)
    authorization = p4.authorize_transition(verified, request)
    expected = p4.unpack(authorization.expected_semantic)
    sid = old['slot_id']
    require(expected['region'] == model.region_values(state, state['slots'][sid]['region_id'], 'A'),
            'REGION_CHANGE_REQUIRES_CONFIRMED_REGION')
    build = work / 'build'; build.mkdir(parents=True, exist_ok=False)
    edit_shared_flow(pdf, model.project_v2(state), build / 'document.pdf', build / 'candidate-v2.json',
                     model.to_shared_flow_changes(state, request))
    reopened = open_shared_flow(build / 'document.pdf', build / 'candidate-v2.json')
    require(reopened['status'] == 'restored', 'CANDIDATE_RUNTIME_VALIDATION')
    new_payload = {k: expected[k] for k in ('text', 'style', 'font', 'body_style_id', 'edges')}
    new_payload['region_id'] = state['slots'][sid]['semantic']['payload']['region_id']
    if tamper:
        tamper(new_payload)
    built = model.attach(reopened['state'], sid, new_payload, asset_bytes)
    diff = p4.check_diff(authorization, model.model_semantic(built, sid))
    checked = model.open_semantic_flow(build / 'document.pdf', built, asset_bytes)
    staging = work / 'staging'; staging.mkdir()
    shutil.copyfile(build / 'document.pdf', staging / NAMES[0]); dump(staging / NAMES[1], built)
    return dict(pdf=staging / NAMES[0], sidecar=staging / NAMES[1], state=built, diff=diff, old=old, new=checked,
                classification=authorization.classification)


def publish_bundle(staging, parent, name, *, order='pdf-first', fail=None):
    """Restricted contract: new non-existing bundle dir, same parent, one rename; no latest pointer."""
    private = parent / ('.pending-' + name); private.mkdir(parents=False, exist_ok=False)
    public = parent / name
    pairs = [(staging / n, private / n) for n in NAMES]
    if order == 'record-first':
        pairs.reverse()
    calls, seen, real_link, original_unlink = 0, [], os.link, Path.unlink
    def link(a, b):
        nonlocal calls
        calls += 1; seen.append(public.exists())
        if calls == 2 and fail in ('second-link', 'rollback-unlink'):
            raise OSError('second-link')
        return real_link(a, b)
    def unlink(path, *args, **kwargs):
        if fail == 'rollback-unlink' and path == pairs[0][1]:
            raise OSError('rollback-unlink')
        return original_unlink(path, *args, **kwargs)
    error = None
    try:
        with patch('pdfeditor.editable.os.link', link), patch.object(Path, 'unlink', unlink):
            _publish(staging, pairs)
        model.open_semantic_flow(private / NAMES[0], load(private / NAMES[1]), ASSET)
        if fail == 'before-rename':
            raise OSError('before-rename')
        require(private.resolve().parent == public.resolve().parent and not public.exists(), 'PUBLICATION_PATH_SCOPE')
        os.rename(private, public)
        if fail == 'after-rename':
            raise OSError('after-rename')
    except OSError as exc:
        error = str(exc)
    files = [(public / n).exists() for n in NAMES]
    return dict(error=error, public_files=files, formal_current='new' if all(files) else 'old',
                half_public=any(files) and not all(files), visible_before_commit=any(seen),
                complete_new_pair=all(files) and (public / NAMES[0]).read_bytes() == (staging / NAMES[0]).read_bytes()
                and (public / NAMES[1]).read_bytes() == (staging / NAMES[1]).read_bytes(),
                private_leftover=private.exists() and any(private.iterdir()), public=str(public.name),
                entries_after=sorted(p.name for p in parent.iterdir() if not p.name.startswith('.')))


def publication_gate(rows):
    for order in ('pdf-first', 'record-first'):
        for failure in ('none', 'second-link', 'rollback-unlink', 'before-rename', 'after-rename'):
            row = rows.get(order + ':' + failure, {})
            committed = failure in ('none', 'after-rename')
            if not (row.get('half_public') is False and row.get('visible_before_commit') is False and
                    row.get('formal_current') == ('new' if committed else 'old') and
                    row.get('public_files') == [committed, committed] and row.get('complete_new_pair') is committed
                    and 'error' in row and (row['error'] is None if failure == 'none' else bool(row['error']))
                    and row.get('old_intact') is True):
                return False
    return True


def rewrite_program(src, dst, transform):
    writer = PdfWriter(clone_from=PdfReader(src))
    stream = writer.pages[0]['/Contents'].get_object()
    stream.set_data(transform(stream.get_data()))
    with open(dst, 'wb') as handle:
        writer.write(handle)
    return dst


def physical_variant(work, name, pdf, state, transform, *, keep_context=False):
    """Mutate the PDF, then refresh every hash so only the physical defect remains."""
    path = rewrite_program(pdf, work / (name + '.pdf'), transform)
    changed = deepcopy(state); changed.pop('model_sha256')
    sid = next(iter(changed['slots'])); record = changed['slots'][sid]['source_output']
    changed['pdf_sha256'] = model.file_sha(path)
    content = ContentPage(path, 1)
    try:
        try:
            span = owned.inventory(content.streams[-content.page.xref])[record['marker_id']]
            current = owned.witness(content, record, span)
            if keep_context:
                current['entry_context_sha256'] = record['current']['entry_context_sha256']
            record['current'] = current
        except Exception:
            pass
    finally:
        content.close()
    binding = changed['slots'][sid]['semantic']['binding']
    binding.update(pdf_sha256=changed['pdf_sha256'], owner_program_sha256=record['current']['program_sha256'],
                   owner_block_sha256=record['current']['block_sha256'])
    return path, model.seal(changed)


def owner_refusal(pdf, state, expected):
    """Isolate the runtime owner contract (source_ownership.validate) from editable PDF-hash checks."""
    result = refused(lambda: owned.validate(pdf, state))
    result['expected'] = expected
    result['isolated'] = result['refused'] and expected in result['reason']
    return result


def resealed(state, edit):
    value = deepcopy(state); value.pop('model_sha256'); edit(value); return model.seal(value)


def reopen_process(folder):
    out = subprocess.run([sys.executable, '-m', 'evaluations.anchors.semantic_owner_observation', '--reopen', str(folder)],
                         cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(out.stdout)


def compact(opened):
    e = opened['evidence']
    return dict(slot_id=opened['slot_id'], owner_current=opened['owner']['current'], marker_id=opened['owner']['marker_id'],
                emitted=e['emitted'], entry_exit_proven=e['entry_exit_proven'], body_span=e['body_span'],
                show_spans=e['show_spans'], plan_sha=sha(json.dumps(e['plan'], sort_keys=True).encode()))


ASSET = None


def evaluate(work):
    global ASSET
    work.mkdir(parents=True, exist_ok=False); before = runtime_digest()
    source, owned_pdf, owned_json, asset = flow(work / 'fixture')
    ASSET = asset.read_bytes()
    # Stored-record start: reload the saved v2 owner sidecar from disk.
    stored_v2 = load(owned_json)
    confirmed = model.initial_confirmation(owned_pdf, stored_v2, dict(operation='confirm-semantic-layout',
        slot_id='slot-0', payload=payload(asset_bytes=ASSET)), ASSET)
    store = work / 'store'; store.mkdir()
    shutil.copyfile(owned_pdf, store / NAMES[0]); dump(store / NAMES[1], confirmed)
    positives = {}
    state = load(store / NAMES[1]); pdf = store / NAMES[0]
    first = model.open_semantic_flow(pdf, state, ASSET)
    positives['stored_shared_flow_reopen'] = first['slot_id'] == 'slot-0' and state['schema'] == model.SCHEMA
    positives['stored_owner_current_witness'] = first['evidence']['entry_exit_proven'] and first['owner']['state'] == 'owned'
    positives['stored_semantic_payload'] = first['evidence']['emitted'] == 'A B'
    positives['noop_reopen'] = compact(model.open_semantic_flow(pdf, load(store / NAMES[1]), ASSET)) == compact(first)
    edit = candidate(work / 'edit', pdf, state, dict(operation='edit', start=1, end=1, text='A'), ASSET)
    old_owner = state['slots']['slot-0']['source_output']; new_owner = edit['state']['slots']['slot-0']['source_output']
    positives['text_edit_authorized'] = edit['classification'] == 'D' and edit['diff'] == {'text': {'before': 'A B', 'after': 'AA B'}}
    positives['candidate_pdf_generated'] = edit['pdf'].exists() and model.file_sha(edit['pdf']) != model.file_sha(pdf)
    positives['owner_rebind'] = (new_owner['marker_id'] == old_owner['marker_id'] and new_owner['created_from'] == old_owner['created_from']
        and new_owner['current'] != old_owner['current']
        and new_owner['current']['entry_context_sha256'] == old_owner['current']['entry_context_sha256'])
    positives['semantic_payload_update'] = edit['state']['slots']['slot-0']['semantic']['payload']['text'] == 'AA B'
    positives['combined_sidecar_reseal'] = edit['state']['schema'] == model.SCHEMA and set(load(edit['sidecar'])) == set(state)
    positives['fresh_candidate_validate'] = compact(model.open_semantic_flow(edit['pdf'], load(edit['sidecar']), ASSET))['emitted'] == 'AA B'
    bundles = work / 'bundles'; bundles.mkdir()
    published = publish_bundle(edit['pdf'].parent, bundles, 'edit-1')
    positives['bundle_publication_candidate'] = published['complete_new_pair'] and published['entries_after'] == ['edit-1']
    reopen = [reopen_process(bundles / 'edit-1') for _ in range(3)]
    positives['fresh_published_reopen'] = reopen[0]['emitted'] == 'AA B' and reopen[0]['owner_current'] == new_owner['current']
    positives['repeated_noop_reopen'] = reopen[0] == reopen[1] == reopen[2]
    noop = candidate(work / 'noop-save', bundles / 'edit-1' / NAMES[0], load(bundles / 'edit-1' / NAMES[1]),
                     dict(operation='save'), ASSET)
    positives['noop_save_semantic_identical'] = (noop['diff'] == {} and noop['state']['slots']['slot-0']['semantic']['payload']
                                                 == edit['state']['slots']['slot-0']['semantic']['payload'])
    # Negatives.
    negatives = {}
    cand_pdf, cand = edit['pdf'], edit['state']
    def set_owner(current, *, binding_too=False):
        def change(value):
            slot = value['slots']['slot-0']; slot['source_output']['current'] = deepcopy(current)
            if binding_too:
                slot['semantic']['binding'].update(owner_program_sha256=current['program_sha256'],
                                                   owner_block_sha256=current['block_sha256'])
        return change
    negatives['old_owner_witness_new_pdf'] = refused(lambda: model.open_semantic_flow(cand_pdf,
        resealed(state, lambda v: v.update(pdf_sha256=cand['pdf_sha256'])), ASSET))
    negatives['new_semantic_old_owner_witness'] = refused(lambda: model.open_semantic_flow(cand_pdf,
        resealed(cand, set_owner(old_owner['current'])), ASSET))
    negatives['new_semantic_old_owner_consistent_binding'] = refused(lambda: model.open_semantic_flow(cand_pdf,
        resealed(cand, set_owner(old_owner['current'], binding_too=True)), ASSET))
    negatives['new_owner_old_semantic'] = refused(lambda: model.open_semantic_flow(cand_pdf,
        resealed(cand, lambda v: v['slots']['slot-0'].update(semantic=deepcopy(state['slots']['slot-0']['semantic']))), ASSET))
    other_src, other_pdf, other_json, _ = flow(work / 'other', flow_id='other-flow')
    other_owner = load(other_json)['slots']['slot-0']['source_output']
    negatives['copied_owner_record'] = refused(lambda: model.open_semantic_flow(pdf,
        resealed(state, lambda v: v['slots']['slot-0'].update(source_output=deepcopy(other_owner))), ASSET))
    negatives['wrong_slot_id'] = refused(lambda: model.open_semantic_flow(pdf,
        resealed(state, lambda v: v['slots']['slot-0']['semantic']['binding'].update(slot_id='slot-9')), ASSET))
    negatives['wrong_paragraph_id'] = refused(lambda: model.open_semantic_flow(pdf,
        resealed(state, lambda v: v['slots']['slot-0']['semantic']['binding'].update(paragraph_id='B')), ASSET))
    def wrong_region(v):
        v['slots']['slot-0']['semantic']['payload']['region_id'] = 'R2'
        v['slots']['slot-0']['semantic']['binding']['region_id'] = 'R2'
    negatives['wrong_region_id'] = refused(lambda: model.open_semantic_flow(pdf, resealed(state, wrong_region), ASSET))
    negatives['second_slot_present'] = refused(lambda: model.open_semantic_flow(pdf,
        resealed(state, lambda v: v['slots'].update({'slot-1': deepcopy(v['slots']['slot-0'])})), ASSET))
    negatives['continuation_present'] = refused(lambda: model.open_semantic_flow(pdf,
        resealed(state, lambda v: v.update(continuation_destinations={'d': {'page': 1}})), ASSET))
    negatives['generated_continuation_slot'] = refused(lambda: model.open_semantic_flow(pdf,
        resealed(state, lambda v: v['slots']['slot-0'].update(destination_id='d')), ASSET))
    negatives['unowned_source_slot'] = refused(lambda: model.initial_confirmation(source,
        load(work / 'fixture' / 'confirmed.json'), dict(operation='confirm-semantic-layout', slot_id='slot-0',
        payload=payload(asset_bytes=ASSET)), ASSET))
    marker = owned.markers(state['slots']['slot-0']['source_output'])
    spoof_pdf, spoof = physical_variant(work, 'marker-spoof', pdf, state, lambda d: b'q Q' + marker[0] + b'q Q\n' + marker[1] + d)
    negatives['marker_spoof'] = refused(lambda: model.open_semantic_flow(spoof_pdf, spoof, ASSET))
    isolated = {'marker_spoof': owner_refusal(spoof_pdf, spoof, 'duplicate source output marker')}
    stale_pdf, stale = physical_variant(work, 'stale-context', pdf, state, lambda d: b'0.5 g\n' + d, keep_context=True)
    negatives['stale_context'] = refused(lambda: model.open_semantic_flow(stale_pdf, stale, ASSET))
    isolated['stale_context'] = owner_refusal(stale_pdf, stale, 'witness mismatch')
    foreign_pdf, foreign = physical_variant(work, 'foreign-glyph', pdf, state,
        lambda d: d.replace(b'<0001> Tj', b'<0001> Tj 1 0 0 1 60 60 Tm <0001> Tj', 1))
    negatives['foreign_glyph_in_island'] = refused(lambda: model.open_semantic_flow(foreign_pdf, foreign, ASSET))
    isolated['foreign_glyph_in_island'] = owner_refusal(foreign_pdf, foreign, 'foreign or unproven glyph')
    isolated['old_owner_witness_new_pdf'] = owner_refusal(cand_pdf,
        resealed(state, lambda v: v.update(pdf_sha256=cand['pdf_sha256'])), 'witness mismatch')
    isolated['new_semantic_old_owner_witness'] = owner_refusal(cand_pdf,
        resealed(cand, set_owner(old_owner['current'])), 'witness mismatch')
    isolated['copied_owner_record'] = owner_refusal(pdf,
        resealed(state, lambda v: v['slots']['slot-0'].update(source_output=deepcopy(other_owner))), 'creation identity mismatch')
    def swap_text(v):
        s = v['slots']['slot-0']['semantic']; s['payload']['text'] = 'B A'
    negatives['semantic_only_reseal'] = refused(lambda: model.open_semantic_flow(pdf, resealed(state, swap_text), ASSET))
    negatives['unauthorized_candidate_semantic'] = refused(lambda: candidate(work / 'tamper', pdf, state,
        dict(operation='edit', start=1, end=1, text='A'), ASSET, tamper=lambda p: p.update(body_style_id='other')))
    negatives['v2_open_of_semantic_sidecar'] = dict(refused=open_shared_flow(pdf, state)['status'] != 'restored',
                                                    reason='runtime v2 opener does not accept the semantic schema')
    v2_view = open_shared_flow(owned_pdf, stored_v2)
    negatives['v2_reopen_never_auto_upgrades'] = dict(refused=v2_view['status'] == 'restored' and
        v2_view['state']['schema'] != model.SCHEMA and 'semantic' not in v2_view['state']['slots']['slot-0'],
        reason='opening v2 creates no semantic payload')
    negatives['implicit_confirmation'] = refused(lambda: model.initial_confirmation(owned_pdf, stored_v2,
        dict(operation='confirm-semantic-layout', slot_id='slot-0'), ASSET))
    # Publication failure matrix with the real candidate pair.
    rows = {}
    for order in ('pdf-first', 'record-first'):
        for failure in ('none', 'second-link', 'rollback-unlink', 'before-rename', 'after-rename'):
            parent = work / 'matrix' / (order + '-' + failure); parent.mkdir(parents=True)
            old = parent / 'current'; old.mkdir()
            for n in NAMES: shutil.copyfile(store / n, old / n)
            before_old = [(old / n).read_bytes() for n in NAMES]
            row = publish_bundle(edit['pdf'].parent, parent, 'next', order=order, fail=failure)
            row['old_intact'] = [(old / n).read_bytes() for n in NAMES] == before_old
            row['old_still_opens'] = model.open_semantic_flow(old / NAMES[0], load(old / NAMES[1]), ASSET)['slot_id'] == 'slot-0'
            rows[order + ':' + failure] = row
    ownership = dict(stored_slot_exists='slot-0' in state['slots'], stored_owner_owned=old_owner['state'] == 'owned',
        current_witness_matches=first['evidence']['entry_exit_proven'] and positives['stored_owner_current_witness'],
        exact_island_span=len(first['evidence']['body_span']) == 2 and len(first['evidence']['show_spans']) == 3,
        containment=isolated['foreign_glyph_in_island']['isolated'],
        entry_exit_derived=first['evidence']['entry_exit_proven'], candidate_rebind=positives['owner_rebind'],
        stale_stored_witness_refused=isolated['old_owner_witness_new_pdf']['isolated'],
        marker_spoof_refused=isolated['marker_spoof']['isolated'], stale_context_refused=isolated['stale_context']['isolated'])
    gates = dict(
        model_determinism=dict(exact_derived=positives['repeated_noop_reopen'] and positives['noop_reopen']),
        input_admissibility=dict(scoped_physical=positives['stored_semantic_payload'],
            scope_conditions=all(negatives[k]['refused'] for k in ('second_slot_present', 'continuation_present',
                'generated_continuation_slot', 'unowned_source_slot', 'wrong_region_id'))),
        authenticated_binding=dict(
            current_binding=positives['stored_shared_flow_reopen'] and positives['noop_reopen'] and all(v['refused'] for v in negatives.values()),
            semantic_transition_authority=positives['text_edit_authorized'] and negatives['unauthorized_candidate_semantic']['refused']
                and negatives['semantic_only_reseal']['refused'] and negatives['implicit_confirmation']['refused'],
            stored_owner_binding=positives['stored_owner_current_witness'] and isolated['copied_owner_record']['isolated'],
            physical_mutation_ownership=all(ownership.values()) and all(v['isolated'] for v in isolated.values()),
            candidate_reverification=positives['fresh_candidate_validate'],
            owner_semantic_rebind=positives['owner_rebind'] and positives['semantic_payload_update'] and all(negatives[k]['refused'] for k in
                ('new_semantic_old_owner_witness', 'new_semantic_old_owner_consistent_binding', 'new_owner_old_semantic')),
            fresh_bundle_reopen=positives['fresh_published_reopen'] and positives['repeated_noop_reopen'],
            atomic_pair_publication=publication_gate(rows) and all(r['old_still_opens'] for r in rows.values())))
    require(runtime_digest() == before, 'RUNTIME_CHANGED')
    return dict(base=BASE, runtime_sha256=before, schema=model.SCHEMA, artifacts=list(NAMES),
        field_classes=model.FIELD_CLASSES, positive=positives, negative=negatives, isolated_owner_refusals=isolated, ownership=ownership,
        stored_reopen=compact(first), published_reopens=reopen, edit_diff=edit['diff'], publication=rows,
        gates=gates, verdict=model.verdict(gates),
        limitation='Runtime _source_body writer stands in for the L2 canonical island writer; '
                   'no runtime schema/opener/writer is implemented. No latest/current pointer is maintained.')


def main():
    global ASSET
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path); parser.add_argument('--output', type=Path); parser.add_argument('--reopen', type=Path)
    args = parser.parse_args()
    if args.reopen:
        # Fresh process: only the published bundle (PDF + sidecar) and the supplied asset.
        ASSET = (args.reopen.parent.parent / 'fixture' / 'asset.ttf').read_bytes()
        print(json.dumps(compact(model.open_semantic_flow(args.reopen / NAMES[0], load(args.reopen / NAMES[1]), ASSET)),
                         sort_keys=True))
        return
    result = evaluate(args.work.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2, sort_keys=True) + '\n').encode('utf-8'))
    print(result['verdict'])


if __name__ == '__main__':
    main()
