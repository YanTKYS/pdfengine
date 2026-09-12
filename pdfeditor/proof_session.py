"""Bounded, operation-local reuse of identical PDF evidence, never trust by path."""
from collections import OrderedDict
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import hashlib
from pathlib import Path

from .backend import PdfError


_session=ContextVar('pdfengine_proof_session',default=None)


def revision(source):
    data=source if isinstance(source,bytes) else Path(source).read_bytes()
    return hashlib.sha256(data).hexdigest()


def proof_session(function):
    """Nested calls share evidence; the outermost call always discards it."""
    @wraps(function)
    def run(*args,**kwargs):
        if _session.get() is not None:return function(*args,**kwargs)
        token=_session.set({})
        try:return function(*args,**kwargs)
        finally:_session.reset(token)
    return run


def evidence_cache(key, *, limit):
    """Keys include exact PDF bytes and every semantic input; copies are isolated.

    Small separate LRU limits keep counterfactual render observations bounded.
    Outside a proof session the underlying function runs without caching.
    """
    def decorate(function):
        @wraps(function)
        def read(*args,**kwargs):
            session=_session.get()
            if session is None:return function(*args,**kwargs)
            cache=session.setdefault(function,OrderedDict());ident=key(*args,**kwargs)
            if ident in cache:
                cache.move_to_end(ident)
                return deepcopy(cache[ident])
            value=function(*args,**kwargs)
            if key(*args,**kwargs)!=ident:
                raise PdfError('PDF or proof input changed while collecting evidence')
            cache[ident]=deepcopy(value)
            if len(cache)>limit:cache.popitem(last=False)
            return value
        return read
    return decorate
