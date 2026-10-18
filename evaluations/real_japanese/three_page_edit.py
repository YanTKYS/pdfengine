"""Sequential editing of three related pages of an unchanged public Japanese judgment.

Edits are evaluation examples, not a representation of the court's decision.
"""
import argparse
import json
import logging
import time
from pathlib import Path

from pdfeditor.backend import PdfError
from pdfeditor.continuation import inspect_continuation_boundaries, slot_id
from pdfeditor.owned_flow_proposal import propose_owned_flow_extension, accept_owned_flow_report
from pdfeditor.page_proposal import _Analysis, propose_page_flow, accept_page_flow_report, replace_in_flow
from pdfeditor.post_edit_extension import inspect_fixed_foreign
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.source_ownership import immutable

SOURCE = Path(__file__).resolve().parents[1] / 'realpdf/corpus/courts_97044.pdf'
URL = 'https://www.courts.go.jp/assets/hanrei/hanrei-pdf-97044.pdf'
SHA = '70ce8722348b21c10a7035a5b6dd09dc641b032d7ec33588e9bc8cb711ef17f2'
CONFIRMATIONS = {
    1: {'P1': {'s1': {'tracking': .24}}},
    2: {'P1': {'s2': {'tracking': .24}, 's3': {'tracking': .6}},
        'P2': {'s1': {'tracking': .24}}, 'P3': {'s2': {'tracking': .24}},
        'P4': {'s2': {'tracking': .24}}, 'P5': {'s1': {'tracking': .24}}},
    3: {'P1': {'s2': {'tracking': .24}}},
}
LINES = {1: range(25, 28), 2: range(1, 27), 3: range(1, 5)}
FIND = '入居することはなかった。'
INITIAL = FIND + 'なお、'
LONG = FIND + 'なお、入居地区が確定し、入居すべき居室も確定していたが、入居することはなかった。'
B_FIND = 'いずれも実現しなかった。'
B_LONG = B_FIND + '本件建物から退去することは困難であるなどとの相談を複数回行い、いずれも実現しなかった。'
A_SECOND = LONG.replace('入居地区が確定し', '入居地区も確定し')
B_SECOND = B_LONG.replace('相談を複数回', '相談も複数回')
NOOP_STEMS = {2: 'extension-2-noop-precise', 3: 'extension-3-noop'}
# Include the original paragraph's independently styled trailing space in the
# first growth replacement. A trimmed-only style has no physical witness in a
# new fragment; this is an explicit owned-text edit, never automatic cleanup.
A_FIRST_FIND = INITIAL + ' '
B_FIRST_FIND = B_FIND + ' '
FIRST_REFUSAL = 'before-page-2-no-tail-space'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def page_proposal(source, number, output):
    saved = output / f'page-{number}-proposal.json'
    if saved.exists():
        observed = json.loads(saved.read_text())
        assert observed['binding']['source_sha256'] == source_sha(source)
        assert observed.get('source_style_confirmations') == CONFIRMATIONS[number]
        # Acceptance reobserves the saved proposal through production _recompute.
        return observed
    return propose_page_flow(source, number, line_ids=[f'p{number}-l{i}' for i in LINES[number]],
        font_candidates=[], include_embedded_fonts=True, font_cache=output / 'fonts',
        source_style_confirmations=CONFIRMATIONS[number])


def change(state, pid, old, new):
    # All replacement characters are covered by the original embedded font.
    registry = state['paragraphs'][pid]['style_registry']
    style = next(k for k, v in registry.items() if v['attributes']['tracking']['value'] == .24)
    return replace_in_flow(state, old, new, paragraph_id=pid, style_id=style)


def save(source, state, output, name, edits, *, whole=False):
    from .three_page_audit import audit
    pdf, side = output / f'{name}.pdf', output / f'{name}.json'
    report = edit_shared_flow(source, state, pdf, side, edits)
    write(output / f'{name}-report.json', report)
    opened = open_shared_flow(pdf, side)
    assert opened['status'] == 'restored', opened
    new = opened['state']
    for sid, old in state['slots'].items():
        current = new['slots'][sid]
        if old.get('source_output'):
            assert immutable(old['source_output']) == immutable(current['source_output'])
        if old.get('creation_binding'):
            assert old['creation_binding'] == current['creation_binding']
    assert state.get('continuation_destinations', {}) == new.get('continuation_destinations', {})
    assert state['pages'] == new['pages']
    font_changes = generated_font_evidence(state, new, edited=bool(edits))
    expected = {pid: p['logical']['text'] for pid, p in state['paragraphs'].items()}
    for pid, changes in edits.items():
        for edit in sorted(changes['edits'], key=lambda e: e['start'], reverse=True):
            expected[pid] = expected[pid][:edit['start']] + edit['text'] + expected[pid][edit['end']:]
    assert expected == {pid: p['logical']['text'] for pid, p in new['paragraphs'].items()}
    checked = audit(source, pdf, state, new, report, output / f'{name}-audit', whole=whole)
    checked['font_registry_evidence'] = font_changes
    checked['reopen'] = 'restored'
    checked['pdf_sha256'] = new['pdf_sha256']
    write(output / f'{name}-audit.json', checked)
    independent_releases(new)
    print(name, 'restored', {sid: s['occupancy'] for sid, s in new['slots'].items()}, flush=True)
    return pdf, new


def generated_font_evidence(old, new, *, edited):
    """Registry equality for no-ops; glyph-level program equality for edits.

    A replaced generated occurrence may require a new subset under the same
    resource alias. The independent audit still compares every retained/reused
    occurrence's exact code, GID, resource and program, and every new occurrence
    with its explicitly selected provider. Record registry changes alongside
    that audit rather than treating a replaced subset as an immutable source.
    """
    before, after = old.get('generated_fonts', {}), new.get('generated_fonts', {})
    providers = lambda state: {pid: {sid: entry['reflow_provider']
        for sid, entry in paragraph['style_registry'].items()}
        for pid, paragraph in state['paragraphs'].items()}
    assert providers(old) == providers(new), 'explicit providers must remain unchanged'
    changes = []
    for page in sorted(set(before) | set(after)):
        a, b = before.get(page, {}), after.get(page, {})
        for alias in sorted(set(a) | set(b)):
            if a.get(alias) != b.get(alias):
                changes.append(dict(page=int(page), resource=alias,
                                    before=a.get(alias), after=b.get(alias)))
    if not edited:
        assert not changes, 'no-op generated font registry must be unchanged'
    return dict(mode='glyph-program-equality-for-edits' if edited else 'exact-registry-equality',
        explicit_provider_programs_unchanged=True, changes=changes,
        retained_and_reused_occurrences='exact code/GID/resource/program equality in independent audit',
        new_occurrences='provider program SHA, outline and advance equality in independent audit')


def refuse(source, state, output, name, edits):
    pdf, side = output / f'{name}.pdf', output / f'{name}.json'
    assert not pdf.exists() and not side.exists()
    try:
        edit_shared_flow(source, state, pdf, side, edits)
    except PdfError as exc:
        reason = str(exc)
    else:
        raise AssertionError(f'{name}: unauthorized growth succeeded')
    assert not pdf.exists() and not side.exists()
    write(output / f'{name}-refusal.json', {'reason': reason, 'published': False,
        'source_sha256': source_sha(source), 'edits': edits})
    print(name, 'REFUSED', reason, flush=True)


def kept(old, new):
    for sid, slot in old['slots'].items():
        assert new['slots'][sid] == slot
    for key in ('continuation_destinations', 'destination_bindings', 'generated_fonts'):
        assert all(new.get(key, {}).get(k) == v for k, v in old.get(key, {}).items())


def owner(state, paragraph):
    return next(sid for sid, slot in state['slots'].items()
                if slot['paragraph_id'] == paragraph and slot.get('source_output'))


def independent_releases(state):
    expected = {'P1-next': 'D1', 'B-next': 'C'}
    owners = []
    destinations = state.get('continuation_destinations', {})
    for destination, pid in expected.items():
        if destination not in destinations:
            continue
        actual = set(destinations[destination]['vacated_space']['owners'])
        assert actual == {owner(state, pid)}
        owners.append(actual)
    if len(owners) == 2:
        assert owners[0].isdisjoint(owners[1])


def grow(source, state, number, output):
    pid, old, new = ('P1', A_FIRST_FIND, LONG) if number == 2 else ('B', B_FIRST_FIND, B_LONG)
    edits = change(state, pid, old, new)
    refusal_name = FIRST_REFUSAL if number == 2 else 'before-page-3'
    refusal = json.loads((output / f'{refusal_name}-refusal.json').read_text())
    assert edits == refusal['edits'], 'the exact refused request must be retried'
    paragraph = state['paragraphs'][pid]
    last = paragraph['logical']['style_spans'][-1]
    assert paragraph['logical']['text'][last['start']:last['end']] == ' '
    assert paragraph['style_registry'][last['style_id']]['attributes']['tracking']['value'] == 0
    write(output / f'grown-{number}-request.json', dict(edits=edits, original_trailing_space=last,
        trailing_space_policy='explicit deletion within owned replacement; no automatic trimming',
        refused_revision=refusal['source_sha256'], input_revision=source_sha(source)))
    source, state = save(source, state, output, f'grown-{number}', edits)
    proof = used_destination(output, f'grown-{number}', state, pid, 'B' if number == 2 else 'C')
    write(output / f'grown-{number}-destination-proof.json', proof)
    if number == 2:
        refuse(source, state, output, 'before-page-3', change(state, 'B', B_FIRST_FIND, B_LONG))
    return source, state


def used_destination(output, name, state, pid, follower):
    destination = state['continuation_destinations'][f'{pid}-next']
    generated = slot_id(destination)
    slot = state['slots'][generated]
    assert slot['occupancy'] is not None
    report = json.loads((output / f'{name}-report.json').read_text())
    assert report['plan']['fragments'][generated]['lines']
    assert state['regions'][slot['region_id']]['page'] == (2 if pid == 'P1' else 3)
    target = owner(state, follower)
    initial = json.loads((output / f'extension-{2 if pid == "P1" else 3}-accepted.json').read_text())['state']
    assert state['slots'][target]['occupancy']['baseline'] > initial['slots'][target]['occupancy']['baseline']
    witness = next(step for step in report['steps'] if step['slot_id'] == generated)['report']['vacated_space']
    assert {o['source_owner'] for o in witness['owners']} == set(destination['vacated_space']['owners'])
    return dict(generated_slot=generated, source_follower=target,
                occupancy=slot['occupancy'], follower_occupancy=state['slots'][target]['occupancy'],
                same_transaction_release=witness)


def compare_regrown_c(output, before, after, before_state, after_state, before_audit, after_audit):
    """Compare actual C placement and both page-3 rasters without another save."""
    import shutil
    import subprocess
    import pymupdf
    from PIL import Image
    from .three_page_audit import _pixels

    directory = output / 'regrow-B-initial-page3-comparison'
    directory.mkdir(exist_ok=False)
    old_c = next(p for p in before_audit['placements'] if p['paragraph'] == 'C')
    new_c = next(p for p in after_audit['placements'] if p['paragraph'] == 'C')
    observations, images = [], []
    with pymupdf.open(before) as old, pymupdf.open(after) as new:
        for document, state in ((old, before_state), (new, after_state)):
            page = document[2]
            glyphs = [c for span in page.get_texttrace() for c in span['chars']]
            ids = state['slots'][owner(state, 'C')]['binding']['paragraph']['selection']['glyph_ids']
            observations.append([glyphs[i] for i in ids])
            pix = page.get_pixmap(dpi=144, colorspace=pymupdf.csRGB, alpha=False)
            images.append(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
        mupdf = _pixels(*images, old[2].rect, [])
        rect = old[2].rect
    renderer = shutil.which('pdftoppm')
    assert renderer, 'Poppler is required for regrowth placement equality'
    for label, pdf in (('initial', before), ('regrow', after)):
        subprocess.run([renderer, '-f', '3', '-l', '3', '-singlefile', '-r', '144', '-png',
                        str(pdf), str(directory / label)], check=True, capture_output=True, timeout=120)
    with Image.open(directory / 'initial.png') as a, Image.open(directory / 'regrow.png') as b:
        poppler = _pixels(a.convert('RGB'), b.convert('RGB'), rect, [])
    evidence = dict(scope='regrow-C-placement-and-page3-render-equality', dpi=144,
        initial_baselines=old_c['baselines'], regrow_baselines=new_c['baselines'],
        planned_baselines_equal=old_c['baselines'] == new_c['baselines'],
        glyphs_checked=len(observations[0]), glyphs_equal=observations[0] == observations[1],
        mupdf_changed_pixels=mupdf, poppler_changed_pixels=poppler)
    write(output / 'regrow-B-placement-equality.json', evidence)
    assert evidence['glyphs_equal'] and mupdf == poppler == 0, evidence
    return evidence


def compare_a_resume(output, name, before, after, before_state, after_state, pages, *, compare_codes=False):
    """Read-only exact owned-glyph and whole-page renderer comparison."""
    import shutil
    import subprocess
    import pymupdf
    from PIL import Image
    from .three_page_audit import _codes, _pixels

    directory = output / f'{name}-input-comparison'
    directory.mkdir(exist_ok=False)
    renderer = shutil.which('pdftoppm')
    assert renderer, 'Poppler is required for A resume placement equality'
    results = []
    with pymupdf.open(before) as old, pymupdf.open(after) as new:
        for number in pages:
            observations, images = [], []
            for document, state in ((old, before_state), (new, after_state)):
                page = document[number - 1]
                glyphs = [c for span in page.get_texttrace() for c in span['chars']]
                observations.append({sid: [glyphs[i] for i in slot['binding']['paragraph']['selection']['glyph_ids']]
                    for sid, slot in state['slots'].items()
                    if state['regions'][slot['region_id']]['page'] == number})
                pix = page.get_pixmap(dpi=144, colorspace=pymupdf.csRGB, alpha=False)
                images.append(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
            rect = old[number - 1].rect
            mupdf = _pixels(*images, rect, [])
            for label, pdf in (('input', before), ('output', after)):
                subprocess.run([renderer, '-f', str(number), '-l', str(number), '-singlefile',
                    '-r', '144', '-png', str(pdf), str(directory / label)],
                    check=True, capture_output=True, timeout=60)
            with Image.open(directory / 'input.png') as a, Image.open(directory / 'output.png') as b:
                poppler = _pixels(a.convert('RGB'), b.convert('RGB'), rect, [])
            results.append(dict(page=number, owned_glyphs_equal=observations[0] == observations[1],
                glyphs_checked=sum(map(len, observations[0].values())),
                mupdf_changed_pixels=mupdf, poppler_changed_pixels=poppler))
            if compare_codes:
                results[-1]['all_glyph_codes_resources_programs_equal'] = _codes(before, number) == _codes(after, number)
    evidence = dict(scope='exact-input-owned-glyphs-and-whole-page-render', dpi=144, pages=results)
    evidence['passed'] = all(p['owned_glyphs_equal'] and p['mupdf_changed_pixels'] == p['poppler_changed_pixels'] == 0
                             and p.get('all_glyph_codes_resources_programs_equal', True)
                             for p in results)
    write(output / f'{name}-input-comparison.json', evidence)
    return evidence


def extension(source, state, number, output):
    accepted_path = output / f'extension-{number}-accepted.json'
    if accepted_path.exists():
        accepted = json.loads(accepted_path.read_text())['state']
        assert accepted['pdf_sha256'] == source_sha(source)
        opened = open_shared_flow(source, accepted)
        assert opened['status'] == 'restored'
        kept(state, accepted)
        independent_releases(accepted)
        print('extension', number, 'saved acceptance restored', flush=True)
        return accepted
    page = page_proposal(source, number, output)
    write(output / f'page-{number}-proposal.json', page)
    assert page['status'] == 'proposed', page.get('refusals')
    print('page', number, 'current-revision proposal ready', flush=True)
    ids = ['D1', 'D2', 'D3', 'D4', 'B'] if number == 2 else ['C']
    before = 'P1' if number == 2 else 'B'
    groups = [dict(id=pid, parts=[dict(page=number, paragraph_id=p['id'])])
              for pid, p in zip(ids, page['paragraphs'])]
    assert len(groups) == len(page['paragraphs']) == len(ids)
    r = page['region']
    release_id, destination = f'release-{number}', f'{before}-next'
    region = dict(page=number, bounds=[r['x'], 88, r['x'] + r['width'], 132],
                  x=r['x'], width=r['width'], first_baseline=100)
    boundary = next(c for c in inspect_continuation_boundaries(source, number)['candidates'] if c['ordinal'] == 0)
    confirmation = dict(destination_id=destination, paragraph_id=before, region_id=release_id,
        page=number, bounds=region['bounds'], insertion='confirmed-page-program-boundary',
        graphics_state='confirmed-boundary-state', boundary=boundary['boundary_id'])
    analysis = _Analysis(source, number - 1)
    footer = next(l for l in analysis.lines if l['text'] == f'- {number - 1} - ')
    fixed = inspect_fixed_foreign(source, page=number - 1, line_ids=[footer['id']],
        classification='footer', body_bounds=[0, 0, analysis.width, footer['bbox'][1]])
    request = dict(region_id=f'page-{number}',
        page_break=dict(before=before, after=ids[0], minimum_baseline_gap=26.4,
                        placement='source-region-floor-and-same-page-follow'),
        fixed_foreign=[fixed],
        vacated_continuation=dict(region_id=release_id, region=region,
                                 release_paragraphs=[ids[0]], confirmation=confirmation))
    proposal = propose_owned_flow_extension(source, state, [page], paragraphs=groups,
        relationship='Independent consecutive factual paragraphs of the same judgment, section 1; '
                     'the page 1 title and reasons heading, all later page 3 paragraphs and section 2 stay foreign.',
        next_page=request)
    write(output / f'extension-{number}-proposal.json', proposal)
    print('extension', number, 'reviewed', flush=True)
    accepted = accept_owned_flow_report(source, proposal)
    write(output / f'extension-{number}-accepted.json', accepted)
    print('extension', number, 'accepted', flush=True)
    kept(state, accepted['state'])
    independent_releases(accepted['state'])
    return accepted['state']


@proof_session
def run(output, phase):
    if phase == 'initial':
        output.mkdir(parents=True, exist_ok=False)
    else:
        assert output.is_dir(), 'resume phases require an existing measured history'
    original_sha = source_sha(SOURCE)
    assert original_sha == SHA
    if phase == 'initial':
        page = page_proposal(SOURCE, 1, output)
        write(output / 'page-1-proposal.json', page)
        accepted = accept_page_flow_report(SOURCE, page)
        write(output / 'page-1-accepted.json', accepted)
        source, state = save(SOURCE, accepted['state'], output, 'original-noop', {}, whole=True)
        source, state = save(source, state, output, 'initial', change(state, 'P1', FIND, INITIAL))
        refuse(source, state, output, FIRST_REFUSAL, change(state, 'P1', A_FIRST_FIND, LONG))
    elif phase in ('extend-2', 'extend-3'):
        number = int(phase[-1])
        stem = 'initial' if number == 2 else 'grown-2'
        source = output / f'{stem}.pdf'
        opened = open_shared_flow(source, output / f'{stem}.json')
        assert opened['status'] == 'restored'
        if number == 2 and not (output / f'{FIRST_REFUSAL}-refusal.json').exists():
            refuse(source, opened['state'], output, FIRST_REFUSAL, change(opened['state'], 'P1', A_FIRST_FIND, LONG))
        state = extension(source, opened['state'], number, output)
        source, state = save(source, state, output, NOOP_STEMS[number], {}, whole=True)
        source, state = grow(source, state, number, output)
    elif phase == 'grow-2':
        # Resume the measured accepted/no-op history without replaying any edit.
        previous = output / 'initial.pdf'
        first = open_shared_flow(previous, output / 'initial.json')
        assert first['status'] == 'restored'
        if not (output / f'{FIRST_REFUSAL}-refusal.json').exists():
            refuse(previous, first['state'], output, FIRST_REFUSAL, change(first['state'], 'P1', A_FIRST_FIND, LONG))
        source = output / f'{NOOP_STEMS[2]}.pdf'
        opened = open_shared_flow(source, output / f'{NOOP_STEMS[2]}.json')
        assert opened['status'] == 'restored'
        assert json.loads((output / f'{NOOP_STEMS[2]}-audit.json').read_text())['scope'] == 'all-page-whole-noop'
        source, state = grow(source, opened['state'], 2, output)
    elif phase == 'grow-3':
        # One edit from the independently audited saved revision. Do not replay
        # adoption, its no-op, earlier edits, or the later lifecycle.
        started = time.perf_counter()
        assert not list(output.glob('grown-3*')), 'preserve every previous growth artifact'
        stem = 'extension-3-noop-roundtrip'
        source, side = output / f'{stem}.pdf', output / f'{stem}.json'
        audited = json.loads((output / f'{stem}-audit.json').read_text())
        input_sha = source_sha(source)
        assert audited['pdf_sha256'] == input_sha
        assert audited['scope'] == 'all-page-whole-noop' and audited['dpi'] == 144
        assert [p['page'] for p in audited['pages']] == list(range(1, 14))
        assert all(p['mupdf_changed_pixels'] == p['poppler_changed_pixels'] == 0
                   for p in audited['pages'])
        opened = open_shared_flow(source, side)
        assert opened['status'] == 'restored'
        before = opened['state']
        assert before['pdf_sha256'] == input_sha
        assert set(before['continuation_destinations']) == {'P1-next', 'B-next'}
        independent_releases(before)
        refused = json.loads((output / 'before-page-3-refusal.json').read_text())
        assert refused['source_sha256'] == source_sha(output / 'grown-2.pdf')
        assert not refused['published']
        assert change(before, 'B', B_FIRST_FIND, B_LONG) == refused['edits']
        write(output / 'grown-3-input.json', dict(pdf_sha256=input_sha,
            sidecar_sha256=source_sha(side), audit_sha256=source_sha(output / f'{stem}-audit.json'),
            reopen='restored', noop_pages=13, noop_both_renderers_zero=True,
            request_equals_previous_refusal=True, original_sha256=original_sha))
        print('grow-3 input qualified; executing the previously refused request once', flush=True)
        source, state = grow(source, before, 3, output)
        checked = json.loads((output / 'grown-3-audit.json').read_text())
        assert len(checked['pages']) == 13
        assert all(p['mupdf_changed_pixels'] == p['poppler_changed_pixels'] == 0
                   for p in checked['pages'])
        for sid, previous in before['slots'].items():
            assert all(state['slots'][sid][k] == previous[k] for k in ('paragraph_id', 'region_id'))
        assert before['continuation_destinations'] == state['continuation_destinations']
        continuation = slot_id(state['continuation_destinations']['B-next'])
        cslot = owner(state, 'C')
        first = before['slots'][cslot]['occupancy']['baseline']
        last = state['slots'][cslot]['occupancy']['baseline']
        assert last > first
        b_before = [p for p in audited['placements'] if p['paragraph'] == 'B']
        b_after = [p for p in checked['placements'] if p['paragraph'] == 'B']
        page3 = [p for p in b_after if p['slot'] == continuation]
        assert len(page3) == 1 and page3[0]['page'] == 3 and page3[0]['lines'] > 0
        assert source_sha(SOURCE) == original_sha
        result = dict(scope='grow-3-only', verdict='PASS: B continues onto page 3 and C moves down',
            real_world_three_page_sequential_reflow='NOT VALIDATED',
            input_sha256=input_sha, output_sha256=source_sha(source),
            sidecar_sha256=source_sha(output / 'grown-3.json'), original_sha256=original_sha,
            original_unchanged=True, reopen='restored', elapsed_seconds=time.perf_counter() - started,
            B_before=dict(characters=len(before['paragraphs']['B']['logical']['text']), placements=b_before),
            B_after=dict(characters=len(state['paragraphs']['B']['logical']['text']), placements=b_after),
            page3_continuation=page3[0], C_baseline_before=first, C_baseline_after=last,
            C_delta_pt=last-first, existing_slot_identities_preserved=True,
            source_markers={sid: immutable(s['source_output']) for sid, s in state['slots'].items()
                            if s.get('source_output')},
            generated_creation_bindings={sid: s['creation_binding'] for sid, s in state['slots'].items()
                                         if s.get('creation_binding')},
            continuation_destinations_unchanged=True,
            release_owners={k: list(v['vacated_space']['owners'])
                            for k, v in state['continuation_destinations'].items()},
            audit=checked, unrun=['shorten/regrow/re-edit lifecycle', 'broad regressions', 'full suite'])
        write(output / 'grown-3-summary.json', result)
        print(result['verdict'] + '; stopped before lifecycle', flush=True)
    elif phase == 'b-short-regrow':
        # Exactly two saves from the measured growth, with a full restored/audit
        # gate between them. Existing artifacts are never replayed or replaced.
        for stem in ('shorten-B', 'regrow-B'):
            assert not list(output.glob(f'{stem}*')), 'preserve every previous edit artifact'
        assert not (output / 'b-short-regrow-summary.json').exists()
        source, side = output / 'grown-3.pdf', output / 'grown-3.json'
        audited = json.loads((output / 'grown-3-audit.json').read_text())
        measured = json.loads((output / 'grown-3-summary.json').read_text())
        assert source_sha(source) == audited['pdf_sha256'] == measured['output_sha256']
        assert source_sha(side) == measured['sidecar_sha256']
        assert audited['scope'] == 'all-page-outside-confirmed-regions' and audited['dpi'] == 144
        assert [p['page'] for p in audited['pages']] == list(range(1, 14))
        assert all(p['mupdf_changed_pixels'] == p['poppler_changed_pixels'] == 0
                   for p in audited['pages'])
        opened = open_shared_flow(source, side)
        assert opened['status'] == 'restored'
        initial = state = opened['state']
        assert state['pdf_sha256'] == measured['output_sha256']
        assert set(state['continuation_destinations']) == {'P1-next', 'B-next'}
        independent_releases(state)
        first = slot_id(state['continuation_destinations']['P1-next'])
        second = slot_id(state['continuation_destinations']['B-next'])
        cslot = owner(state, 'C')
        c_floor = state['regions'][state['slots'][cslot]['region_id']]['first_baseline']
        assert state['slots'][second]['occupancy'] is not None
        results = []
        print('b-short-regrow input qualified; executing only the two requested edits', flush=True)
        for name, old, new in [('shorten-B', B_LONG, B_FIND), ('regrow-B', B_FIND, B_LONG)]:
            started = time.perf_counter()
            edits = change(state, 'B', old, new)
            input_sha = source_sha(source)
            write(output / f'{name}-request.json', dict(source_sha256=input_sha, edits=edits))
            source, state = save(source, state, output, name, edits)
            checked = json.loads((output / f'{name}-audit.json').read_text())
            assert [p['page'] for p in checked['pages']] == list(range(1, 14))
            assert all(p['mupdf_changed_pixels'] == p['poppler_changed_pixels'] == 0
                       for p in checked['pages'])
            assert set(state['slots']) == set(initial['slots'])
            for sid, previous in initial['slots'].items():
                assert all(state['slots'][sid][k] == previous[k] for k in ('paragraph_id', 'region_id'))
                assert state['slots'][sid].get('creation_binding') == previous.get('creation_binding')
                if previous.get('source_output'):
                    assert immutable(state['slots'][sid]['source_output']) == immutable(previous['source_output'])
            assert state['continuation_destinations'] == initial['continuation_destinations']
            assert state['slots'][first]['occupancy'] == initial['slots'][first]['occupancy']
            continuation = state['slots'][second]
            if name == 'shorten-B':
                assert continuation['occupancy'] is None
                assert continuation['range'][0] == continuation['range'][1]
                assert continuation['binding']['paragraph']['text'] == ''
                assert continuation['binding']['paragraph']['selection']['glyph_ids'] == []
                assert state['slots'][cslot]['occupancy']['baseline'] == c_floor
                placement_equality = None
            else:
                proof = used_destination(output, name, state, 'B', 'C')
                write(output / f'{name}-destination-proof.json', proof)
                assert state['paragraphs']['B']['logical']['text'] == initial['paragraphs']['B']['logical']['text']
                assert continuation['occupancy'] == initial['slots'][second]['occupancy']
                assert continuation['range'] == initial['slots'][second]['range']
                assert state['slots'][cslot]['occupancy']['baseline'] == initial['slots'][cslot]['occupancy']['baseline']
                original_b = [p for p in audited['placements'] if p['paragraph'] == 'B']
                current_b = [p for p in checked['placements'] if p['paragraph'] == 'B']
                assert [{k: p[k] for k in ('slot', 'characters', 'lines', 'range', 'baselines')} for p in current_b] == [
                    {k: p[k] for k in ('slot', 'characters', 'lines', 'range', 'baselines')} for p in original_b]
                placement_equality = compare_regrown_c(output, output / 'grown-3.pdf', source,
                    initial, state, audited, checked)
            assert source_sha(SOURCE) == original_sha
            result = dict(name=name, scope='b-short-regrow-only', input_sha256=input_sha,
                output_sha256=source_sha(source), sidecar_sha256=source_sha(output / f'{name}.json'),
                reopen='restored', elapsed_seconds=time.perf_counter() - started,
                B_characters=len(state['paragraphs']['B']['logical']['text']),
                placements=[p for p in checked['placements'] if p['paragraph'] in ('B', 'C')],
                continuation_slot=second, continuation_occupancy=continuation['occupancy'],
                creation_binding=continuation['creation_binding'],
                existing_slot_markers_and_creation_bindings_preserved=True,
                destinations_unchanged=True, release_owners={k: list(v['vacated_space']['owners'])
                    for k, v in state['continuation_destinations'].items()},
                original_sha256=original_sha, original_unchanged=True, audit=checked)
            result['initial_growth_placement_comparison'] = placement_equality
            write(output / f'{name}-summary.json', result)
            results.append(result)
            print(name, 'measured PASS', flush=True)
        write(output / 'b-short-regrow-summary.json', dict(scope='b-short-regrow-only',
            verdict='REAL-PDF THREE-PAGE SHORTEN/REGROW — PASS',
            real_world_three_page_sequential_reflow='NOT VALIDATED', stages=results,
            unrun=['A edits', 'further re-edit lifecycle', 'additional no-op', 'broad regressions', 'full suite']))
        print('REAL-PDF THREE-PAGE SHORTEN/REGROW — PASS; stopped after two saves', flush=True)
    elif phase in ('a-short-regrow', 'a-spacing-fixed', 'ab-short-regrow'):
        # Resume exactly two saves, optionally editing A and B together.
        # Earlier histories and ownership extensions are never replayed.
        simultaneous = phase == 'ab-short-regrow'
        suffix = '-B-spacing-fixed' if simultaneous else '-spacing-fixed' if phase == 'a-spacing-fixed' else ''
        short_name, long_name = 'shorten-A' + suffix, 'regrow-A' + suffix
        for stem in (short_name, long_name, phase + '-summary'):
            assert not list(output.glob(f'{stem}*')), 'preserve every previous edit artifact'
        resume = 'regrow-A-spacing-fixed' if simultaneous else 'regrow-B'
        initial_pdf = source = output / f'{resume}.pdf'
        side = output / f'{resume}.json'
        audited = json.loads((output / f'{resume}-audit.json').read_text())
        measured = json.loads((output / f'{resume}-summary.json').read_text())
        assert source_sha(source) == audited['pdf_sha256'] == measured['output_sha256'] == (
            '7e58e00309a903865dce1c0006e4cdbde7546046aa7a9d894228be6800fffaab' if simultaneous else
            '121541869471fd0ea28f32d11c8faf4547d1bb17846fc206cea5bf264cc6ca71')
        assert source_sha(side) == measured['sidecar_sha256']
        if simultaneous:
            assert source_sha(side) == 'a062bf5f33b24416a1e92844a242631f94a2712ecbc417a0341bb7d89f13b8ce'
        assert audited['scope'] == 'all-page-outside-confirmed-regions' and audited['dpi'] == 144
        assert [p['page'] for p in audited['pages']] == list(range(1, 14))
        assert all(p['mupdf_changed_pixels'] == p['poppler_changed_pixels'] == 0 for p in audited['pages'])
        opened = open_shared_flow(source, side)
        assert opened['status'] == 'restored'
        initial = state = opened['state']
        assert state['pdf_sha256'] == audited['pdf_sha256']
        assert set(state['continuation_destinations']) == {'P1-next', 'B-next'}
        independent_releases(state)
        first = slot_id(state['continuation_destinations']['P1-next'])
        second = slot_id(state['continuation_destinations']['B-next'])
        assert all(state['slots'][sid]['occupancy'] is not None for sid in (first, second))
        assert state['paragraphs']['P1']['logical']['text'].endswith(LONG)
        assert state['paragraphs']['B']['logical']['text'].endswith(B_LONG)
        if simultaneous:
            assert tuple(len(state['paragraphs'][pid]['logical']['text']) for pid in ('P1', 'B')) == (126, 181)
        results = []
        print(phase, 'input qualified; executing only the two requested saves', flush=True)
        for name, old, new in [(short_name, LONG, FIND), (long_name, FIND, LONG)]:
            started = time.perf_counter()
            edits = change(state, 'P1', old, new)
            if simultaneous:
                b_old, b_new = (B_LONG, B_FIND) if name == short_name else (B_FIND, B_LONG)
                edits.update(change(state, 'B', b_old, b_new))
                assert set(edits) == {'P1', 'B'}
            input_sha = source_sha(source)
            write(output / f'{name}-request.json', dict(source_sha256=input_sha, edits=edits,
                transaction_scope='one edit_shared_flow call containing every requested paragraph'))
            input_state = state
            source, state = save(source, state, output, name, edits)
            checked = json.loads((output / f'{name}-audit.json').read_text())
            assert [p['page'] for p in checked['pages']] == list(range(1, 14))
            assert all(p['mupdf_changed_pixels'] == p['poppler_changed_pixels'] == 0 for p in checked['pages'])
            if simultaneous:
                expected_chars = (86, 138) if name == short_name else (126, 181)
                assert tuple(len(state['paragraphs'][pid]['logical']['text']) for pid in ('P1', 'B')) == expected_chars
            assert set(state['slots']) == set(initial['slots'])
            for sid, previous in initial['slots'].items():
                assert all(state['slots'][sid][k] == previous[k] for k in ('paragraph_id', 'region_id'))
                assert state['slots'][sid].get('creation_binding') == previous.get('creation_binding')
                if previous.get('source_output'):
                    assert immutable(state['slots'][sid]['source_output']) == immutable(previous['source_output'])
            assert state['continuation_destinations'] == initial['continuation_destinations']
            untouched = set(initial['paragraphs']) - set(edits)
            assert {pid: state['paragraphs'][pid]['logical']['text'] for pid in untouched} == {
                pid: initial['paragraphs'][pid]['logical']['text'] for pid in untouched}
            if not simultaneous or name == long_name:
                assert state['slots'][second]['occupancy'] is not None
                b_proof = used_destination(output, name, state, 'B', 'C')
                write(output / f'{name}-B-destination-proof.json', b_proof)
            continuation = state['slots'][first]
            if name == short_name:
                assert continuation['occupancy'] is None
                assert continuation['range'][0] == continuation['range'][1]
                assert continuation['binding']['paragraph']['text'] == ''
                assert continuation['binding']['paragraph']['selection']['glyph_ids'] == []
                if simultaneous:
                    other = state['slots'][second]
                    assert other['occupancy'] is None and other['range'][0] == other['range'][1]
                    assert other['binding']['paragraph']['text'] == ''
                    assert other['binding']['paragraph']['selection']['glyph_ids'] == []
                    for pid in ('D1', 'C'):
                        follower = state['slots'][owner(state, pid)]
                        assert follower['occupancy']['baseline'] == state['regions'][follower['region_id']]['first_baseline']
                comparison_pages = [] if simultaneous else [3]
            else:
                a_proof = used_destination(output, name, state, 'P1', 'D1')
                write(output / f'{name}-A-destination-proof.json', a_proof)
                assert {pid: p['logical']['text'] for pid, p in state['paragraphs'].items()} == {
                    pid: p['logical']['text'] for pid, p in initial['paragraphs'].items()}
                for sid in (initial['slots'] if simultaneous else (first, second)):
                    assert state['slots'][sid]['occupancy'] == initial['slots'][sid]['occupancy']
                    assert state['slots'][sid]['range'] == initial['slots'][sid]['range']
                if simultaneous:
                    for sid, count in ((first, 15), (second, 33)):
                        placement = next(p for p in checked['placements'] if p['slot'] == sid)
                        assert placement['characters'] == placement['glyphs'] == count
                        assert placement['lines'] == 1 and placement['baselines'] == [100]
                comparison_pages = range(1, 14)
            spacing = {}
            if phase in ('a-spacing-fixed', 'ab-short-regrow'):
                for pid in (('D1', 'D2', 'D3', 'D4') if simultaneous else ('D1', 'D2', 'D3', 'D4', 'B')):
                    sid = owner(state, pid)
                    prior = [line['baseline'] for line in input_state['slots'][sid]['binding']['physical_layout']['lines']]
                    current = [line['baseline'] for line in state['slots'][sid]['binding']['physical_layout']['lines']]
                    offsets = [b - prior[0] for b in prior]
                    assert current == [current[0] + d for d in offsets]
                    assert [b - current[0] + prior[0] for b in current] == prior
                    if name == long_name:
                        assert current == [line['baseline'] for line in initial['slots'][sid]['binding']['physical_layout']['lines']]
                    spacing[pid] = dict(slot=sid, input_baselines=prior, output_baselines=current,
                        translated_positions_exact=True, inverse_translation_exact=True)
            comparison = (compare_a_resume(output, name, initial_pdf, source, initial, state,
                          comparison_pages, compare_codes=simultaneous) if comparison_pages else None)
            assert source_sha(SOURCE) == original_sha
            result = dict(name=name, scope=phase + '-only', input_sha256=input_sha,
                output_sha256=source_sha(source), sidecar_sha256=source_sha(output / f'{name}.json'),
                reopen='restored', elapsed_seconds=time.perf_counter() - started,
                A_characters=len(state['paragraphs']['P1']['logical']['text']), placements=checked['placements'],
                B_characters=len(state['paragraphs']['B']['logical']['text']),
                edited_paragraphs=sorted(edits), edit_shared_flow_calls=1,
                continuation_slots={key: dict(slot_id=sid, occupancy=state['slots'][sid]['occupancy'],
                    creation_binding=state['slots'][sid]['creation_binding'])
                    for key, sid in (('P1-next', first), ('B-next', second))},
                existing_slot_markers_and_creation_bindings_preserved=True, destinations_unchanged=True,
                release_owners={k: list(v['vacated_space']['owners'])
                    for k, v in state['continuation_destinations'].items()},
                original_sha256=original_sha, original_unchanged=True, audit=checked,
                relative_spacing=spacing,
                input_placement_comparison=comparison,
                verdict='PASS' if comparison is None or comparison['passed'] else 'FAIL: input placement/render comparison')
            write(output / f'{name}-summary.json', result)
            assert comparison is None or comparison['passed'], comparison
            results.append(result)
            print(name, 'measured PASS', flush=True)
        verdict = ('REAL-PDF THREE-PAGE A/B SIMULTANEOUS SHORTEN/REGROW — PASS' if simultaneous else
                   'REAL-PDF THREE-PAGE A SHORTEN/REGROW — PASS')
        write(output / f'{phase}-summary.json', dict(scope=phase + '-only', verdict=verdict,
            real_world_three_page_sequential_reflow='NOT VALIDATED', stages=results,
            unrun=['additional A/B re-edits', 'final no-op', 'remaining lifecycle', 'full suite']))
        print(verdict + '; stopped after two saves', flush=True)
    elif phase == 'lifecycle':
        source = output / 'grown-3.pdf'
        opened = open_shared_flow(source, output / 'grown-3.json')
        assert opened['status'] == 'restored'
        state = opened['state']
        refuse(source, state, output, 'over-capacity', change(state, 'B', B_LONG, B_LONG * 4))
        refuse(source, state, output, 'unowned-paragraph', {'foreign-section-2': {'edits': [
            dict(start=0, end=1, text='変更', style_id=state['paragraphs']['B']['typing_style_id'])]}})
        stages = [
            ('shorten-B', [('B', B_LONG, B_FIND)]),
            ('regrow-B', [('B', B_FIND, B_LONG)]),
            ('shorten-A-B', [('P1', LONG, FIND), ('B', B_LONG, B_FIND)]),
            ('regrow-A-B', [('P1', FIND, LONG), ('B', B_FIND, B_LONG)]),
            ('second-A-B', [('P1', LONG, A_SECOND), ('B', B_LONG, B_SECOND)]),
            ('final-noop', []),
        ]
        for name, replacements in stages:
            edits = {}
            for pid, old, new in replacements:
                edits.update(change(state, pid, old, new))
            source, state = save(source, state, output, name, edits, whole=not edits)
            first = slot_id(state['continuation_destinations']['P1-next'])
            second = slot_id(state['continuation_destinations']['B-next'])
            if name in ('shorten-B', 'shorten-A-B'):
                assert state['slots'][second]['occupancy'] is None
            else:
                used_destination(output, name, state, 'B', 'C')
            if name == 'shorten-A-B':
                assert state['slots'][first]['occupancy'] is None
            else:
                assert state['slots'][first]['occupancy'] is not None
        summaries = []
        names = ['original-noop', 'initial', NOOP_STEMS[2], 'grown-2',
                 NOOP_STEMS[3], 'grown-3'] + [name for name, _ in stages]
        for name in names:
            saved = open_shared_flow(output / f'{name}.pdf', output / f'{name}.json')
            assert saved['status'] == 'restored'
            summaries.append(dict(name=name, reopen='restored', pdf_sha256=saved['state']['pdf_sha256'],
                audit=json.loads((output / f'{name}-audit.json').read_text())))
        assert source_sha(SOURCE) == SHA
        result = dict(verdict='REAL-WORLD THREE-PAGE SEQUENTIAL REFLOW VALIDATED — PASS',
            source=dict(url=URL, sha256=SHA, pages=13, edited_pages=[1, 2, 3]),
            original_sha_unchanged=True, stages=summaries,
            refusals={name: json.loads((output / f'{name}-refusal.json').read_text())
                      for name in (FIRST_REFUSAL, 'before-page-3', 'over-capacity', 'unowned-paragraph')},
            destinations=state['continuation_destinations'], final_revision=state['pdf_sha256'])
        write(output / 'summary.json', result)
        print(result['verdict'], flush=True)
    assert source_sha(SOURCE) == original_sha


if __name__ == '__main__':
    logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', choices=['initial', 'extend-2', 'grow-2', 'extend-3', 'grow-3', 'b-short-regrow', 'a-short-regrow', 'a-spacing-fixed', 'ab-short-regrow', 'lifecycle'], required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    try:
        run(args.output, args.phase)
    finally:
        print(f'phase={args.phase} elapsed_seconds={time.perf_counter() - started:.6f}', flush=True)
