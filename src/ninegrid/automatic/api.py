"""Resource-safe automatic optimization with external proof-directory support."""
from pathlib import Path
from contextlib import ExitStack
import json,shutil,tempfile


def optimize_project(project,inputs,*,records_directory=None,mode='visual',workers=1,engine_dir=None,
                     original_project=None,ignore_names=('icon','ico','图标'),allowed_component_types=(),
                     playwright_module=None,progress=print):
    """Optimize one PNG directory in place, only after CPU and native GPU proof.

    Preserves texture UUIDs. No source PSD or exporter dependency is involved.
    records_directory defaults outside assets; failures preserve diagnostics and
    leave source images/scenes unchanged. Existing gridded PNGs are skipped.
    ``original_project`` lets callers validate an isolated export staging area
    against the destination project's engine/atlas configuration.
    """
    from ..engine import engine_libraries
    from ..laya.runtime import project_version,validate_engine_version,engine_options
    from ..parallel import ImagePool
    from ..laya.policy import native_ui_policy
    from .workflow import optimize_automatically
    project=Path(project).resolve();inputs=(project/inputs).resolve()
    original_project=Path(original_project or project).resolve()
    if mode not in {'visual','strict'}:raise ValueError('mode must be visual or strict')
    if not inputs.is_dir() or not inputs.is_relative_to(project/'assets'):raise ValueError('PNG input must be a project assets directory')
    version=project_version(original_project);libraries=engine_libraries(original_project,engine_dir)
    validate_engine_version(version,libraries)
    if not list(project.glob('*.laya')):(project/'Ninegrid.laya').write_text(json.dumps({'version':version})+'\n')
    if project_version(project)!=version:raise ValueError('Staging and destination project versions differ')
    parent=inputs
    while parent.is_relative_to(project/'assets'):
        source=original_project/parent.relative_to(project)
        for config in source.glob('*.atlascfg'):
            target=parent/config.name
            if target.exists() and target.read_bytes()!=config.read_bytes():raise ValueError(f'Atlas configuration differs: {target}')
            if not target.exists():shutil.copy2(config,target)
        parent=parent.parent
    records=Path(records_directory).resolve() if records_directory else project/'.ninegrid/automatic'
    if records.is_relative_to(inputs):
        # Exporters may keep proofs with staged inputs, but never scan proofs.
        if records!=inputs/'export-records/ninegrid':raise ValueError('Proofs must not overlap PNG inputs')
    if records.exists() and any(records.iterdir()):raise ValueError('Choose a new empty proof directory for this run')
    records.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ninegrid-auto-',dir=project) as temporary:
        work=Path(temporary)
        try:
            with engine_options(playwright_module=playwright_module),native_ui_policy(allowed_component_types),ImagePool(workers,progress=progress) as pool:
                return optimize_automatically(project,inputs,original_project,mode,workers,libraries,version,ignore_names,pool,
                    evidence_directory=records,work_directory=work,allowed_component_types=allowed_component_types)
        except BaseException as error:
            if work.exists():shutil.copytree(work,records/'failed-attempts',dirs_exist_ok=True)
            (records/'failure.json').write_text(json.dumps({'valid':False,'error':str(error)},indent=2)+'\n')
            raise
