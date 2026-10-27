"""Read archived originals only when the current PNG matches a recorded codec."""
from pathlib import Path
import hashlib,json,re
import numpy as np
from PIL import Image
from .codecs import decode_candidate

def restore_original(image,metadata,record_directories):
    """Return verified original PNG bytes without writing any files.

    Handles legacy strict/visual fields, guarded strict/tile, linear, and
    geometryOnly records. Edited or unproved PNGs are never silently restored.
    """
    image=Path(image)
    if not metadata.get('importer',{}).get('sizeGrid'):return image.read_bytes()
    for directory in map(Path,record_directories):
        for item in json.loads((directory/'report.json').read_text())['items']:
            if item.get('sourceMetadata',{}).get('uuid')!=metadata['uuid'] or not item.get('sizeGrid'):continue
            checksum=item.get('sourceSha256','')
            if not re.fullmatch('[0-9a-f]{64}',checksum):continue
            original=directory/'originals'/(checksum+'.png')
            if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest()!=checksum:continue
            candidate=decode_candidate(item)
            if [candidate.margins[k] for k in ('top','right','bottom','left','repeat')]!=metadata['importer']['sizeGrid']:continue
            with Image.open(original) as source,Image.open(image) as current:
                if np.array_equal(candidate.crop(np.asarray(source.convert('RGBA'))),np.asarray(current.convert('RGBA'))):return original.read_bytes()
    raise ValueError(f'No verified original matches the current PNG: {image}')
