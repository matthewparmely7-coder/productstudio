"""ProductStudio engine: segmentation, cutouts, backdrops, shadows and watermarks.

This module has no Qt imports, so it can be tested without a window.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

LANCZOS = Image.Resampling.LANCZOS


def asset_path(name: str) -> Path:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return root / name


# ---------------------------------------------------------------- segmentation

class Segmenter:
    """Runs the bundled IS-Net model on the CPU and returns an alpha mask."""

    def __init__(self):
        import onnxruntime as ort
        model = asset_path('models/isnet-general-use.onnx')
        if not model.exists():
            raise FileNotFoundError(f'The model file is missing: {model}. Run download_model.py once to fetch it.')
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = min(4, os.cpu_count() or 1)
        self.session = ort.InferenceSession(str(model), sess_options=opts, providers=['CPUExecutionProvider'])

    def mask(self, image: Image.Image) -> Image.Image:
        img = image.convert('RGB').resize((1024, 1024), LANCZOS)
        pixels = np.asarray(img, dtype=np.float32)
        pixels = pixels / max(float(pixels.max()), 1e-6) - 0.5
        tensor = np.ascontiguousarray(pixels.transpose(2, 0, 1)[None, ...], dtype=np.float32)
        result = self.session.run(None, {self.session.get_inputs()[0].name: tensor})[0][0, 0]
        lo, hi = float(result.min()), float(result.max())
        if not np.isfinite(result).all() or hi - lo < 1e-8:
            raise ValueError('The model could not produce a usable mask for this photo.')
        mask = Image.fromarray(((result - lo) * (255 / (hi - lo))).clip(0, 255).astype('uint8'))
        return mask.resize(image.size, LANCZOS)


def load_image(path: str) -> Image.Image:
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert('RGBA')


# -------------------------------------------------------------------- settings

# The backdrops a new installation starts with: 5 solid colours, 3 linear and 3 radial gradients.
# The user can add to this list and remove from it. A backdrop is a small dict:
#   {'type': 'solid', 'color': '#RRGGBB'}
#   {'type': 'linear', 'colors': [start, end], 'angle': degrees}   (180 runs top to bottom, 90 left to right)
#   {'type': 'radial', 'colors': [centre, edge]}
#   {'type': 'image'}                                              (the photo carries the picture itself)
DEFAULT_BACKDROPS = [
    {'name': 'Paper', 'type': 'solid', 'color': '#F1F0ED'},
    {'name': 'White', 'type': 'solid', 'color': '#FFFFFF'},
    {'name': 'Ink', 'type': 'solid', 'color': '#1B1B1F'},
    {'name': 'Coral', 'type': 'solid', 'color': '#FF8A6B'},
    {'name': 'Sky', 'type': 'solid', 'color': '#8EC5FF'},
    {'name': 'Sunset', 'type': 'linear', 'colors': ['#FFB88C', '#FF6F91'], 'angle': 160},
    {'name': 'Lagoon', 'type': 'linear', 'colors': ['#9FE3C1', '#8EC5FF'], 'angle': 135},
    {'name': 'Dusk', 'type': 'linear', 'colors': ['#3A3F5C', '#15161D'], 'angle': 180},
    {'name': 'Sand studio', 'type': 'radial', 'colors': ['#F6ECDD', '#D8C2A2']},
    {'name': 'Night studio', 'type': 'radial', 'colors': ['#46536B', '#161A23']},
    {'name': 'Blush studio', 'type': 'radial', 'colors': ['#FFE9F0', '#F4A9C4']},
]
DEFAULT_SHAPES = [
    {'name': 'Square', 'w': 1600, 'h': 1600},
    {'name': 'Portrait', 'w': 1200, 'h': 1600},
    {'name': 'Landscape', 'w': 1600, 'h': 1200},
]


def same_backdrop(a: dict | None, b: dict | None) -> bool:
    """Two backdrops are the same when they look the same. The name does not matter."""
    strip = lambda d: {k: v for k, v in (d or {}).items() if k != 'name'}
    return strip(a) == strip(b)


def base_color(spec: dict) -> str:
    return spec.get('color') or (spec.get('colors') or ['#F1F0ED'])[0]


SHADOWS = ('none', 'soft', 'floor', 'hard')
LAYOUTS = ('single', 'center', 'diagonal', 'grid', 'brick', 'band')
LOOKS = ('solid', 'outline', 'shadow', 'emboss', 'label')


@dataclass
class Settings:
    """Everything shared by all photos: canvas, backdrop, shadow and watermark."""
    width: int = 1600
    height: int = 1600
    backdrop: dict = field(default_factory=lambda: {'type': 'solid', 'color': '#F1F0ED'})
    size_mode: str = 'fixed'         # 'original' means the canvas follows the photo's own size
    background_mode: str = 'Cover'   # how a backdrop image fits: Cover or Contain
    feather: int = 0
    shadow: str = 'soft'
    shadow_strength: int = 100       # percent of the style's normal darkness, 0 to 200
    shadow_angle: int = 0            # degrees: 0 falls straight down, positive swings to the right
    mark: dict | None = None         # the watermark spec, or None for no watermark
    layout: str = 'single'
    look: str = 'solid'
    mark_anchor: str = 'br'          # where a single mark sits: t/m/b for the row, l/c/r for the column
    mark_margin: int = 4             # percent of the shorter side kept free at the edge
    mark_color: str | None = None    # None picks white or dark to suit the picture
    mark_size: int = 5               # text height, as a percent of the shorter canvas side
    mark_opacity: int = 35
    mark_angle: int = -25
    mark_spacing: int = 40


@dataclass
class Placement:
    """Where one product sits on the canvas."""
    scale: float = 80.0   # percent of the canvas the product may fill
    x: float = 0.0        # -100..100, percent of half the canvas width
    y: float = 0.0
    flip: bool = False


# ----------------------------------------------------------------------- fonts

_WIN_FONTS: dict | None = None
_FONT_CACHE: dict = {}
SCRIPT_FONTS = ('Segoe Script', 'Ink Free', 'Lucida Handwriting', 'Brush Script MT')


def _win_fonts() -> dict:
    """Map lower-case font names ('arial bold') to (file, face index) from the registry."""
    global _WIN_FONTS
    if _WIN_FONTS is not None:
        return _WIN_FONTS
    table: dict = {}
    try:
        import winreg
        fonts_dir = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(key)[1]):
                label, filename, _ = winreg.EnumValue(key, i)
                filename = str(filename)
                if not filename.lower().endswith(('.ttf', '.otf', '.ttc')):
                    continue
                path = filename if os.path.isabs(filename) else os.path.join(fonts_dir, filename)
                label = label.rsplit(' (', 1)[0]
                # A .ttc collection lists its faces as "Name A & Name B".
                for index, name in enumerate(label.split(' & ')):
                    table.setdefault(name.strip().lower(), (path, index))
    except ImportError:
        pass
    _WIN_FONTS = table
    return table


def get_font(family: str, size: float, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Find the exact installed face for a family. Falls back to Arial, then Pillow's default."""
    size = max(1, int(size))
    key = (family, bold, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font = None
    table = _win_fonts()
    names = ([f'{family} bold'] if bold else []) + [family]
    for name in names:
        hit = table.get(name.lower())
        if hit:
            try:
                font = ImageFont.truetype(hit[0], size, index=hit[1])
                break
            except OSError:
                continue
    if font is None:
        fallbacks = ('arialbd.ttf', 'DejaVuSans-Bold.ttf') if bold else ('arial.ttf', 'DejaVuSans.ttf')
        for name in fallbacks:
            try:
                font = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
    if font is None:
        font = ImageFont.load_default(size=size)
    _FONT_CACHE[key] = font
    return font


def script_font(size: float) -> ImageFont.FreeTypeFont:
    table = _win_fonts()
    for family in SCRIPT_FONTS:
        if family.lower() in table:
            return get_font(family, size)
    for name in ('ariali.ttf', 'DejaVuSans-Oblique.ttf'):
        try:
            return ImageFont.truetype(name, max(1, int(size)))
        except OSError:
            continue
    return get_font('Arial', size)


# ------------------------------------------------------------- cutout + canvas

def feathered(mask: Image.Image, feather: float = 0) -> Image.Image:
    """Soften the mask edge inward, so no halo of the old background comes back."""
    alpha = mask.convert('L')
    if feather > 0:
        # Blur, then keep only the inner half of the blurred edge.
        blurred = alpha.filter(ImageFilter.GaussianBlur(feather))
        alpha = blurred.point(lambda v: max(0, min(255, (v - 128) * 2)))
    return alpha


def solid_box(alpha: Image.Image):
    """The box round the real object.

    A plain bounding box also counts faint haze and stray specks the model leaves behind,
    which makes the object sit off-centre in its frame. Here a pixel counts only when it is
    clearly visible, and a row or column counts only when it holds more than a speck.
    """
    solid = np.asarray(alpha) > 40
    if not solid.any():
        return alpha.getbbox()
    h, w = solid.shape
    cols = np.flatnonzero(solid.sum(axis=0) >= max(2, round(h * 0.004)))
    rows = np.flatnonzero(solid.sum(axis=1) >= max(2, round(w * 0.004)))
    if len(cols) == 0 or len(rows) == 0:
        return alpha.getbbox()
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def cutout(original: Image.Image, mask: Image.Image, feather: float = 0) -> Image.Image | None:
    """Return the product as an RGBA image cropped to its mask, or None if the mask is empty."""
    alpha = feathered(mask, feather)
    box = solid_box(alpha)
    if not box:
        return None
    product = original.convert('RGBA').copy()
    product.putalpha(alpha)
    return product.crop(box)


def _radial(w: int, h: int, inner: str, outer: str) -> Image.Image:
    sw, sh = max(2, min(w, 256)), max(2, min(h, 256))
    yy, xx = np.mgrid[0:sh, 0:sw]
    t = np.clip(np.hypot(xx / sw - 0.5, yy / sh - 0.38) / 0.75, 0, 1)[..., None]
    a = np.array(ImageColor.getrgb(inner), dtype=np.float32)
    b = np.array(ImageColor.getrgb(outer), dtype=np.float32)
    small = Image.fromarray((a * (1 - t) + b * t).astype('uint8'), 'RGB')
    return small.resize((w, h), Image.Resampling.BICUBIC).convert('RGBA')


def _linear(w: int, h: int, first: str, second: str, angle: float) -> Image.Image:
    side = 128
    yy, xx = np.mgrid[0:side, 0:side] / (side - 1) - 0.5
    a = np.radians(angle)
    dx, dy = np.sin(a), -np.cos(a)
    t = np.clip((xx * dx + yy * dy) / (abs(dx) + abs(dy)) + 0.5, 0, 1)[..., None]
    c1 = np.array(ImageColor.getrgb(first), dtype=np.float32)
    c2 = np.array(ImageColor.getrgb(second), dtype=np.float32)
    small = Image.fromarray((c1 * (1 - t) + c2 * t).astype('uint8'), 'RGB')
    return small.resize((w, h), Image.Resampling.BICUBIC).convert('RGBA')


def make_backdrop(s: Settings, w: int, h: int, background: Image.Image | None = None) -> Image.Image:
    spec = s.backdrop or {}
    kind = spec.get('type', 'solid')
    if kind == 'image' and background is not None:
        if s.background_mode == 'Contain':
            canvas = Image.new('RGBA', (w, h), '#F1F0ED')
            bg = ImageOps.contain(background, (w, h), method=LANCZOS).convert('RGBA')
            canvas.alpha_composite(bg, ((w - bg.width) // 2, (h - bg.height) // 2))
            return canvas
        return ImageOps.fit(background, (w, h), method=LANCZOS).convert('RGBA')
    colors = spec.get('colors') or []
    if kind == 'linear' and len(colors) >= 2:
        return _linear(w, h, colors[0], colors[1], spec.get('angle', 180))
    if kind == 'radial' and len(colors) >= 2:
        return _radial(w, h, colors[0], colors[1])
    return Image.new('RGBA', (w, h), base_color(spec))


def paste_rgba(canvas: Image.Image, im: Image.Image, x: int, y: int) -> None:
    """Alpha-composite `im` at (x, y), clipping whatever falls outside the canvas."""
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + im.width, canvas.width), min(y + im.height, canvas.height)
    if x1 <= x0 or y1 <= y0:
        return
    canvas.alpha_composite(im, (x0, y0), (x0 - x, y0 - y, x1 - x, y1 - y))


def product_box(size: tuple, p: Placement, w: int, h: int) -> tuple:
    """Where a product of `size` lands on a w x h canvas: (x, y, width, height)."""
    pw, ph = size
    factor = min(w * p.scale / 100 / pw, h * p.scale / 100 / ph)
    nw, nh = max(1, round(pw * factor)), max(1, round(ph * factor))
    x = round((w - nw) / 2 + p.x * w / 200)
    y = round((h - nh) / 2 + p.y * h / 200)
    return x, y, nw, nh


def _shadow(canvas: Image.Image, product: Image.Image, x: int, y: int, s: Settings) -> None:
    """Each style sets how far, how blurred and how dark. The user adds strength and direction."""
    w, h = canvas.size
    #           distance  blur    darkness  own angle
    recipe = {
        'soft': (0.035, 0.022, 0.30, 0),
        'floor': (0.012, 0.006, 0.45, 0),
        'hard': (0.030, 0.0, 0.22, 45),
    }.get(s.shadow)
    if not recipe:
        return
    distance, blur, darkness, own = recipe
    strength = max(0.0, min(1.0, darkness * s.shadow_strength / 100))
    if strength <= 0:
        return
    angle = np.radians(own + s.shadow_angle)
    short = min(w, h)
    dx, dy = round(short * distance * np.sin(angle)), round(short * distance * np.cos(angle))
    alpha = product.getchannel('A').point(lambda v: round(v * strength))
    layer = Image.new('L', (w, h), 0)
    layer.paste(alpha, (x + dx, y + dy))
    if blur:
        layer = layer.filter(ImageFilter.GaussianBlur(max(1, short * blur)))
    shadow = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    shadow.putalpha(layer)
    canvas.alpha_composite(shadow)


def render(product: Image.Image | None, s: Settings, p: Placement, background: Image.Image | None = None,
           max_side: int | None = None, transparent: bool = False) -> Image.Image:
    """Compose the final picture. `product` is the cropped RGBA cutout from cutout()."""
    w, h = s.width, s.height
    ratio = min(1.0, max_side / max(w, h)) if max_side else 1.0
    w, h = max(1, round(w * ratio)), max(1, round(h * ratio))
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0)) if transparent else make_backdrop(s, w, h, background)
    if product is not None:
        x, y, nw, nh = product_box(product.size, p, w, h)
        item = product.resize((nw, nh), LANCZOS)
        if p.flip:
            item = ImageOps.mirror(item)
        if not transparent:
            _shadow(canvas, item, x, y, s)
        paste_rgba(canvas, item, x, y)
    if s.mark and not transparent:
        draw_marks(canvas, s)
    return canvas


# ------------------------------------------------------------------ watermarks
#
# A watermark is a small dict, for example:
#   {'kind': 'badge', 'text': 'Oak & Pine', 'tagline': 'Handmade', 'handle': '@oakandpine'}
# `kind` names one of the templates below. Optional keys: font, tracking, case, icon, path, color.

_IMAGE_CACHE: dict = {}
_FACE_CACHE: dict = {}

# style name -> (bold, regular, light) files in assets/fonts, then Windows fonts to fall back on.
# All bundled fonts are open source (SIL Open Font License). Their licences sit next to them.
FONT_STYLES = {
    'Poppins': (('Poppins-Bold.ttf', 'Poppins-Medium.ttf', 'Poppins-Light.ttf'), ('Segoe UI',)),
    'Bebas Neue': (('BebasNeue-Regular.ttf',) * 3, ('Bahnschrift', 'Impact')),
    'DM Serif Display': (('DMSerifDisplay-Regular.ttf',) * 3, ('Georgia',)),
    'Great Vibes': (('GreatVibes-Regular.ttf',) * 3, ('Segoe Script',)),
    'Sacramento': (('Sacramento-Regular.ttf',) * 3, ('Ink Free', 'Segoe Script')),
    'Space Mono': (('SpaceMono-Regular.ttf',) * 3, ('Consolas',)),
}
FONT_LABELS = {'Poppins': 'Modern', 'Bebas Neue': 'Condensed', 'DM Serif Display': 'Classic',
               'Great Vibes': 'Script', 'Sacramento': 'Handwritten', 'Space Mono': 'Mono'}
ICONS = ('none', 'star', 'heart', 'leaf', 'crown', 'diamond', 'bolt', 'sun')

# id, name shown to the user, group
TEMPLATES = [
    ('wordmark', 'Wordmark', 'Text'), ('thin', 'Airy', 'Text'), ('serif', 'Classic', 'Text'),
    ('stack', 'Stacked', 'Text'), ('lines', 'Ruled', 'Text'), ('copyright', 'Copyright', 'Text'),
    ('handle', 'Handle', 'Text'), ('brackets', 'Brackets', 'Text'),
    ('signature', 'Signature', 'Script'), ('autograph', 'Autograph', 'Script'),
    ('mono_circle', 'Circle', 'Monogram'), ('mono_square', 'Square', 'Monogram'),
    ('mono_diamond', 'Diamond', 'Monogram'), ('mono_split', 'Split', 'Monogram'),
    ('badge', 'Badge', 'Badge'), ('crest', 'Crest', 'Badge'), ('seal', 'Seal', 'Badge'),
    ('hexagon', 'Hexagon', 'Badge'), ('shield', 'Shield', 'Badge'),
    ('pill', 'Pill', 'Label'), ('box', 'Framed', 'Label'), ('tag', 'Tag', 'Label'), ('ribbon', 'Ribbon', 'Label'),
    ('logotext', 'Logo + name', 'Logo'), ('logostack', 'Logo over name', 'Logo'), ('image', 'Logo only', 'Logo'),
]
TEMPLATE_IDS = tuple(t[0] for t in TEMPLATES)
OLD_KINDS = {'text': 'wordmark', 'stamp': 'badge', 'monogram': 'mono_square'}   # names used by earlier versions
MARK_KINDS = TEMPLATE_IDS + tuple(OLD_KINDS)
DEFAULT_KIT = {'name': 'Your Brand', 'tagline': 'Handmade goods', 'handle': '@yourbrand', 'logo': None}


def template_spec(kind: str, kit: dict) -> dict:
    """A watermark made from a template and the user's brand kit."""
    name = next((n for i, n, _g in TEMPLATES if i == kind), 'Watermark')
    return {'kind': kind, 'name': name, 'text': kit.get('name') or 'Your Brand', 'tagline': kit.get('tagline') or '',
            'handle': kit.get('handle') or '', 'path': kit.get('logo'), 'from_kit': True}


def face(style: str, size: float, weight: str = 'bold'):
    """A font by style name. Bundled fonts first, then a similar Windows font, then any installed family."""
    size = max(2, int(size))
    key = (style, size, weight)
    if key in _FACE_CACHE:
        return _FACE_CACHE[key]
    font = None
    entry = FONT_STYLES.get(style)
    if entry:
        try:
            font = ImageFont.truetype(str(asset_path('assets/fonts/' + entry[0][('bold', 'regular', 'light').index(weight)])), size)
        except OSError:
            table = _win_fonts()
            family = next((f for f in entry[1] if f.lower() in table), entry[1][0])
            font = get_font(family, size, weight == 'bold')
    if font is None:
        font = get_font(style, size, weight == 'bold')
    _FACE_CACHE[key] = font
    return font


# ---- small drawing helpers. Every helper returns a greyscale picture: white is ink.

def _text_mask(text: str, font, pad: int = 2) -> Image.Image:
    box = font.getbbox(text or ' ')
    w, h = max(1, box[2] - box[0]), max(1, box[3] - box[1])
    im = Image.new('L', (w + 2 * pad, h + 2 * pad), 0)
    ImageDraw.Draw(im).text((pad - box[0], pad - box[1]), text, font=font, fill=255)
    return im


def T(text: str, style: str, size: float, tracking: float = 0.0, weight: str = 'bold') -> Image.Image:
    """One line of text. `tracking` adds space between letters, as a share of the font size."""
    font = face(style, size, weight)
    text = text or ' '
    if tracking <= 0.001:
        return _text_mask(text, font)
    gap = size * tracking
    widths = [font.getlength(c) for c in text]
    ascent, descent = font.getmetrics()
    im = Image.new('L', (int(sum(widths) + gap * (len(text) - 1)) + 8, ascent + descent + 8), 0)
    draw, x = ImageDraw.Draw(im), 4.0
    for c, w in zip(text, widths):
        draw.text((x, 4), c, font=font, fill=255)
        x += w + gap
    return im.crop(im.getbbox() or (0, 0, 1, 1))


def rule(width: float, thickness: float) -> Image.Image:
    return Image.new('L', (max(1, round(width)), max(1, round(thickness))), 255)


def vstack(parts: list, gap: float) -> Image.Image:
    gap = round(gap)
    im = Image.new('L', (max(p.width for p in parts), sum(p.height for p in parts) + gap * (len(parts) - 1)), 0)
    y = 0
    for p in parts:
        im.paste(p, ((im.width - p.width) // 2, y))
        y += p.height + gap
    return im


def hstack(parts: list, gap: float) -> Image.Image:
    gap = round(gap)
    im = Image.new('L', (sum(p.width for p in parts) + gap * (len(parts) - 1), max(p.height for p in parts)), 0)
    x = 0
    for p in parts:
        im.paste(p, (x, (im.height - p.height) // 2))
        x += p.width + gap
    return im


def framed(part: Image.Image, pad_x: float, pad_y: float, line: float, radius: float = 0, double: bool = False,
           solid: bool = False) -> Image.Image:
    """Put a border round a part. `solid` fills the box and cuts the part out of it."""
    pad_x, pad_y, line = round(pad_x), round(pad_y), max(1, round(line))
    w, h = part.width + 2 * pad_x, part.height + 2 * pad_y
    im = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(im)
    radius = min(round(radius), h // 2)
    if solid:
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
        im.paste(0, (pad_x, pad_y), part)
        return im
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, outline=255, width=line)
    if double:
        inset = line * 3
        draw.rounded_rectangle((inset, inset, w - 1 - inset, h - 1 - inset), radius=max(0, radius - inset),
                               outline=255, width=max(1, line // 2))
    im.paste(255, (pad_x, pad_y), part)
    return im


def _center(mask: Image.Image, part: Image.Image, cx: float, cy: float, cut: bool = False) -> None:
    mask.paste(0 if cut else 255, (round(cx - part.width / 2), round(cy - part.height / 2)), part)


def arc_text(canvas: Image.Image, text: str, style: str, size: float, cx: float, cy: float, radius: float,
             tracking: float = 0.1, weight: str = 'bold', bottom: bool = False) -> None:
    """Write text along a circle. Top text reads over the arc, bottom text reads under it."""
    font = face(style, size, weight)
    gap = size * tracking
    widths = [font.getlength(c) for c in text]
    total = sum(widths) + gap * (len(text) - 1)
    cell = int(size * 2.2)
    angle = -total / radius / 2
    for c, w in zip(text, widths):
        a = angle + w / 2 / radius
        glyph = Image.new('L', (cell, cell), 0)
        ImageDraw.Draw(glyph).text((cell / 2, cell / 2 + size * 0.36), c, font=font, fill=255, anchor='ms')
        if bottom:
            glyph = glyph.rotate(np.degrees(a), resample=Image.Resampling.BICUBIC)
            px, py = cx + radius * np.sin(a), cy + radius * np.cos(a)
        else:
            glyph = glyph.rotate(-np.degrees(a), resample=Image.Resampling.BICUBIC)
            px, py = cx + radius * np.sin(a), cy - radius * np.cos(a)
        canvas.paste(255, (round(px - cell / 2), round(py - cell / 2)), glyph)
        angle += (w + gap) / radius


def draw_icon(canvas: Image.Image, name: str, cx: float, cy: float, r: float) -> None:
    """A small solid symbol centred at (cx, cy) with radius r."""
    d = ImageDraw.Draw(canvas)
    pts = lambda pairs: [(cx + x * r, cy + y * r) for x, y in pairs]
    if name == 'star':
        ring = [(np.sin(i * np.pi / 5) * (1 if i % 2 == 0 else 0.42), -np.cos(i * np.pi / 5) * (1 if i % 2 == 0 else 0.42))
                for i in range(10)]
        d.polygon(pts(ring), fill=255)
    elif name == 'heart':
        d.ellipse((cx - r, cy - r * 0.85, cx, cy + r * 0.15), fill=255)
        d.ellipse((cx, cy - r * 0.85, cx + r, cy + r * 0.15), fill=255)
        d.polygon(pts([(-0.95, -0.15), (0.95, -0.15), (0, 1)]), fill=255)
    elif name == 'leaf':
        d.chord((cx - r * 1.5, cy - r, cx + r * 0.5, cy + r), -62, 62, fill=255)
        d.chord((cx - r * 0.5, cy - r, cx + r * 1.5, cy + r), 118, 242, fill=255)
    elif name == 'crown':
        d.polygon(pts([(-1, 0.7), (-1, -0.5), (-0.5, 0.05), (0, -0.8), (0.5, 0.05), (1, -0.5), (1, 0.7)]), fill=255)
    elif name == 'diamond':
        d.polygon(pts([(0, -1), (0.75, 0), (0, 1), (-0.75, 0)]), fill=255)
    elif name == 'bolt':
        d.polygon(pts([(0.25, -1), (-0.65, 0.15), (-0.05, 0.15), (-0.3, 1), (0.65, -0.2), (0.05, -0.2)]), fill=255)
    elif name == 'sun':
        d.ellipse((cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5), fill=255)
        for i in range(8):
            a = i * np.pi / 4
            d.line((cx + np.cos(a) * r * 0.7, cy + np.sin(a) * r * 0.7, cx + np.cos(a) * r, cy + np.sin(a) * r),
                   fill=255, width=max(1, round(r * 0.16)))
    elif name == 'dot':
        d.ellipse((cx - r * 0.45, cy - r * 0.45, cx + r * 0.45, cy + r * 0.45), fill=255)


def _ring(canvas: Image.Image, cx: float, cy: float, r: float, width: float) -> None:
    ImageDraw.Draw(canvas).ellipse((cx - r, cy - r, cx + r, cy + r), outline=255, width=max(1, round(width)))


class _Words:
    """The text of one watermark, with the user's own choices applied over a template's defaults."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.text = (spec.get('text') or '').strip() or 'Your Brand'
        self.tagline = (spec.get('tagline') or '').strip()
        self.handle = (spec.get('handle') or '').strip() or '@' + self.text.lower().replace(' ', '')

    def main(self, case: str = 'upper') -> str:
        case = self.spec.get('case') or case
        return {'upper': self.text.upper(), 'lower': self.text.lower(), 'title': self.text.title()}.get(case, self.text)

    def sub(self) -> str:
        return self.tagline.upper()

    def font(self, default: str) -> str:
        return self.spec.get('font') or default

    def track(self, default: float) -> float:
        value = self.spec.get('tracking')
        return default if value is None else value / 100

    def icon(self, default: str) -> str:
        return self.spec.get('icon') or default

    def initials(self, count: int = 2) -> str:
        return ''.join(word[0] for word in self.text.replace('&', ' ').split())[:count].upper() or 'YB'


def _fit(make, max_width: float, size: float) -> Image.Image:
    """Call make(size) and shrink the size until the result fits in max_width."""
    part = make(size)
    while part.width > max_width and size > 6:
        size *= max_width / part.width * 0.98
        part = make(size)
    return part


# ---- the templates. `s` is the watermark's words, `u` is the base text height in pixels.

def _t_wordmark(s, u):
    return T(s.main(), s.font('Poppins'), u, s.track(0.2), 'bold')


def _t_thin(s, u):
    return T(s.main(), s.font('Poppins'), u * 0.9, s.track(0.5), 'light')


def _t_serif(s, u):
    name = T(s.main('title'), s.font('DM Serif Display'), u * 1.35, s.track(0.0))
    return vstack([name, T(s.sub(), 'Poppins', u * 0.3, 0.45, 'regular')], u * 0.28) if s.tagline else name


def _t_stack(s, u):
    name = T(s.main(), s.font('Bebas Neue'), u * 1.7, s.track(0.08))
    if not s.tagline:
        return name
    small = _fit(lambda size: T(s.sub(), 'Poppins', size, 0.4, 'regular'), name.width, u * 0.32)
    return vstack([name, rule(name.width, u * 0.045), small], u * 0.2)


def _t_lines(s, u):
    name = T(s.main(), s.font('Poppins'), u * 0.9, s.track(0.38), 'regular')
    line = rule(name.width + u * 0.8, u * 0.035)
    return vstack([line, name, line], u * 0.34)


def _t_copyright(s, u):
    return T('© ' + s.main('asis'), s.font('Poppins'), u, s.track(0.03), 'regular')


def _t_handle(s, u):
    return T(s.handle, s.font('Space Mono'), u * 0.95, s.track(0.0))


def _t_brackets(s, u):
    name = T(s.main(), s.font('Poppins'), u * 0.85, s.track(0.3), 'regular')
    pad, arm, line = round(u * 0.5), round(u * 0.42), max(1, round(u * 0.05))
    im = Image.new('L', (name.width + 2 * pad, name.height + 2 * pad), 0)
    d, w, h = ImageDraw.Draw(im), name.width + 2 * pad, name.height + 2 * pad
    for x, y, dx, dy in ((0, 0, 1, 1), (w - 1, 0, -1, 1), (0, h - 1, 1, -1), (w - 1, h - 1, -1, -1)):
        d.line((x, y, x + dx * arm, y), fill=255, width=line)
        d.line((x, y, x, y + dy * arm), fill=255, width=line)
    im.paste(255, (pad, pad), name)
    return im


def _t_signature(s, u):
    return T(s.main('asis'), s.font('Great Vibes'), u * 2.0)


def _t_autograph(s, u):
    name = T(s.main('asis'), s.font('Sacramento'), u * 2.1)
    parts = [name, rule(name.width * 0.72, u * 0.035)]
    if s.tagline:
        parts.append(_fit(lambda size: T(s.sub(), 'Poppins', size, 0.45, 'regular'), name.width, u * 0.28))
    return vstack(parts, u * 0.16)


def _t_mono_circle(s, u):
    d = round(u * 2.6)
    im = Image.new('L', (d, d), 0)
    _ring(im, d / 2, d / 2, d / 2 - 1, u * 0.09)
    _center(im, _fit(lambda size: T(s.initials(), s.font('DM Serif Display'), size), d * 0.58, u * 1.25), d / 2, d / 2)
    return im


def _t_mono_square(s, u):
    letters = _fit(lambda size: T(s.initials(), s.font('Poppins'), size, 0.06), u * 1.5, u * 1.05)
    side = round(u * 2.3)
    im = Image.new('L', (side, side), 0)
    ImageDraw.Draw(im).rounded_rectangle((0, 0, side - 1, side - 1), radius=side // 5, outline=255, width=max(1, round(u * 0.1)))
    _center(im, letters, side / 2, side / 2)
    return im


def _t_mono_diamond(s, u):
    side = round(u * 3.0)
    im = Image.new('L', (side, side), 0)
    c, r = side / 2, side / 2 - 1
    ImageDraw.Draw(im).polygon([(c, c - r), (c + r, c), (c, c + r), (c - r, c)], outline=255, width=max(1, round(u * 0.08)))
    _center(im, _fit(lambda size: T(s.initials(), s.font('Bebas Neue'), size, 0.08), side * 0.46, u * 1.2), c, c)
    return im


def _t_mono_split(s, u):
    letters = s.initials()
    left = T(letters[0], s.font('DM Serif Display'), u * 2.0)
    right = T(letters[-1], s.font('DM Serif Display'), u * 2.0)
    bar = rule(u * 0.05, max(left.height, right.height) * 1.25)
    top = hstack([left, bar, right], u * 0.32)
    name = _fit(lambda size: T(s.main(), 'Poppins', size, 0.4, 'regular'), top.width * 1.6, u * 0.3)
    return vstack([top, name], u * 0.3)


def _t_badge(s, u):
    d = round(u * 4.4)
    im = Image.new('L', (d, d), 0)
    c = d / 2
    _ring(im, c, c, c - 1, u * 0.1)
    _ring(im, c, c, c - u * 0.26, u * 0.03)
    name = s.main()
    size = min(u * 0.52, (c - u * 0.75) * 2.6 / max(len(name), 6))
    arc_text(im, name, s.font('Poppins'), size, c, c, c - u * 0.78, s.track(0.16))
    if s.tagline:
        arc_text(im, s.sub(), 'Poppins', min(u * 0.3, size * 0.7), c, c, c - u * 0.72, 0.3, 'regular', bottom=True)
    for side in (-1, 1):
        draw_icon(im, 'dot', c + side * (c - u * 0.76), c, u * 0.14)
    draw_icon(im, s.icon('star'), c, c, u * 0.62)
    return im


def _t_crest(s, u):
    d = round(u * 4.4)
    im = Image.new('L', (d, d), 0)
    c = d / 2
    _ring(im, c, c, c - 1, u * 0.05)
    _ring(im, c, c, c - u * 0.16, u * 0.05)
    _ring(im, c, c, c - u * 1.05, u * 0.035)
    name = s.main()
    size = min(u * 0.46, (c - u * 0.62) * 2.6 / max(len(name), 6))
    arc_text(im, name, 'Poppins', size, c, c, c - u * 0.62, 0.2)
    if s.tagline:
        arc_text(im, s.sub(), 'Poppins', min(u * 0.27, size * 0.7), c, c, c - u * 0.6, 0.3, 'regular', bottom=True)
    _center(im, _fit(lambda sz: T(s.initials(), s.font('DM Serif Display'), sz), (c - u * 1.3) * 1.5, u * 1.2), c, c)
    return im


def _t_seal(s, u):
    d = round(u * 4.2)
    im = Image.new('L', (d, d), 0)
    c, r = d / 2, d / 2 - 1
    draw = ImageDraw.Draw(im)
    edge = lambda radius: [(c + np.cos(t) * radius * (1 + 0.035 * np.cos(18 * t)), c + np.sin(t) * radius * (1 + 0.035 * np.cos(18 * t)))
                           for t in np.linspace(0, 2 * np.pi, 360, endpoint=False)]
    draw.polygon(edge(r * 0.965), fill=255)
    draw.polygon(edge(r * 0.965 - u * 0.09), fill=0)
    _ring(im, c, c, r - u * 0.36, u * 0.03)
    name = _fit(lambda size: T(s.main(), s.font('Bebas Neue'), size, s.track(0.1)), d * 0.6, u * 0.9)
    parts = [name]
    if s.tagline:
        parts = [name, rule(name.width * 0.5, u * 0.03), _fit(lambda size: T(s.sub(), 'Poppins', size, 0.35, 'regular'), d * 0.5, u * 0.24)]
    block = vstack(parts, u * 0.14)
    _center(im, block, c, c + u * 0.25)
    draw_icon(im, s.icon('crown'), c, c - block.height / 2 - u * 0.25, u * 0.3)
    return im


def _t_hexagon(s, u):
    side = round(u * 3.2)
    im = Image.new('L', (side, side), 0)
    c, r = side / 2, side / 2 - 1
    hexagon = [(c + r * np.sin(i * np.pi / 3), c - r * np.cos(i * np.pi / 3)) for i in range(6)]
    ImageDraw.Draw(im).polygon(hexagon, outline=255, width=max(1, round(u * 0.09)))
    _center(im, _fit(lambda size: T(s.initials(), s.font('Poppins'), size, 0.05), side * 0.5, u * 1.15), c, c)
    name = _fit(lambda size: T(s.main(), 'Poppins', size, 0.4, 'regular'), side * 1.5, u * 0.3)
    return vstack([im, name], u * 0.32)


def _t_shield(s, u):
    w, h = round(u * 2.7), round(u * 3.2)
    im = Image.new('L', (w, h), 0)
    curve = [(w / 2 + (w / 2 - 1) * np.cos(t) ** 0.8, h * 0.52 + (h * 0.48 - 1) * np.sin(t)) for t in np.linspace(0, np.pi / 2, 24)]
    outline = [(1, 1), (w - 2, 1)] + curve + [(w - x, y) for x, y in reversed(curve)]
    ImageDraw.Draw(im).polygon(outline, outline=255, width=max(1, round(u * 0.09)))
    _center(im, _fit(lambda size: T(s.initials(1), s.font('DM Serif Display'), size), w * 0.5, u * 1.6), w / 2, h * 0.44)
    name = _fit(lambda size: T(s.main(), 'Poppins', size, 0.4, 'regular'), w * 1.7, u * 0.3)
    return vstack([im, name], u * 0.3)


def _t_pill(s, u):
    return framed(T(s.main(), s.font('Poppins'), u * 0.78, s.track(0.28), 'regular'), u * 0.75, u * 0.42, u * 0.06, radius=u * 3)


def _t_box(s, u):
    return framed(T(s.main(), s.font('DM Serif Display'), u, s.track(0.22)), u * 0.8, u * 0.55, u * 0.045, double=True)


def _t_tag(s, u):
    return framed(T(s.main(), s.font('Poppins'), u * 0.85, s.track(0.22)), u * 0.6, u * 0.4, 1, radius=u * 0.14, solid=True)


def _t_ribbon(s, u):
    part = T(s.main(), s.font('Poppins'), u * 0.85, s.track(0.18))
    pad_x, pad_y, notch = round(u * 1.1), round(u * 0.42), round(u * 0.42)
    w, h = part.width + 2 * pad_x, part.height + 2 * pad_y
    im = Image.new('L', (w, h), 0)
    ImageDraw.Draw(im).polygon([(0, 0), (w, 0), (w - notch, h / 2), (w, h), (0, h), (notch, h / 2)], fill=255)
    _center(im, part, w / 2, h / 2, cut=True)
    return im


_DRAW = {name[3:]: fn for name, fn in list(globals().items()) if name.startswith('_t_')}


def mark_mask(spec: dict, height: int) -> Image.Image:
    """Draw a one-colour watermark as a greyscale mask. It is drawn at double size and
    scaled down, which gives smooth edges."""
    kind = OLD_KINDS.get(spec.get('kind'), spec.get('kind'))
    draw = _DRAW.get(kind, _t_wordmark)
    big = draw(_Words(spec), max(8, height) * 2)
    return big.resize((max(1, big.width // 2), max(1, big.height // 2)), LANCZOS)


def _mark_picture(path: str | None, height: int) -> Image.Image | None:
    if not path or not os.path.exists(path):
        return None
    if path not in _IMAGE_CACHE:
        picture = load_image(path)
        picture.thumbnail((1200, 1200), LANCZOS)
        _IMAGE_CACHE[path] = picture
    picture = _IMAGE_CACHE[path]
    width = max(1, round(picture.width * height / picture.height))
    return picture.resize((width, max(1, height)), LANCZOS)


def _tint(mask: Image.Image, color: tuple, strength: float = 1.0) -> Image.Image:
    im = Image.new('RGBA', mask.size, tuple(color[:3]) + (0,))
    im.putalpha(mask if strength >= 1 else mask.point(lambda v: round(v * strength)))
    return im


def _contrast(color: tuple) -> tuple:
    luminance = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
    return (20, 20, 16) if luminance > 140 else (255, 255, 255)


def mark_image(spec: dict, height: float, color: tuple, look: str = 'solid') -> Image.Image:
    """Render one watermark as an RGBA picture, in one of the looks."""
    height = max(6, int(height))
    kind = OLD_KINDS.get(spec.get('kind'), spec.get('kind', 'wordmark'))
    color = tuple(color[:3])
    ink = _contrast(color) if look == 'label' else color   # a label swaps ink and plate

    def styled(mask: Image.Image) -> Image.Image:
        if look == 'outline':
            k = max(3, (height // 9) | 1)
            mask = ImageOps.expand(mask, k, 0)
            mask = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(k)), mask)
        if look == 'emboss':        # a light edge above and a dark edge below, like a pressed stamp
            d = max(1, height // 14)
            grown = Image.new('RGBA', (mask.width + 4 * d, mask.height + 4 * d), (0, 0, 0, 0))
            grown.alpha_composite(_tint(mask, (0, 0, 0), 0.55), (3 * d, 3 * d))
            grown.alpha_composite(_tint(mask, (255, 255, 255), 0.85), (d, d))
            grown.alpha_composite(_tint(mask, ink, 0.6), (2 * d, 2 * d))
            return grown
        return _tint(mask, ink)

    words = dict(spec, kind='wordmark')
    if kind == 'image':
        picture = _mark_picture(spec.get('path'), round(height * 2.4))
        im = picture if picture is not None else styled(mark_mask(dict(spec, kind='mono_square'), height))
    elif kind in ('logotext', 'logostack'):
        tall = kind == 'logostack'
        picture = _mark_picture(spec.get('path'), round(height * (2.2 if tall else 1.6)))
        if picture is None:
            picture = styled(mark_mask(dict(spec, kind='mono_square'), round(height * (0.95 if tall else 0.7))))
        name = styled(mark_mask(words if not tall else dict(spec, kind='thin'), round(height * (0.6 if tall else 1))))
        gap = round(height * 0.4)
        if tall:
            im = Image.new('RGBA', (max(picture.width, name.width), picture.height + gap + name.height), (0, 0, 0, 0))
            im.alpha_composite(picture, ((im.width - picture.width) // 2, 0))
            im.alpha_composite(name, ((im.width - name.width) // 2, picture.height + gap))
        else:
            im = Image.new('RGBA', (picture.width + gap + name.width, max(picture.height, name.height)), (0, 0, 0, 0))
            im.alpha_composite(picture, (0, (im.height - picture.height) // 2))
            im.alpha_composite(name, (picture.width + gap, (im.height - name.height) // 2))
    else:
        im = styled(mark_mask(spec, height))

    if look == 'label':
        pad = round(height * 0.45)
        plate = Image.new('RGBA', (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
        ImageDraw.Draw(plate).rounded_rectangle((0, 0, plate.width - 1, plate.height - 1),
                                                radius=round(height * 0.35), fill=color + (215,))
        plate.alpha_composite(im, (pad, pad))
        im = plate
    elif look == 'shadow':
        pad = max(3, round(height * 0.25))
        grown = Image.new('RGBA', (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
        soft = Image.new('L', grown.size, 0)
        soft.paste(im.getchannel('A').point(lambda v: v * 6 // 10), (pad, pad + max(1, height // 12)))
        shade = Image.new('RGBA', grown.size, (0, 0, 0, 0))
        shade.putalpha(soft.filter(ImageFilter.GaussianBlur(max(1, height / 10))))
        grown.alpha_composite(shade)
        grown.alpha_composite(im, (pad, pad))
        im = grown
    return im


def auto_mark_color(canvas: Image.Image) -> tuple:
    """White on dark pictures, near-black on light ones."""
    tiny = np.asarray(canvas.convert('L').resize((24, 24)), dtype=np.float32)
    return (255, 255, 255) if tiny.mean() < 110 else (20, 20, 16)


ANCHORS = ('tl', 'tc', 'tr', 'ml', 'mc', 'mr', 'bl', 'bc', 'br')


def draw_marks(canvas: Image.Image, s: Settings) -> None:
    spec = s.mark
    if not spec:
        return
    w, h = canvas.size
    short = min(w, h)
    auto = auto_mark_color(canvas)
    chosen = s.mark_color or spec.get('color')
    color = ImageColor.getrgb(chosen) if chosen else auto
    layout = 'single' if s.layout == 'corner' else s.layout
    big = layout in ('center', 'diagonal')
    height = short * s.mark_size / 100 * (2.5 if big else (0.75 if layout == 'band' else 1))
    mark = mark_image(spec, height, color, s.look)
    if mark.width > w * 0.8:   # a long mark is shrunk so it never runs off the canvas
        mark = mark_image(spec, height * w * 0.8 / mark.width, color, s.look)
    opacity = max(0, min(100, s.mark_opacity))
    mark.putalpha(mark.getchannel('A').point(lambda v: v * opacity // 100))
    angle = s.mark_angle if layout in ('diagonal', 'grid', 'brick') else 0
    if angle:
        mark = mark.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    mw, mh = mark.size
    margin = round(short * s.mark_margin / 100)
    gap = round(short * (0.02 + s.mark_spacing / 100 * 0.25))
    anchor = s.mark_anchor if s.mark_anchor in ANCHORS else 'br'

    if layout == 'single':
        x = {'l': margin, 'c': (w - mw) // 2, 'r': w - mw - margin}[anchor[1]]
        y = {'t': margin, 'm': (h - mh) // 2, 'b': h - mh - margin}[anchor[0]]
        paste_rgba(canvas, mark, x, y)
    elif big:
        paste_rgba(canvas, mark, (w - mw) // 2, (h - mh) // 2)
    elif layout == 'band':
        band_h = round(mh * 1.6)
        top = 0 if anchor[0] == 't' else h - band_h
        paste_rgba(canvas, Image.new('RGBA', (w, band_h), auto + (min(40, opacity),)), 0, top)
        step = mw + gap
        count = max(1, (w + gap) // step)
        start = (w - (count * step - gap)) // 2
        for i in range(count):
            paste_rgba(canvas, mark, start + i * step, top + (band_h - mh) // 2)
    else:  # grid or brick
        step_x, step_y = mw + gap, mh + gap
        rows, cols = h // step_y + 3, w // step_x + 3
        top = (h - (rows * step_y - gap)) // 2
        left = (w - (cols * step_x - gap)) // 2
        for r in range(rows):
            shift = step_x // 2 if (layout == 'brick' and r % 2) else 0
            for c in range(cols):
                paste_rgba(canvas, mark, left + c * step_x + shift, top + r * step_y)
