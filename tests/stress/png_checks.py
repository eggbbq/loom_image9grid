"""Exercise supported PNG storage modes and explicit unsupported formats."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

from ninegrid.crop import png_bytes
from ninegrid.scan import read_rgba


PNG_MODES = ('RGBA', 'RGB', 'L', 'LA', 'P', '1', 'I;16', 'APNG')


def check_png(case, index):
    mode = PNG_MODES[index % len(PNG_MODES)]
    # Every case proves raw RGBA preservation, including invisible RGB values.
    with Image.open(BytesIO(png_bytes(case.image))) as decoded:
        assert np.array_equal(np.array(decoded.convert('RGBA')), case.image)
    original = Image.fromarray(case.image)
    if mode in ('RGBA', 'RGB', 'L', 'LA', '1'):
        encoded = original.convert(mode)
    elif mode == 'P':
        height, width = case.image.shape[:2]
        indices = np.indices((height, width)).sum(axis=0).astype(np.uint8)
        encoded = Image.fromarray(indices, mode='P')
        encoded.putpalette([value for i in range(256) for value in (i, 255-i, (i*7) % 256)])
        encoded.info['transparency'] = bytes(range(256))
    elif mode == 'I;16':
        encoded = Image.fromarray(case.image[..., 0].astype(np.uint16)*257)
    else:
        encoded = original.resize((max(2, original.width), max(2, original.height)))
    with TemporaryDirectory(prefix='ninegrid-png-') as temporary:
        path = Path(temporary) / 'fixture.png'
        if mode == 'APNG':
            second = Image.new('RGBA', encoded.size, (255, 0, 0, 255))
            # Avoid accidentally identical frames being collapsed by Pillow.
            second.putpixel((0, 0), (0, 255, 0, 255))
            encoded.save(path, save_all=True, append_images=[second], duration=20, loop=0)
        else:
            encoded.save(path)
        if mode in ('I;16', 'APNG'):
            expected = 'sixteen_bit_png' if mode == 'I;16' else 'animated_png'
            try:
                read_rgba(path)
            except ValueError as error:
                assert str(error) == expected
            else:
                raise AssertionError(f'{mode} was silently accepted')
            return dict(valid=True, storageMode=mode, rejected=True)
        rgba = read_rgba(path)
        assert rgba.dtype == np.uint8
        assert np.array_equal(rgba, np.array(encoded.convert('RGBA'))), f'{mode} decoding mismatch'
    return dict(valid=True, storageMode=mode, rejected=False)
