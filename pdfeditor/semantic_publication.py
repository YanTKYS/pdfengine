"""L3: atomic publication of one verified semantic bundle (PDF + shared-flow-3 sidecar).

Publication is a boundary, not a new authority. It never re-seals, rebinds or
reinterprets the sidecar: the exact candidate bytes are placed in a private
staging directory inside the destination's parent (same filesystem), that
staged pair is opened fresh with `semantic_layout.open_semantic_flow`, and the
complete directory is made public by one directory rename. The published pair
is then reopened from its public path; only `restored` there is success.

Contract (narrow, see docs §22):

* the destination is a new bundle directory; an existing entry is refused and
  never replaced, moved or deleted;
* PDF and sidecar are never public separately: before the rename nothing is in
  the public namespace, after it both fixed children are there together;
* any failure before the rename leaves the public namespace unchanged and
  removes the staging directory (a cleanup failure never hides the cause);
* if the public reopen fails, the bundle is withdrawn by one rename into a
  private quarantine name in the same parent and an error is raised; if that
  rename also fails the error says the public path is not a verified bundle.
  Success is never reported for a bundle that did not reopen.

The caller has exclusive use of the destination parent while publishing; this
is exception atomicity of the public pair on one local filesystem, not
concurrent-publisher locking or power-loss durability (files and directories
are fsynced where the platform allows).
"""
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import tempfile

from .backend import PdfError
from . import semantic_layout as semantic

PDF_NAME = 'document.pdf'
SIDECAR_NAME = 'shared-flow.json'
STAGING_PREFIX = '.pdfengine-staging-'
WITHDRAWN_PREFIX = '.pdfengine-withdrawn-'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _sync_directory(path):
    """Flush a directory entry where the platform supports opening directories (not Windows)."""
    if os.name == 'nt':
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(path, data):
    """Create one staged child (never overwrite), write all bytes, flush and fsync before close."""
    with open(path, 'xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _staged_digests(directory):
    return {name: _sha((directory / name).read_bytes()) for name in (PDF_NAME, SIDECAR_NAME)}


def _destination(destination):
    final = Path(destination)
    parent = final.parent
    if final.name in ('', '.', '..') or final.name.startswith((STAGING_PREFIX, WITHDRAWN_PREFIX)):
        raise PdfError('semantic bundle destination needs a plain new directory name')
    if not parent.is_dir() or parent.is_symlink():
        raise PdfError('semantic bundle destination parent must be an existing, non-symlink directory')
    if os.path.lexists(final):
        raise PdfError('semantic bundle destination already exists; published bundles are never replaced')
    return final, parent


def _withdraw(final, parent):
    """Remove a published-but-unverified bundle from the public name with one rename."""
    quarantine = parent / (WITHDRAWN_PREFIX + final.name + '-' + secrets.token_hex(8))
    os.rename(final, quarantine)
    _sync_directory(parent)
    return quarantine


def publish_semantic_bundle(pdf, sidecar, destination):
    """Publish a verified candidate pair as the new bundle directory `destination`.

    `pdf` and `sidecar` are read once; exactly those bytes are staged, verified
    and published as `destination/document.pdf` and `destination/shared-flow.json`.
    Returns the public paths, the digests of the published bytes and the public
    reopen evidence (`status` is always `restored` on return).
    """
    final, parent = _destination(destination)
    data = {PDF_NAME: Path(pdf).read_bytes(), SIDECAR_NAME: Path(sidecar).read_bytes()}
    expected = {name: _sha(value) for name, value in data.items()}
    staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=parent))
    try:
        for name in (PDF_NAME, SIDECAR_NAME):
            _write(staging / name, data[name])
        _sync_directory(staging)
        staged = semantic.open_semantic_flow(staging / PDF_NAME, staging / SIDECAR_NAME)
        if staged['status'] != 'restored':
            raise PdfError('staged semantic bundle does not verify: ' + staged['reason'])
        if _staged_digests(staging) != expected:
            raise PdfError('staged semantic bundle changed after verification')
        if os.path.lexists(final):
            raise PdfError('semantic bundle destination already exists; published bundles are never replaced')
        os.rename(staging, final)  # the single publication step
    except BaseException as error:
        try:
            shutil.rmtree(staging)
        except OSError as cleanup:
            error.add_note(f'staging cleanup failed, private garbage left at {staging}: {cleanup}')
        raise
    _sync_directory(parent)
    try:
        public = semantic.open_semantic_flow(final / PDF_NAME, final / SIDECAR_NAME)
        if public['status'] != 'restored':
            raise PdfError('published semantic bundle does not reopen: ' + public['reason'])
        if _staged_digests(final) != expected:
            raise PdfError('published semantic bundle bytes differ from the verified staging bytes')
    except Exception as error:
        try:
            quarantine = _withdraw(final, parent)
        except OSError as withdraw:
            raise PdfError(f'published semantic bundle at {final} is NOT verified and could not be withdrawn: '
                           f'{withdraw}') from error
        raise PdfError(f'published semantic bundle did not reopen and was withdrawn to {quarantine}: {error}') from error
    return dict(directory=final, pdf=final / PDF_NAME, sidecar=final / SIDECAR_NAME, sha256=expected,
                verification=dict(status=public['status'], slot_id=public['slot_id'], version=public['version'],
                                  semantic=public['semantic'], authority=public['authority'],
                                  owner=public['owner'], island=public['island']))
