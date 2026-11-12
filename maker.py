"""Brand tools: the brand kit window and the watermark customizer."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QConicalGradient, QFont, QPainter
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFileDialog, QFontComboBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget)

import engine as E
from colorpick import pick_color
from ui_kit import C, SliderRow, Tile, button, checker, label, segmented, to_pixmap

TILE_INK = (58, 58, 54)
IMAGE_FILTER = 'Images (*.png *.jpg *.jpeg *.webp *.bmp)'
TALL = ('Badge', 'Monogram')          # groups whose marks are taller than a line of text
CASES = {'Auto': None, 'AA': 'upper', 'Aa': 'title', 'aa': 'lower'}


def group_of(kind: str) -> str:
    kind = E.OLD_KINDS.get(kind, kind)
    return next((g for i, _n, g in E.TEMPLATES if i == kind), 'Text')


def mark_thumb(spec: dict, height: int = 30):
    """A dark preview of a watermark for a light tile, drawn with the real text."""
    if group_of(spec.get('kind', '')) in TALL:
        height = round(height * 0.8)
    return to_pixmap(E.mark_image(spec, height, TILE_INK))


def _dot_icon(name: str):
    mask = Image.new('L', (60, 60), 0)
    if name != 'none':
        E.draw_icon(mask, name, 30, 30, 22)
    return to_pixmap(E._tint(mask, TILE_INK))


class MarkPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 320)
        self.pixmap = None
        self.dark = False

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        board = QRectF(self.rect()).adjusted(20, 12, -20, -12)
        if self.dark:
            checker(p, board, 16, '#3A3A40', '#2A2A2F')
        else:
            checker(p, board, 16, '#EDEDE8', '#DADAD4')
        if self.pixmap is not None:
            k = min(board.width() * 0.72 / self.pixmap.width(), board.height() * 0.72 / self.pixmap.height(), 1.0)
            t = QRectF(0, 0, self.pixmap.width() * k, self.pixmap.height() * k)
            t.moveCenter(board.center())
            p.drawPixmap(t, self.pixmap, QRectF(self.pixmap.rect()))


class BrandDialog(QDialog):
    """The brand kit: the name, tagline, handle and logo every watermark template uses.
    `self.kit` holds the result after accept()."""

    def __init__(self, kit: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Your brand')
        self.setMinimumWidth(440)
        self.kit = dict(kit)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 22)
        box.setSpacing(8)
        box.addWidget(label('Your brand', 'h2'))
        box.addWidget(label('Fill this in once. Every watermark style uses it.', 'muted'))
        box.addSpacing(6)
        self.fields = {}
        for key, caption, hint in (('name', 'Brand name', 'Oak & Pine'), ('tagline', 'Tagline', 'Handmade goods'),
                                   ('handle', 'Website or handle', '@oakandpine')):
            cap = label(caption)
            cap.setStyleSheet(f'color: {C["soft"]}; font-weight: 600; font-size: 9pt;')
            box.addWidget(cap)
            field = QLineEdit(self.kit.get(key) or '')
            field.setPlaceholderText(hint)
            field.setMaxLength(40)
            field.setAccessibleName(caption)
            box.addWidget(field)
            box.addSpacing(4)
            self.fields[key] = field
        cap = label('Logo')
        cap.setStyleSheet(f'color: {C["soft"]}; font-weight: 600; font-size: 9pt;')
        box.addWidget(cap)
        row = QHBoxLayout()
        self.logo_button = button('', self.choose_logo, None, 'upload')
        row.addWidget(self.logo_button, 1)
        self.clear_button = button('Remove', self.clear_logo)
        row.addWidget(self.clear_button)
        box.addLayout(row)
        box.addSpacing(10)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button('Cancel', self.reject))
        buttons.addWidget(button('Save', self.finish, 'primary', 'check'))
        box.addLayout(buttons)
        self.refresh()

    def refresh(self):
        logo = self.kit.get('logo')
        self.logo_button.setText(Path(logo).name if logo else 'Choose a logo image')
        self.clear_button.setVisible(bool(logo))

    def choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose your logo', '', IMAGE_FILTER)
        if path:
            self.kit['logo'] = path
            self.refresh()

    def clear_logo(self):
        self.kit['logo'] = None
        self.refresh()

    def finish(self):
        for key, field in self.fields.items():
            self.kit[key] = field.text().strip()
        if not self.kit['name']:
            self.kit['name'] = 'Your Brand'
        self.accept()


class MakerDialog(QDialog):
    """Fine-tune one watermark: its template, words, font, spacing, icon and colour.
    `self.spec` holds the result after accept()."""

    def __init__(self, spec: dict | None = None, product=None, parent=None, kit: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle('Customize watermark')
        self.resize(1100, 760)
        self.product = product
        self.spec = dict(spec) if spec else E.template_spec('badge', kit or E.DEFAULT_KIT)
        self.spec['kind'] = E.OLD_KINDS.get(self.spec.get('kind'), self.spec.get('kind', 'wordmark'))
        self.spec.pop('from_kit', None)
        if not spec or spec.get('from_kit'):
            self.spec['name'] = 'My ' + self.spec.get('name', 'watermark').lower()
        self.building = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        bar.setObjectName('bar')
        bar.setFixedHeight(60)
        top = QHBoxLayout(bar)
        top.setContentsMargins(20, 0, 16, 0)
        top.addWidget(label('Customize watermark', 'h2'))
        top.addStretch(1)
        top.addWidget(button('Reset style', self.reset_style, None, None, 'Go back to the template\'s own font, spacing and icon'))
        top.addWidget(button('Cancel', self.reject))
        top.addWidget(button('Save to My watermarks', self.accept, 'primary', 'check'))
        root.addWidget(bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)

        left = QVBoxLayout()
        left.setContentsMargins(8, 8, 8, 16)
        self.preview = MarkPreview()
        left.addWidget(self.preview, 1)
        looks = QHBoxLayout()
        looks.setSpacing(14)
        looks.addStretch(1)
        self.on_light, self.on_dark = QLabel(), QLabel()
        looks.addWidget(self.on_light)
        looks.addWidget(self.on_dark)
        looks.addStretch(1)
        left.addLayout(looks)
        body.addLayout(left, 1)

        side = QFrame()
        side.setObjectName('side')
        side.setFixedWidth(360)
        side_box = QVBoxLayout(side)
        side_box.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        form = QVBoxLayout(inner)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(6)
        area.setWidget(inner)
        side_box.addWidget(area)
        body.addWidget(side)

        def caption(text: str) -> QLabel:
            cap = label(text)
            cap.setStyleSheet(f'color: {C["soft"]}; font-weight: 600; font-size: 9pt;')
            form.addWidget(cap)
            return cap

        caption('Style')
        self.kind_box = QComboBox()
        for ident, name, group in E.TEMPLATES:
            self.kind_box.addItem(f'{group} · {name}', ident)
        self.kind_box.setCurrentIndex(max(0, self.kind_box.findData(self.spec['kind'])))
        self.kind_box.currentIndexChanged.connect(lambda _i: self.change('kind', self.kind_box.currentData()))
        form.addWidget(self.kind_box)
        form.addSpacing(6)

        self.rows = {}
        for key, text, limit in (('name', 'Name in your list', 24), ('text', 'Brand name', 40), ('tagline', 'Tagline', 40),
                                 ('handle', 'Website or handle', 40)):
            cap = caption(text)
            field = QLineEdit(self.spec.get(key) or '')
            field.setMaxLength(limit)
            field.setAccessibleName(text)
            field.textChanged.connect(lambda value, k=key: self.change(k, value))
            form.addWidget(field)
            form.addSpacing(6)
            self.rows[key] = (cap, field)

        self.font_cap = caption('Font')
        fonts = QGridLayout()
        fonts.setSpacing(8)
        self.font_group = QButtonGroup(self)
        self.font_tiles = {}
        for i, (style, name) in enumerate(E.FONT_LABELS.items()):
            pix = to_pixmap(E._tint(E.T('Aa', style, 34), TILE_INK))
            tile = Tile(name, QSize(98, 52), pixmap=pix, tip=f'{name}: {style}')
            tile.clicked.connect(lambda _=False, s=style: self.change('font', s))
            self.font_group.addButton(tile)
            self.font_tiles[style] = tile
            fonts.addWidget(tile, i // 3, i % 3)
        self.font_holder = QWidget()
        self.font_holder.setLayout(fonts)
        form.addWidget(self.font_holder)
        self.pc_cap = caption('Or a font from this PC')
        self.font_box = QFontComboBox()
        self.font_box.activated.connect(lambda _i: self.change('font', self.font_box.currentFont().family()))
        form.addWidget(self.font_box)
        form.addSpacing(6)

        self.spacing = SliderRow('Letter spacing', 0, 60, int(self.spec.get('tracking') or 15))
        self.spacing.moved.connect(lambda v: self.change('tracking', v))
        form.addWidget(self.spacing)
        self.case_cap = caption('Letters')
        self.case_row, self.case_buttons = segmented(list(CASES), lambda n: self.change('case', CASES[n]))
        form.addWidget(self.case_row)
        form.addSpacing(6)

        self.icon_cap = caption('Symbol')
        icons = QGridLayout()
        icons.setSpacing(8)
        self.icon_group = QButtonGroup(self)
        self.icon_tiles = {}
        for i, name in enumerate(E.ICONS):
            tile = Tile(size=QSize(34, 34), pixmap=_dot_icon(name), tip=name.capitalize())
            tile.clicked.connect(lambda _=False, n=name: self.change('icon', n))
            self.icon_group.addButton(tile)
            self.icon_tiles[name] = tile
            icons.addWidget(tile, 0, i)
        self.icon_holder = QWidget()
        self.icon_holder.setLayout(icons)
        form.addWidget(self.icon_holder)

        self.picture_cap = caption('Logo image')
        self.picture_button = button('Choose a picture', self.choose_picture, None, 'upload')
        form.addWidget(self.picture_button)
        form.addSpacing(6)

        self.color_cap = caption('Color')
        swatches = QHBoxLayout()
        swatches.setSpacing(10)
        self.color_group = QButtonGroup(self)
        self.color_tiles = {}
        for key, tip, fill in ((None, 'Automatic: dark on light photos, white on dark', None),
                               ('#141410', 'Ink', '#141410'), ('#FFFFFF', 'White', '#FFFFFF'),
                               ('custom', 'Pick a color', None)):
            paint = self._auto_paint if key is None else (self._custom_paint if key == 'custom' else None)
            tile = Tile(size=QSize(44, 44), fill=fill, paint=paint, tip=tip)
            tile.clicked.connect(lambda _=False, k=key: self.set_color(k))
            self.color_group.addButton(tile)
            self.color_tiles[key] = tile
            swatches.addWidget(tile)
        swatches.addStretch(1)
        self.color_holder = QWidget()
        self.color_holder.setLayout(swatches)
        form.addWidget(self.color_holder)
        form.addStretch(1)

        self.building = False
        self.refresh()

    # ----------------------------------------------------------------- painting

    @staticmethod
    def _auto_paint(p: QPainter, r: QRectF):
        p.fillRect(r, QColor('#FFFFFF'))
        p.fillRect(QRectF(r.left(), r.top(), r.width() / 2, r.height()), QColor('#141410'))

    def _custom_paint(self, p: QPainter, r: QRectF):
        current = self.spec.get('color')
        if current and current not in ('#141410', '#FFFFFF'):
            p.fillRect(r, QColor(current))
        else:
            wheel = QConicalGradient(r.center(), 90)
            for i in range(13):
                wheel.setColorAt(i / 12, QColor.fromHsvF((i / 12) % 1.0 if i < 12 else 0.0, 0.85, 1.0))
            p.fillRect(r, wheel)

    # ------------------------------------------------------------------ changes

    def change(self, key: str, value):
        if self.building:
            return
        self.spec[key] = value
        self.refresh()

    def reset_style(self):
        for key in ('font', 'tracking', 'case', 'icon', 'color'):
            self.spec.pop(key, None)
        self.refresh()

    def set_color(self, key):
        if key == 'custom':
            before = self.spec.get('color')

            def live(color: str):
                self.spec['color'] = color
                self.refresh()

            key = pick_color(self, before or '#C9803F', 'Watermark color', live)
            if key is None:
                self.spec['color'] = before
                self.refresh()
                return
        self.spec['color'] = key
        self.refresh()

    def choose_picture(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose a picture', '', IMAGE_FILTER)
        if path:
            self.spec['path'] = path
            self.refresh()

    def refresh(self):
        kind = self.spec.get('kind', 'wordmark')
        group = group_of(kind)
        logo = group == 'Logo'
        show = {'name': True, 'text': kind != 'image', 'handle': kind == 'handle',
                'tagline': kind in ('serif', 'stack', 'autograph', 'badge', 'crest', 'seal')}
        for key, (cap, field) in self.rows.items():
            cap.setVisible(show[key])
            field.setVisible(show[key])
        styled = kind != 'image'
        for w in (self.font_cap, self.font_holder, self.pc_cap, self.font_box, self.spacing, self.case_cap, self.case_row,
                  self.color_cap, self.color_holder):
            w.setVisible(styled)
        for w in (self.icon_cap, self.icon_holder):
            w.setVisible(kind in ('badge', 'seal'))
        for w in (self.picture_cap, self.picture_button):
            w.setVisible(logo)
        self.picture_button.setText(Path(self.spec['path']).name if self.spec.get('path') else 'Choose a picture')

        self.building = True
        self.font_group.setExclusive(False)
        for style, tile in self.font_tiles.items():
            tile.setChecked(self.spec.get('font') == style)
        self.font_group.setExclusive(True)
        self.icon_group.setExclusive(False)
        for name, tile in self.icon_tiles.items():
            tile.setChecked(self.spec.get('icon') == name)
        self.icon_group.setExclusive(True)
        case = next((n for n, v in CASES.items() if v == self.spec.get('case')), 'Auto')
        self.case_buttons[case].setChecked(True)
        color = self.spec.get('color')
        key = None if not color else (color if color in self.color_tiles else 'custom')
        self.color_tiles[key].setChecked(True)
        self.building = False

        rgb = QColor(color).getRgb()[:3] if color else (20, 20, 16)
        self.preview.dark = sum(rgb) > 600
        draft = E.mark_image(self.spec, 40, rgb)
        sharp = 40 * 620 / max(draft.size)       # draw the big preview at full sharpness
        self.preview.pixmap = to_pixmap(E.mark_image(self.spec, sharp, rgb))
        self.preview.update()
        for target, backdrop in ((self.on_light, {'type': 'solid', 'color': '#FF8A6B'}),
                                 (self.on_dark, {'type': 'solid', 'color': '#1B1B1F'})):
            s = E.Settings(width=300, height=300, backdrop=backdrop, shadow='soft', mark=self.spec, layout='single',
                           mark_size=6 if group in TALL else 8, mark_opacity=85)
            pix = to_pixmap(E.render(self.product, s, E.Placement(scale=70, y=-8)))
            pix.setDevicePixelRatio(2)
            target.setPixmap(pix)
        self.color_tiles['custom'].update()
