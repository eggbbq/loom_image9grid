"""Version validation and explicit browser dependency configuration."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import json,os,re

_PLAYWRIGHT=ContextVar('ninegrid_playwright',default=None)
SUPPORTED_VERSIONS=frozenset({'3.4.1','3.4.2'})

def project_version(project):
    configs=list(Path(project).glob('*.laya'))
    versions={json.loads(p.read_text())['version'] for p in configs}
    if len(versions)!=1 or not versions.issubset(SUPPORTED_VERSIONS):raise ValueError(f'Unsupported Laya project version: {versions}')
    return next(iter(versions))

def validate_engine_version(version,libraries):
    core=(Path(libraries)/'laya.core.js').read_text()
    match=re.search(r'LayaEnv\.version\s*=\s*[\'"]([^\'"]+)',core)
    if version not in SUPPORTED_VERSIONS or not match or match[1]!=version:
        raise ValueError(f'Laya engine/project version mismatch / 不匹配: {version}, {match[1] if match else "unknown"}')
    return version

@contextmanager
def engine_options(*,playwright_module=None):
    path=str(Path(playwright_module).resolve()) if playwright_module else None
    if path and not Path(path).is_file():raise ValueError(f'Playwright module is missing: {path}')
    token=_PLAYWRIGHT.set(path)
    try:yield
    finally:_PLAYWRIGHT.reset(token)

def engine_environment():
    result=dict(os.environ)
    if _PLAYWRIGHT.get():result['NINEGRID_PLAYWRIGHT_MODULE']=_PLAYWRIGHT.get()
    return result

@contextmanager
def verified_engine(version,libraries):
    validate_engine_version(version,libraries)
    yield
