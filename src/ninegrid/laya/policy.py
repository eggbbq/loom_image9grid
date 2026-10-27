"""Scoped official reference-policy selection; no module monkey patches."""
from contextlib import contextmanager
from contextvars import ContextVar

_REFERENCE_POLICY=ContextVar('ninegrid_reference_policy',default=('legacy',()))

def current_reference_policy():return _REFERENCE_POLICY.get()

@contextmanager
def native_ui_policy(allowed_component_types=()):
    token=_REFERENCE_POLICY.set(('native',tuple(allowed_component_types)))
    try:yield
    finally:_REFERENCE_POLICY.reset(token)
