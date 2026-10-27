"""Synthetic UI families. No production renderer is used to create fixtures."""
from dataclasses import dataclass
import hashlib

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


STYLES = ("random_rgba", "opaque", "binary_alpha", "soft_alpha", "hidden_rgb",
          "solid", "hollow", "bevel", "double_border", "corner_marks",
          "edge_texture", "alpha_thresholds")
AXES = ("both", "horizontal", "vertical")
SPECIALS = ("blank", "invisible_noise", "solid", "translucent", "tiny", "wide", "tall",
            "spike", "multiple_bands", "tied_bands", "short_band", "gradient_xy",
            "gradient_x", "gradient_y", "checkerboard", "noise", "alpha_noise",
            "rounded", "pill", "shadow", "tab", "center_logo", "corner_ornaments",
            "outer_padding", "internal_gap", "near_rgb", "near_alpha", "alpha_ramp",
            "periodic_center", "frame_hole", "text_header", "radial")
PATTERNS = tuple(f"exact/{style}/{axis}" for style in STYLES for axis in AXES) + SPECIALS


@dataclass
class Case:
    name: str
    seed: int
    image: np.ndarray
    # If supplied, this independent source also defines correct resized artwork.
    compact: np.ndarray | None = None
    grid: dict | None = None
    visual_expected: bool | None = None
    stretch_axes: tuple = ()
    # The entire feature rectangle must stay outside the selected stretch bands.
    protected: tuple | None = None


def reference_render(source, grid, size, linear=False):
    """Render nine independent tiles, without production axis/index helpers."""
    width, height = size
    top, right, bottom, left = (grid[k] for k in ("top", "right", "bottom", "left"))
    sh, sw = source.shape[:2]
    xcuts, ycuts = (0, left, sw-right, sw), (0, top, sh-bottom, sh)
    widths, heights = (left, width-left-right, right), (top, height-top-bottom, bottom)
    if min(widths[1], heights[1], sw-left-right, sh-top-bottom) < 1:
        raise ValueError("reference renderer requires positive centers")
    strips = []
    for row, th in enumerate(heights):
        tiles = []
        for col, tw in enumerate(widths):
            tile = source[ycuts[row]:ycuts[row+1], xcuts[col]:xcuts[col+1]]
            if not th or not tw:
                tiles.append(np.empty((th, tw, 4), dtype=np.uint8))
                continue
            if linear:
                # Resize each of the nine regions independently, x then y.
                # Production linear-center decoding resizes whole strips.
                resized = Image.fromarray(tile)
                if tw != tile.shape[1]:
                    resized = resized.resize((tw, tile.shape[0]), Image.Resampling.BILINEAR)
                if th != tile.shape[0]:
                    resized = resized.resize((tw, th), Image.Resampling.BILINEAR)
                tiles.append(np.array(resized))
                continue
            if grid.get("repeat", 0):
                ys = [i % tile.shape[0] for i in range(th)]
                xs = [i % tile.shape[1] for i in range(tw)]
            else:
                ys = [i*tile.shape[0]//th for i in range(th)]
                xs = [i*tile.shape[1]//tw for i in range(tw)]
            tiles.append(np.take(np.take(tile, ys, axis=0), xs, axis=1))
        strips.append(np.concatenate(tiles, axis=1))
    return np.concatenate(strips, axis=0)


def _exact(style, axis, rng, width, height):
    cap = int(rng.choice([2, 4, 10, max(2, min(width, height)//4)]))
    top, right, bottom, left = map(int, rng.integers(0, cap, 4))
    # Cycle symmetric, asymmetric, zero-sided and extremely thin caps.
    if rng.integers(4) == 0:
        top = right = bottom = left = int(rng.integers(1, 9))
    if axis == "horizontal":
        top = bottom = 0
        left, right = max(1, left), max(1, right)
    if axis == "vertical":
        left = right = 0
        top, bottom = max(1, top), max(1, bottom)
    ch = height if axis == "horizontal" else top+bottom+1
    cw = width if axis == "vertical" else left+right+1
    compact = rng.integers(0, 256, (ch, cw, 4), dtype=np.uint8)
    if style == "opaque":
        compact[..., 3] = 255
    elif style == "binary_alpha":
        compact[..., 3] = rng.choice([0, 255], (ch, cw))
    elif style == "soft_alpha":
        compact[..., 3] = rng.integers(16, 240, (ch, cw))
    elif style == "hidden_rgb":
        compact[..., 3] = rng.choice([0, 0, 0, 128, 255], (ch, cw))
    elif style == "alpha_thresholds":
        compact[..., 3] = rng.choice([0, 1, 7, 8, 9, 127, 254, 255], (ch, cw))
    elif style in ("solid", "hollow", "bevel", "double_border", "corner_marks"):
        compact[:] = [49, 83, 121, 255]
        if style == "hollow":
            compact[1:-1, 1:-1] = 0
        elif style == "bevel":
            compact[0] = compact[:, 0] = [220, 230, 250, 255]
            compact[-1] = compact[:, -1] = [10, 20, 40, 255]
        elif style == "double_border":
            for k in range(min(3, ch//2, cw//2)):
                compact[k:ch-k, k] = compact[k:ch-k, cw-k-1] = [k*70, 180, 255, 255]
                compact[k, k:cw-k] = compact[ch-k-1, k:cw-k] = [k*70, 180, 255, 255]
        elif style == "corner_marks":
            for y in (0, ch-1):
                for x in (0, cw-1):
                    compact[y, x] = rng.integers(0, 256, 4)
    elif style == "edge_texture":
        # Random caps, patterned straight edges, constant center.
        compact[..., 3] = 255
        if axis != "vertical":
            compact[:, left] = np.array([70, 80, 90, 255], dtype=np.uint8)
        if axis != "horizontal":
            compact[top, :] = np.array([70, 80, 90, 255], dtype=np.uint8)
    # Single-axis fixtures keep a unique fixed-axis sentinel in their cap.
    # Otherwise e.g. a flat hollow frame is actually stretchable in BOTH axes,
    # and a full-image scale would not be its intended nine-slice resize oracle.
    if axis == "horizontal":
        compact[:, 0, 0] = np.arange(ch, dtype=np.uint8)
        compact[:, 0, 3] = 255
    elif axis == "vertical":
        compact[0, :, 0] = np.arange(cw, dtype=np.uint8)
        compact[0, :, 3] = 255
    grid = dict(top=top, right=right, bottom=bottom, left=left, repeat=0)
    return compact, grid, reference_render(compact, grid, (width, height))


def make_case(pattern, variant=0, seed=20261027):
    digest = hashlib.blake2b(f"{seed}/{pattern}/{variant}".encode(), digest_size=8).digest()
    case_seed = int.from_bytes(digest, "little")
    rng = np.random.default_rng(case_seed)
    width, height = int(rng.integers(96, 193)), int(rng.integers(72, 145))
    if variant % 32 == 31:
        width, height = ((257, 255), (512, 127), (127, 512), (1024, 256))[(variant//32) % 4]
    name = f"{pattern}/{variant:05d}"
    if pattern.startswith("exact/"):
        _, style, axis = pattern.split("/")
        compact, grid, image = _exact(style, axis, rng, width, height)
        stretch_axes = AXES[1:] if axis == "both" else (axis,)
        # The eight dihedral orientations also exercise non-contiguous arrays.
        for _ in range(variant % 4):
            compact, image = np.rot90(compact), np.rot90(image)
            grid = dict(top=grid['right'], right=grid['bottom'], bottom=grid['left'], left=grid['top'], repeat=0)
            stretch_axes = tuple('vertical' if a == 'horizontal' else 'horizontal' for a in stretch_axes)
        if variant % 8 >= 4:
            compact, image = compact[:, ::-1], image[:, ::-1]
            grid = dict(grid, left=grid['right'], right=grid['left'])
        return Case(name, case_seed, image, compact, grid, None,
                    stretch_axes)

    image = np.zeros((height, width, 4), dtype=np.uint8)
    image[:] = [50, 80, 120, 255]
    expected, protected = None, None
    if pattern == "blank":
        image[:] = 0
        expected = False
    elif pattern == "invisible_noise":
        image = rng.integers(0, 256, image.shape, dtype=np.uint8)
        image[..., 3] = 0
        expected = False
    elif pattern in ("solid", "translucent"):
        image[:] = rng.integers(0, 256, 4)
        image[..., 3] = 255 if pattern == "solid" else int(rng.integers(16, 240))
        expected = True
    elif pattern == "tiny":
        w, h = ((1, 1), (1, 2), (2, 1), (2, 2), (3, 5), (5, 3), (1, 63), (63, 1))[variant % 8]
        image = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    elif pattern in ("wide", "tall"):
        w, h = (int(rng.integers(512, 2049)), int(rng.integers(3, 13)))
        if pattern == "tall":
            w, h = h, w
        axis = "horizontal" if pattern == "wide" else "vertical"
        compact, grid, image = _exact("opaque", axis, rng, w, h)
        # Caps on the fixed axis are zero; small cross-axis dimensions are valid.
        return Case(name, case_seed, image, compact, grid, True, (axis,))
    elif pattern in ("noise", "alpha_noise", "short_band", "multiple_bands", "tied_bands"):
        image = rng.integers(0, 256, image.shape, dtype=np.uint8)
        if pattern == "alpha_noise":
            image[..., :3] = 255
        if pattern == "short_band":
            image[:, 5:7] = image[:, 5:6]
        elif pattern in ("multiple_bands", "tied_bands"):
            image[:, 3:13] = image[:, 3:4]
            end = 36 if pattern == "tied_bands" else width-8
            image[:, 26:end] = image[:, 26:27]
        if pattern in ("noise", "alpha_noise"):
            expected = False
    elif pattern in ("gradient_xy", "gradient_x", "gradient_y", "alpha_ramp", "radial"):
        x, y = np.meshgrid(np.linspace(0, 255, width), np.linspace(0, 255, height))
        if pattern == "radial":
            image[..., 0] = np.clip(np.hypot(x-128, y-128)*1.4, 0, 255)
            image[..., 1] = x
            image[..., 2] = y
        else:
            if pattern != "gradient_y":
                image[..., 0] = x
            if pattern != "gradient_x":
                image[..., 1] = y
            if pattern == "alpha_ramp":
                image[..., 3] = (x+y)/2
        if pattern in ("gradient_xy", "alpha_ramp", "radial"):
            expected = False
    elif pattern in ("checkerboard", "periodic_center"):
        y, x = np.indices((height, width))
        period = 1+variant % 5
        image[..., :3] = (((x//period+y//period) % 2)*255)[..., None]
        if period == 1:
            expected = False
    elif pattern in ("near_rgb", "near_alpha"):
        image[:] = [100, 110, 120, 180]
        channel = 0 if pattern == "near_rgb" else 3
        image[..., channel] += rng.integers(0, 2, (height, width), dtype=np.uint8)
        expected = True
    elif pattern == "spike":
        image = rng.integers(0, 256, image.shape, dtype=np.uint8)
        image[:, 10:width-10] = image[:, 10:11]
        # A one-channel change at an outermost row breaks a whole-column band.
        x = width//2
        image[0, x, variant % 4] ^= 255
    else:
        canvas = Image.new("RGBA", (width*3, height*3))
        draw = ImageDraw.Draw(canvas)
        box = (18, 18, width*3-19, height*3-19)
        if pattern in ("rounded", "pill", "shadow", "tab"):
            radius = (height*3-36)//2 if pattern == "pill" else 24+variant % 15
            draw.rounded_rectangle(box, radius, fill=(70, 110, 170, 255), outline=(230, 240, 255, 255), width=6)
            if pattern == "tab":
                draw.rectangle((width*3//2-12, 3, width*3//2+12, 40), fill=(255, 200, 20, 255))
            if pattern == "shadow":
                shadow = Image.new("RGBA", canvas.size)
                ImageDraw.Draw(shadow).rounded_rectangle((21, 24, width*3-12, height*3-9), 28, fill=(0, 0, 0, 100))
                shadow = shadow.filter(ImageFilter.GaussianBlur(5))
                canvas = Image.alpha_composite(shadow, canvas)
            # Near-circular pills may have no band meeting 20% area saving.
            # Elongated capsules have a guaranteed straight stretch interior.
            expected = (True if max(width, height) >= 1.8*min(width, height) else None) if pattern == 'pill' else True
        elif pattern in ("outer_padding", "internal_gap", "corner_ornaments", "frame_hole"):
            if pattern == "outer_padding":
                patch = rng.integers(8, 256, (12, 14, 4), dtype=np.uint8)
                image[:] = 0
                image[height//2:height//2+12, width//2:width//2+14] = patch
                return Case(name, case_seed, image, visual_expected=False)
            if pattern == "frame_hole":
                draw.rectangle(box, outline=(255, 190, 30, 255), width=9)
            else:
                for x in (18, width*3-45):
                    for y in (18, height*3-45):
                        draw.rectangle((x, y, x+26, y+8), fill=(255, 210, 20, 255))
                        draw.rectangle((x, y, x+8, y+26), fill=(255, 210, 20, 255))
                if pattern == "internal_gap":
                    draw.rectangle((18, 18, 39, height*3-19), fill=(200, 130, 30, 255))
                    draw.rectangle((width*3-40, 18, width*3-19, height*3-19), fill=(200, 130, 30, 255))
            expected = True
        elif pattern in ("center_logo", "text_header"):
            draw.rectangle(box, fill=(30, 60, 90, 255))
            if pattern == "center_logo":
                cx, cy = width*3//2, height*3//2
                draw.ellipse((cx-27, cy-24, cx+27, cy+24), fill=(255, 180, 10, 255))
                # Preserve the opaque logo core. Its antialiased perimeter is
                # allowed to vary within the visual policy's explicit budget.
                protected = (width//2-5, height//2-4, width//2+5, height//2+4)
            else:
                draw.text((30, 27), "TASK 0123 / UI", fill=(255, 255, 255, 255), stroke_width=1)
        else:
            raise ValueError(f"Unknown pattern: {pattern}")
        image = np.array(canvas.resize((width, height), Image.Resampling.LANCZOS))
    # Exercise negative strides, transposes and non-contiguous inputs as well.
    if variant % 4 == 1 and protected is None:
        image = image[:, ::-1]
    elif variant % 4 == 2 and protected is None:
        image = image[::-1]
    return Case(name, case_seed, image, visual_expected=expected, protected=protected)
