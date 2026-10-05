"""L3: atomic publication of a verified semantic candidate pair (one directory rename)."""
from copy import deepcopy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pdfeditor.backend import PdfError
from pdfeditor import semantic_layout as semantic
from pdfeditor import semantic_publication as publication
from pdfeditor import semantic_writer as writer
from test_semantic_authority import alternate_font
from test_semantic_layout import make_flow, resealed, statement


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope='module')
def candidates(tmp_path_factory):
    root = tmp_path_factory.mktemp('semantic-publication')
    source, asset = make_flow(root / 'main')
    main = root / 'main'
    alternate = root / 'alternate.ttf'
    alternate.write_bytes(alternate_font())
    v3 = semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0', semantic=statement(asset))
    work = root / 'work'
    base = writer.build_semantic_candidate(main / 'rev1.pdf', v3, dict(operation='save'), workspace=work)
    size = writer.build_semantic_candidate(base['pdf'], base['sidecar'],
                                           dict(operation='reinterpret', changes=dict(font_size='13')), workspace=work)
    font = writer.build_semantic_candidate(base['pdf'], base['sidecar'], dict(
        operation='reinterpret', changes=dict(font=sha(alternate))), workspace=work, asset=alternate)
    return dict(root=root, asset=asset, alternate=alternate, base=base, size=size, font=font)


def pair(candidate):
    return candidate['pdf'], candidate['sidecar']


def entries(parent):
    return sorted(p.name for p in Path(parent).iterdir()) if Path(parent).exists() else []


@pytest.fixture
def public(tmp_path):
    parent = tmp_path / 'public'
    parent.mkdir()
    return parent


def copy_pair(tmp_path, candidate, name, sidecar_change=None, pdf_change=None):
    """A copy of a candidate pair, optionally tampered (sidecar resealed so integrity alone passes)."""
    directory = tmp_path / name
    directory.mkdir()
    pdf, sidecar = directory / 'document.pdf', directory / 'shared-flow.json'
    data = Path(candidate['pdf']).read_bytes()
    pdf.write_bytes(pdf_change(data) if pdf_change else data)
    value = json.loads(Path(candidate['sidecar']).read_text())
    sidecar.write_text(json.dumps(resealed(value, sidecar_change) if sidecar_change else value))
    return pdf, sidecar


# --- happy path ---------------------------------------------------------------------------------------------------

def test_publish_stages_verifies_renames_once_and_reopens_from_the_public_path(candidates, public, monkeypatch):
    renames, opens = [], []
    real_rename, real_open = os.rename, semantic.open_semantic_flow
    monkeypatch.setattr(os, 'rename', lambda a, b: (renames.append((Path(a), Path(b))), real_rename(a, b))[1])
    monkeypatch.setattr(semantic, 'open_semantic_flow', lambda pdf, sidecar: (opens.append(Path(pdf)), real_open(pdf, sidecar))[1])
    destination = public / 'bundle-0001'
    result = publication.publish_semantic_bundle(*pair(candidates['size']), destination)
    # Exactly one rename: the complete private staging directory becomes the bundle directory.
    assert len(renames) == 1 and renames[0][1] == destination
    staging = renames[0][0]
    assert staging.parent == public and staging.name.startswith(publication.STAGING_PREFIX)
    # Fresh verification ran on the staged pair first, then on the public path, never on the candidate path.
    assert opens == [staging / 'document.pdf', destination / 'document.pdf']
    assert entries(public) == ['bundle-0001'] and entries(destination) == ['document.pdf', 'shared-flow.json']
    assert result['directory'] == destination and result['pdf'] == destination / 'document.pdf'
    assert result['sidecar'] == destination / 'shared-flow.json' and result['verification']['status'] == 'restored'
    assert result['verification']['semantic']['style']['font_size'] == '13'
    assert result['verification']['authority']['provenance'] == semantic.CALLER_CONFIRMED


def test_published_bytes_are_the_candidate_bytes_and_the_sidecar_is_not_rewritten(candidates, public):
    pdf, sidecar = pair(candidates['font'])
    before = Path(pdf).read_bytes(), Path(sidecar).read_bytes()
    result = publication.publish_semantic_bundle(pdf, sidecar, public / 'font')
    assert result['pdf'].read_bytes() == before[0] and result['sidecar'].read_bytes() == before[1]
    assert result['sha256'] == {'document.pdf': hashlib.sha256(before[0]).hexdigest(),
                                'shared-flow.json': hashlib.sha256(before[1]).hexdigest()}
    assert (Path(pdf).read_bytes(), Path(sidecar).read_bytes()) == before  # the candidate is untouched
    value = json.loads(result['sidecar'].read_text())
    assert value == json.loads(before[1])  # pdf_sha256, owner witness, authority, registry, providers, fonts, binding
    assert value['slots']['slot-0']['semantic']['current']['provider']['path'] == str(candidates['alternate'].resolve())


def test_staged_bytes_equal_published_bytes(candidates, public, monkeypatch):
    staged = {}
    real_rename = os.rename
    def rename(a, b):
        staged.update({name: (Path(a) / name).read_bytes() for name in ('document.pdf', 'shared-flow.json')})
        return real_rename(a, b)
    monkeypatch.setattr(os, 'rename', rename)
    result = publication.publish_semantic_bundle(*pair(candidates['base']), public / 'base')
    assert staged['document.pdf'] == result['pdf'].read_bytes()
    assert staged['shared-flow.json'] == result['sidecar'].read_bytes()


@pytest.mark.parametrize('name', ['base', 'size', 'font'])
def test_published_bundle_reopens_in_a_fresh_process(candidates, public, name):
    result = publication.publish_semantic_bundle(*pair(candidates[name]), public / name)
    code = ('import json,sys;from pdfeditor.semantic_layout import open_semantic_flow;'
            'r=open_semantic_flow(sys.argv[1],sys.argv[2]);'
            'print(json.dumps([r["status"],r.get("semantic"),r.get("authority"),r.get("island",{}).get("canonical")]))')
    out = subprocess.run([sys.executable, '-c', code, str(result['pdf']), str(result['sidecar'])], capture_output=True,
                         text=True, check=True, cwd=Path(__file__).resolve().parents[1])
    status, payload, authority, canonical = json.loads(out.stdout)
    assert status == 'restored' and canonical is True
    assert payload == result['verification']['semantic'] and authority == result['verification']['authority']


# --- immutable destination ----------------------------------------------------------------------------------------

@pytest.mark.parametrize('kind', ['bundle', 'empty_directory', 'file'])
def test_existing_destination_is_refused_and_left_unchanged(candidates, public, kind):
    destination = public / 'taken'
    if kind == 'bundle':
        publication.publish_semantic_bundle(*pair(candidates['base']), destination)
    elif kind == 'empty_directory':
        destination.mkdir()
    else:
        destination.write_bytes(b'not a bundle')
    snapshot = {p: p.read_bytes() for p in public.rglob('*') if p.is_file()}
    with pytest.raises(PdfError, match='already exists'):
        publication.publish_semantic_bundle(*pair(candidates['size']), destination)
    assert entries(public) == ['taken'] and {p: p.read_bytes() for p in public.rglob('*') if p.is_file()} == snapshot


def test_republishing_the_same_bundle_never_overwrites(candidates, public):
    first = publication.publish_semantic_bundle(*pair(candidates['base']), public / 'rev')
    before = first['pdf'].read_bytes(), first['sidecar'].read_bytes()
    with pytest.raises(PdfError, match='already exists'):
        publication.publish_semantic_bundle(first['pdf'], first['sidecar'], public / 'rev')
    assert (first['pdf'].read_bytes(), first['sidecar'].read_bytes()) == before and entries(public) == ['rev']


def test_destination_appearing_after_verification_is_refused(candidates, public, monkeypatch):
    destination = public / 'race'
    real_open = semantic.open_semantic_flow
    def opened(pdf, sidecar):
        result = real_open(pdf, sidecar)
        destination.mkdir()
        (destination / 'other.txt').write_text('someone else')
        return result
    monkeypatch.setattr(semantic, 'open_semantic_flow', opened)
    with pytest.raises(PdfError, match='already exists'):
        publication.publish_semantic_bundle(*pair(candidates['base']), destination)
    assert entries(public) == ['race'] and entries(destination) == ['other.txt']


@pytest.mark.parametrize('name', ['', '.', '..', publication.STAGING_PREFIX + 'x', publication.WITHDRAWN_PREFIX + 'x'])
def test_destination_needs_a_plain_new_name(candidates, public, name):
    with pytest.raises(PdfError):
        publication.publish_semantic_bundle(*pair(candidates['base']), public / name if name else public / '')


def test_destination_parent_must_exist(candidates, public):
    with pytest.raises(PdfError, match='parent'):
        publication.publish_semantic_bundle(*pair(candidates['base']), public / 'missing' / 'bundle')
    assert entries(public) == []


# --- refusal before the rename: verification of the staged bytes --------------------------------------------------

def _owner(v):
    return v['slots']['slot-0']['source_output']


@pytest.mark.parametrize('name,sidecar_change,pdf_change,reason', [
    ('pdf_tamper', None, lambda d: d + b'\n% tampered\n', 'revision'),
    ('pdf_tamper_resealed_sha', 'reseal_pdf_sha', lambda d: d + b'\n% tampered\n', None),
    ('sidecar_unsealed', lambda v: v['slots']['slot-0']['semantic']['payload']['style'].update(font_size='14'), None, None),
    ('stale_owner', lambda v: _owner(v)['current'].update(block_sha256='0' * 64), None, 'ownership'),
    ('authority', lambda v: v['slots']['slot-0']['semantic']['current'].update(provenance=semantic.SOURCE_CONFIRMED),
     None, 'inline attributes'),
    ('semantic', lambda v: v['slots']['slot-0']['semantic']['payload']['style'].update(tracking='1/4'), None, None),
    ('font_binding', lambda v: v['slots']['slot-0']['semantic']['binding'].update(font_subset_sha256='0' * 64),
     None, 'generated font'),
    ('font_record', lambda v: v['generated_fonts']['1']['/PRF1']['provider'].update(source_sha256='0' * 64),
     None, None),
    ('source_evidence', lambda v: v['paragraphs']['A']['style_registry']['body']['attributes']['font_size'].update(
        value=13.0), None, None),
])
def test_invalid_pairs_are_refused_before_the_rename(candidates, public, tmp_path, name, sidecar_change, pdf_change,
                                                       reason):
    if sidecar_change == 'reseal_pdf_sha':  # the attacker also updates every PDF SHA in the sidecar
        tampered = pdf_change(Path(candidates['size']['pdf']).read_bytes())
        digest = hashlib.sha256(tampered).hexdigest()
        def sidecar_change(v):
            v['pdf_sha256'] = digest
            v['slots']['slot-0']['semantic']['binding']['pdf_sha256'] = digest
    pdf, sidecar = copy_pair(tmp_path, candidates['size'], name, sidecar_change, pdf_change)
    if name == 'sidecar_unsealed':  # integrity: an edit without reseal
        value = json.loads(Path(candidates['size']['sidecar']).read_text())
        value['slots']['slot-0']['semantic']['payload']['style']['font_size'] = '14'
        sidecar.write_text(json.dumps(value))
    with pytest.raises(PdfError, match='does not verify') as error:
        publication.publish_semantic_bundle(pdf, sidecar, public / 'bundle')
    if reason:
        assert reason in str(error.value)
    assert entries(public) == []  # no final directory and no staging left behind


def test_missing_provider_asset_is_refused_by_the_existing_validator(candidates, public, tmp_path):
    asset = tmp_path / 'moved.ttf'
    asset.write_bytes(candidates['alternate'].read_bytes())
    candidate = writer.build_semantic_candidate(*pair(candidates['base']), dict(
        operation='reinterpret', changes=dict(font=sha(asset))), workspace=tmp_path / 'work', asset=asset)
    asset.unlink()
    with pytest.raises(PdfError, match='does not verify'):
        publication.publish_semantic_bundle(*pair(candidate), public / 'bundle')
    assert entries(public) == []


def test_the_staged_bytes_not_the_candidate_are_verified(candidates, public, monkeypatch):
    """TOCTOU: the candidate is read once; a later change of the candidate file cannot reach the bundle."""
    pdf, sidecar = pair(candidates['size'])
    original = Path(pdf).read_bytes()
    real_write = publication._write
    def write(path, data):
        real_write(path, data)
        if path.name == 'shared-flow.json':
            Path(pdf).write_bytes(original + b'\n% changed after staging\n')  # candidate changes afterwards
    monkeypatch.setattr(publication, '_write', write)
    try:
        result = publication.publish_semantic_bundle(pdf, sidecar, public / 'bundle')
    finally:
        Path(pdf).write_bytes(original)
    assert result['pdf'].read_bytes() == original and result['verification']['status'] == 'restored'


def test_staged_tampering_is_refused(candidates, public, monkeypatch):
    real_write = publication._write
    def write(path, data):
        real_write(path, data + b'\n% tampered in staging\n' if path.name == 'document.pdf' else data)
    monkeypatch.setattr(publication, '_write', write)
    with pytest.raises(PdfError, match='does not verify'):
        publication.publish_semantic_bundle(*pair(candidates['size']), public / 'bundle')
    assert entries(public) == []


def test_staged_change_after_verification_is_refused(candidates, public, monkeypatch):
    real_open = semantic.open_semantic_flow
    def opened(pdf, sidecar):
        result = real_open(pdf, sidecar)
        Path(sidecar).write_bytes(Path(sidecar).read_bytes() + b' ')
        return result
    monkeypatch.setattr(semantic, 'open_semantic_flow', opened)
    with pytest.raises(PdfError, match='changed after verification'):
        publication.publish_semantic_bundle(*pair(candidates['size']), public / 'bundle')
    assert entries(public) == []


# --- failure injection before the rename ---------------------------------------------------------------------------

def _fail_on(name):
    real = publication._write
    def write(path, data):
        if path.name == name:
            raise OSError('injected write failure: ' + name)
        return real(path, data)
    return write


@pytest.mark.parametrize('point', ['pdf_copy', 'sidecar_copy', 'directory_sync', 'verification', 'rename'])
def test_failures_before_the_rename_leave_the_public_namespace_unchanged(candidates, public, monkeypatch, point):
    (public / 'existing').mkdir()
    (public / 'existing' / 'keep.txt').write_text('keep')
    if point == 'pdf_copy':
        monkeypatch.setattr(publication, '_write', _fail_on('document.pdf'))
    elif point == 'sidecar_copy':  # after the PDF was placed, before the sidecar
        monkeypatch.setattr(publication, '_write', _fail_on('shared-flow.json'))
    elif point == 'directory_sync':  # the staging directory sync, before the rename
        real_sync = publication._sync_directory
        def sync(path):
            if Path(path).name.startswith(publication.STAGING_PREFIX):
                raise OSError('injected staging sync failure')
            return real_sync(path)
        monkeypatch.setattr(publication, '_sync_directory', sync)
    elif point == 'verification':
        monkeypatch.setattr(semantic, 'open_semantic_flow', lambda *a: dict(status='needs_confirmation', reason='injected'))
    else:  # after verification, at the rename itself
        monkeypatch.setattr(os, 'rename', lambda a, b: (_ for _ in ()).throw(OSError('injected rename failure')))
    with pytest.raises((OSError, PdfError), match='injected'):
        publication.publish_semantic_bundle(*pair(candidates['base']), public / 'bundle')
    assert entries(public) == ['existing'] and (public / 'existing' / 'keep.txt').read_text() == 'keep'


def test_cleanup_failure_does_not_hide_the_original_error(candidates, public, monkeypatch):
    monkeypatch.setattr(publication, '_write', _fail_on('shared-flow.json'))
    monkeypatch.setattr(shutil, 'rmtree', lambda path: (_ for _ in ()).throw(OSError('injected cleanup failure')))
    with pytest.raises(OSError, match='injected write failure') as error:
        publication.publish_semantic_bundle(*pair(candidates['base']), public / 'bundle')
    assert any('staging cleanup failed' in note for note in error.value.__notes__)
    # The leftover is private staging only; nothing was published under the bundle name.
    assert not (public / 'bundle').exists()
    assert all(name.startswith(publication.STAGING_PREFIX) for name in entries(public))


# --- after the rename: public reopen is part of success -----------------------------------------------------------

def _second_open_fails(monkeypatch):
    real_open, calls = semantic.open_semantic_flow, []
    def opened(pdf, sidecar):
        calls.append(Path(pdf))
        if len(calls) == 2:
            return dict(status='needs_confirmation', reason='injected public reopen failure')
        return real_open(pdf, sidecar)
    monkeypatch.setattr(semantic, 'open_semantic_flow', opened)
    return calls


def test_public_reopen_failure_withdraws_the_bundle_and_never_reports_success(candidates, public, monkeypatch):
    calls = _second_open_fails(monkeypatch)
    with pytest.raises(PdfError, match='withdrawn') as error:
        publication.publish_semantic_bundle(*pair(candidates['base']), public / 'bundle')
    assert calls[1] == public / 'bundle' / 'document.pdf'  # the public path was actually reopened
    assert not (public / 'bundle').exists()
    quarantine = [n for n in entries(public) if n.startswith(publication.WITHDRAWN_PREFIX + 'bundle-')]
    assert len(quarantine) == 1 and str(public / quarantine[0]) in str(error.value)
    assert entries(public / quarantine[0]) == ['document.pdf', 'shared-flow.json']


def test_public_reopen_failure_that_cannot_be_withdrawn_is_an_explicit_error(candidates, public, monkeypatch):
    _second_open_fails(monkeypatch)
    real_rename, renames = os.rename, []
    def rename(a, b):
        renames.append(b)
        if len(renames) == 2:
            raise OSError('injected withdraw failure')
        return real_rename(a, b)
    monkeypatch.setattr(os, 'rename', rename)
    with pytest.raises(PdfError, match='NOT verified and could not be withdrawn'):
        publication.publish_semantic_bundle(*pair(candidates['base']), public / 'bundle')


def test_public_byte_change_after_rename_is_not_success(candidates, public, monkeypatch):
    real_rename = os.rename
    def rename(a, b):
        real_rename(a, b)
        if Path(b).name == 'bundle':
            sidecar = Path(b) / 'shared-flow.json'
            value = json.loads(sidecar.read_text())
            sidecar.write_text(json.dumps(value, indent=1))  # same JSON meaning, different bytes
    monkeypatch.setattr(os, 'rename', rename)
    with pytest.raises(PdfError, match='withdrawn'):
        publication.publish_semantic_bundle(*pair(candidates['base']), public / 'bundle')
    assert not (public / 'bundle').exists()


# --- directory sync failures after a successful rename ------------------------------------------------------------

def _parent_sync_fails(monkeypatch, parent, events):
    real_sync = publication._sync_directory
    def sync(path):
        events.append(('sync', Path(path)))
        if Path(path) == parent:
            raise OSError('injected parent sync failure')
        return real_sync(path)
    monkeypatch.setattr(publication, '_sync_directory', sync)


def test_parent_sync_failure_after_publication_is_a_late_failure_with_a_verified_public_bundle(candidates, public,
                                                                                                monkeypatch):
    events = []
    real_open = semantic.open_semantic_flow
    monkeypatch.setattr(semantic, 'open_semantic_flow',
                        lambda pdf, sidecar: (events.append(('open', Path(pdf))), real_open(pdf, sidecar))[1])
    _parent_sync_fails(monkeypatch, public, events)
    destination = public / 'bundle'
    with pytest.raises(publication.PublishedSyncError, match='published and verified') as error:
        publication.publish_semantic_bundle(*pair(candidates['size']), destination)
    # The public reopen ran before the parent sync was attempted; the bundle stays public and complete.
    assert events.index(('open', destination / 'document.pdf')) < events.index(('sync', public))
    assert str(destination) in str(error.value)
    result = error.value.result
    assert result['directory'] == destination and result['verification']['status'] == 'restored'
    assert entries(public) == ['bundle'] and entries(destination) == ['document.pdf', 'shared-flow.json']
    assert result['pdf'].read_bytes() == Path(candidates['size']['pdf']).read_bytes()
    assert semantic.open_semantic_flow(result['pdf'], result['sidecar'])['status'] == 'restored'


def test_withdrawal_whose_directory_sync_fails_still_reports_the_quarantine(candidates, public, monkeypatch):
    _second_open_fails(monkeypatch)
    _parent_sync_fails(monkeypatch, public, [])
    with pytest.raises(PdfError, match='was withdrawn to .*directory sync after the withdrawal failed') as error:
        publication.publish_semantic_bundle(*pair(candidates['base']), public / 'bundle')
    assert 'NOT verified' not in str(error.value) and not (public / 'bundle').exists()
    quarantine = [n for n in entries(public) if n.startswith(publication.WITHDRAWN_PREFIX + 'bundle-')]
    assert len(quarantine) == 1 and str(public / quarantine[0]) in str(error.value)
    assert entries(public / quarantine[0]) == ['document.pdf', 'shared-flow.json']
