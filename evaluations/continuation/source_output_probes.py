"""Small Windows supplemental probes using existing source-ownership fixtures.

These are separate synthetic evidence, never substitutes for the real PDF.
All measurements use current runtime validators and existing renderer helpers.
"""
from copy import deepcopy
from pathlib import Path
import sys

from pypdf import PdfReader, PdfWriter

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.editable import edit_document
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow
from pdfeditor.story_flow import confirm_story
from evaluations.continuation import evaluate as single
from evaluations.continuation import canonical_metrics as metrics
from evaluations.continuation.source_output_external import source_metrics, compare_source_rows, ENGINE
from evaluations.continuation.source_slot_accumulation import runtime_digest
from evaluations.backend.followup import render_audit


def fixture_helpers():
    # Reuse the established safe fixture, not a second PDF lifecycle writer.
    tests = str(single.ROOT / 'tests')
    if tests not in sys.path:
        sys.path.insert(0, tests)
    from test_continuation import prepared, change, saved
    return prepared, change, saved


def render_noop(before, after, directory):
    rows = {}
    for page in range(1, len(PdfReader(before).pages)+1):
        folder = directory / str(page)
        folder.mkdir(parents=True)
        r = render_audit(before, after, page, folder, (0, 0, 320, 260), edited_pages={1})
        single.write(folder / 'render.json', r)
        if not (r['before']['rendered'] and r['after']['rendered']):
            raise ValueError('required Poppler rendering unavailable')
        rows[str(page)] = dict(mupdf_equal=r['mupdf_page_pixels'],
            poppler_changed_pixels=r['poppler_diff']['all_changed_pixels'],
            poppler_size_changed=r['poppler_diff']['size_changed'])
    return dict(dpi=144, pages=rows, passed=all(r['mupdf_equal'] and
        r['poppler_changed_pixels']==0 and not r['poppler_size_changed'] for r in rows.values()))


def suffix_witness(pdf):
    content = ContentPage(pdf, 1)
    try:
        event = next(e for e in content.events if ''.join(c.text for c in e.chars)=='KEEP')
        state = event.state.report()
        state.pop('font_xref')
        return dict(state=state, text_matrix=list(event.text_matrix), line_matrix=list(event.line_matrix),
            chars=[dict(text=c.text, code=c.code.hex(), origin=list(c.origin), advance=c.advance) for c in event.chars])
    finally:
        content.close()


def decorate_scaled(source):
    writer = PdfWriter(clone_from=PdfReader(source))
    stream = writer.pages[0]['/Contents'].get_object()
    # KEEP is a suffix in the *same* original text object, after a relative Td.
    data = stream.get_data().replace(b' ET', b' 200 0 Td (KEEP) Tj ET')
    stream.set_data(b'q 0.83 0 0 0.91 7.25 11.5 cm ' + data + b' Q')
    writer.write(source)


def probe(root, name, *, scale=False, empty=False):
    prepared, change, saved = fixture_helpers()
    directory = root / name
    directory.mkdir()
    stage = 'prepare'
    result = dict(status='failed', stages={})
    try:
        source, state = prepared(directory, confirm=False, decorate=decorate_scaled if scale else None)
        initial = deepcopy(state)
        single.write(directory / 'initial.json', initial)
        first_text = '' if empty else 'ABCDE'
        first = None
        for stage, text in [('first-empty' if empty else 'first', first_text), ('noop', None),
                            *([('regrow', 'ABCDE')] if empty else [])]:
            out, updated, report = saved(directory, source, state,
                {} if text is None else change(state, text), stage)
            single.write(directory / (stage+'-report.json'), report)
            row = dict(source_slots=source_metrics(out, updated),
                       page_program=metrics.mutation_breakdown(source, out, updated, report))
            if scale:
                row['suffix'] = suffix_witness(out)
            if first is None:
                first = row
                row['uninitialized_to_owned'] = all(initial['slots'][sid]['source_output']['state']=='uninitialized'
                    and s['record']['state']=='owned' for sid,s in row['source_slots'].items())
            else:
                row['invariants'] = compare_source_rows(first['source_slots'], row['source_slots'], noop=text is None)
            if text is None:
                row['renderer'] = render_noop(source, out, directory/'noop-render')
                row['body_bytes_equal'] = all(metrics.program(source,p)==metrics.program(out,p)
                    for p in range(1,len(PdfReader(source).pages)+1))
            if empty and stage in ('first-empty','noop'):
                p = updated['slots']['slot-0']['binding']['paragraph']
                measure = row['source_slots']['slot-0']
                row['empty_checks'] = dict(painting_shows_zero=measure['body']['painting_text_show_count']==0,
                    typing_witness_exactly_one=measure['empty_TJ']==1,
                    required_style_witnesses_only=measure['empty_TJ']==len(p['style_slot_bindings'])==1,
                    insertion_binding_present=bool(p.get('insertion_binding')))
            result['stages'][stage] = row
            single.write(directory / 'checks.json', result)
            source, state = out, updated
        passed = all(all(all(c.values()) for c in r.get('invariants',{}).values())
            and all(r.get('empty_checks',{}).values()) and r.get('renderer',{}).get('passed',True)
            and r.get('body_bytes_equal',True) and r.get('uninitialized_to_owned',True)
            for r in result['stages'].values())
        if scale:
            original = suffix_witness(directory/'source.pdf')
            result['suffix_state_preserved'] = all(r['suffix']==original for r in result['stages'].values())
            result['ctm'] = [.83,0,0,.91,7.25,11.5]
            passed &= result['suffix_state_preserved']
        if empty:
            result['regrow_text'] = state['paragraphs']['A']['logical']['text']
            passed &= result['regrow_text']=='ABCDE'
        result['status'] = 'passed' if passed else 'failed'
    except Exception as exc:
        result.update(failure=repr(exc), failed_stage=stage)
    single.write(directory / 'checks.json', result)
    return result


def operational(root):
    prepared, change, saved = fixture_helpers()
    directory = root / 'operational'
    directory.mkdir()
    source, state = prepared(directory, confirm=False)
    before = single.source_sha(source)
    denied = None
    try:
        edit_document(source, state['slots']['slot-0']['binding'], directory/'bad.pdf', directory/'bad.json', [],
                      _source_output=state['slots']['slot-0']['source_output'])
    except PdfError as exc:
        denied = str(exc)
    if denied != 'source output requires a text-only owning source slot':
        raise ValueError('ordinary edit_document did not reject the private flag at the owner guard: '+str(denied))
    if (directory/'bad.pdf').exists() or (directory/'bad.json').exists() or single.source_sha(source)!=before:
        raise ValueError('private flag attempt published or changed the source')
    out, updated, _ = saved(directory, source, state, change(state,'ABCDE'), 'owned')
    p = updated['slots']['slot-0']['binding']['paragraph']
    region = updated['regions']['R1']
    # Re-confirm semantic content from the current PDF without importing any
    # source_output record. The existing foreign-marker guard must refuse.
    story = confirm_story(out, {'part': dict(page=1, bounds=region['bounds'], paragraph=p,
        paint_relations=[], layout=dict(x=20, baseline=60, width=150, max_bottom=78, min_line_height=22,
                                        first_line_indent=0))}, paragraph_id='A', chain=['part'],
        protected_regions={}, styles={'body':dict(provider=dict(path=str(directory/'font.ttf')),
            provider_relation='substituted')},
        style_assignments={'part':{s['id']:'body' for s in p['styles']}}, typing_style_id='body')
    refusal = None
    try:
        confirm_shared_flow(out, {'A':story}, flow_id='fresh-without-sidecar', paragraph_order=['A'],
            regions=updated['regions'], region_order=updated['flow']['regions'], slot_regions={'A':{'part':'R1'}},
            paragraph_policies=updated['paragraph_policies'], follows=[], protected_regions={})
    except PdfError as exc:
        refusal = str(exc)
    if refusal != 'source output marker inventory differs from owned slots':
        raise ValueError('sidecar-loss confirmation failed at an unexpected guard: '+str(refusal))
    return dict(status='passed', private_flag_refusal=denied, private_flag_output_published=False,
        sidecar_loss_refusal=refusal, automatic_reconfirm=False,
        code_path='edit_document -> write_editable -> plan_editable -> plan_paragraph_edit (owner is None)')


def adjacent(root):
    _, _, saved = fixture_helpers()
    from test_source_ownership import custom_flow
    from pdfeditor import source_ownership as owned
    directory = root/'adjacent'
    directory.mkdir()
    source, state = custom_flow(directory,
        b'BT /Regular 12 Tf 20 200 Td (AB) Tj ET BT /Regular 12 Tf 155 200 Td (CD) Tj ET',
        [('A',[0,1],20,50,[18,40,73,78],60), ('B',[2,3],155,60,[148,40,223,78],60)])
    first, state, _ = saved(directory, source, state, {}, 'first')
    second, updated, _ = saved(directory, first, state, {}, 'noop')
    data = metrics.program(first,1)
    spans = sorted(owned.inventory(data).values())
    if len(spans)!=2:
        raise ValueError('expected two separately owned source islands')
    gaps = [b[0]-a[3] for a,b in zip(spans,spans[1:])]
    result = dict(status='passed', islands=len(spans), marker_ranges=[[s[0],s[3]] for s in spans],
        intervening_byte_lengths=gaps, directly_adjacent=False,
        page_bytes_equal=data==metrics.program(second,1), renderer=render_noop(first,second,directory/'render'))
    if min(gaps)<=0 or not result['page_bytes_equal'] or not result['renderer']['passed']:
        raise ValueError('writer produced adjacent markers or unstable output')
    single.write(directory/'checks.json', result)
    return result


def run(root):
    if runtime_digest()!=ENGINE:
        raise ValueError('runtime changed')
    results = {}
    for name, options in [('scaled-ctm',dict(scale=True)), ('creation-empty',dict(empty=True))]:
        results[name] = probe(root, name, **options)
        single.write(root/'supplemental.json', results)
        print(name, results[name]['status'], flush=True)
    try:
        results['operational'] = operational(root)
    except Exception as exc:
        results['operational'] = dict(status='failed',failure=repr(exc))
    try:
        results['adjacent'] = adjacent(root)
    except Exception as exc:
        results['adjacent'] = dict(status='failed',failure=repr(exc))
    results['engine_unchanged'] = runtime_digest()==ENGINE
    single.write(root/'supplemental.json', results)
    return results


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    args=parser.parse_args()
    run(single.BASE/'runs'/args.run)
