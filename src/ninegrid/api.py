"""Public Python API for image optimization and LayaAir project workflows.

Image functions accept NumPy uint8 arrays shaped (height, width, 4).
Project functions require an explicit LayaProjectAdapter. Rendering dependencies
are only needed when applying changes or explicitly validating with the engine.
"""
from .automatic import optimize_project, pixel_candidates, decode_candidate, reconstruct_candidate, restore_original
from .candidate import Candidate, choose_candidate
from .crop import png_bytes
from .laya.adapter import LayaProjectAdapter
from .laya.patch import rollback
from .pipeline import apply, build, scan
from .reconstruct import pixel_diff, reconstruct_9slice
from .scan import read_rgba
from .verify import verify_project
from .visual import VisualCandidate, choose_visual_candidate, quality_limits, visual_diff

__all__ = [
    "Candidate", "VisualCandidate", "choose_candidate", "choose_visual_candidate",
    "read_rgba", "png_bytes", "reconstruct_9slice", "pixel_diff", "visual_diff",
    "quality_limits", "LayaProjectAdapter", "scan", "build", "apply",
    "verify_project", "rollback", "optimize_project", "pixel_candidates",
    "decode_candidate", "reconstruct_candidate", "restore_original",
]
