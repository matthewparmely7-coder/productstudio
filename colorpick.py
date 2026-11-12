"""ProductStudio's own colour picker: a colour field, a hue bar, a hex box, an eyedropper
that picks any colour from the screen, suggested colours and the colours used recently."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRegularExpressionValidator)
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ui_kit import C, SliderRow, Tile, button, label, paint_backdrop, segmented

SUGGESTED = ['#FFFFFF', '#F1F0ED', '#D5D9DF', '#1B1B1F', '#FF8A6B', '#F6D860', '#9FE3C1', '#8EC5FF',
             '#C9B8FF', '#F7C6D9', '#2F5BFF', '#0F766E', '#C9803F', '#7A1F3D', '#3B2F2F', '#000000']
RECENT: list = []      # most recent first, shared by every picker in this session


class ColorField(QWidget):
    """The big square: left to right adds colour, top to bottom adds darkness."""
    moved = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.setFixedSize(332, 190)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.hue, self.sat, self.val = 0.0, 0.0, 1.0

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        shape = QPainterPath()
        shape.addRoundedRect(r, 14, 14)
        p.setClipPath(shape)
        p.fillRect(r, QColor.fromHsvF(self.hue, 1, 1))
        white = QLinearGradient(r.topLeft(), r.topRight())
        white.setColorAt(0, QColor(255, 255, 255, 255))
        white.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(r, white)
        black = QLinearGradient(r.topLeft(), r.bottomLeft())
        black.setColorAt(0, QColor(0, 0, 0, 0))
        black.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillRect(r, black)
        p.setClipping(False)
        spot = QPointF(r.left() + self.sat * r.width(), r.top() + (1 - self.val) * r.height())
        p.setPen(QPen(QColor(0, 0, 0, 120), 4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(spot, 9, 9)
        p.setPen(QPen(QColor('#FFFFFF'), 2.5))
        p.setBrush(QColor.fromHsvF(self.hue, self.sat, self.val))
        p.drawEllipse(spot, 9, 9)

    def _pick(self, e):
        self.sat = max(0.0, min(1.0, (e.position().x() - 1) / (self.width() - 2)))
        self.val = 1 - max(0.0, min(1.0, (e.position().y() - 1) / (self.height() - 2)))
        self.update()
        self.moved.emit(self.sat, self.val)

    mousePressEvent = _pick
    mouseMoveEvent = _pick


class HueBar(QWidget):
    moved = Signal(float)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hue = 0.0

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(10, 7, self.width() - 20, 12)
        rainbow = QLinearGradient(bar.topLeft(), bar.topRight())
        for i in range(13):
            rainbow.setColorAt(i / 12, QColor.fromHsvF((i / 12) % 1.0 if i < 12 else 0.0, 1, 1))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rainbow)
        p.drawRoundedRect(bar, 6, 6)
        x = bar.left() + min(self.hue, 0.999) * bar.width()
        p.setPen(QPen(QColor('#FFFFFF'), 2.5))
        p.setBrush(QColor.fromHsvF(min(self.hue, 0.999), 1, 1))
        p.drawEllipse(QPointF(x, bar.center().y()), 10, 10)

    def _pick(self, e):
        self.hue = max(0.0, min(0.999, (e.position().x() - 10) / (self.width() - 20)))
        self.update()
        self.moved.emit(self.hue)

    mousePressEvent = _pick
    mouseMoveEvent = _pick


class ScreenPicker(QWidget):
    """The eyedropper. It freezes a picture of the screen, shows a magnifier at the mouse,
    and reports the colour that is clicked. Esc or a right click cancels."""
    picked = Signal(QColor)
    cancelled = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.shot = screen.grabWindow(0)
        self.image = self.shot.toImage()
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.at = QPointF(self.width() / 2, self.height() / 2)

    def color_at(self, pos: QPointF) -> QColor:
        k = self.image.width() / max(1, self.width())
        x = max(0, min(self.image.width() - 1, int(pos.x() * k)))
        y = max(0, min(self.image.height() - 1, int(pos.y() * k)))
        return self.image.pixelColor(x, y)

    def mouseMoveEvent(self, e):
        self.at = e.position()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.color_at(e.position()))
        else:
            self.cancelled.emit()
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.shot)
        color = self.color_at(self.at)
        centre = self.at + QPointF(70, 70)
        if centre.x() > self.width() - 70:
            centre.setX(self.at.x() - 70)
        if centre.y() > self.height() - 90:
            centre.setY(self.at.y() - 70)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        loupe = QPainterPath()
        loupe.addEllipse(centre, 52, 52)
        p.save()
        p.setClipPath(loupe)
        k = self.image.width() / max(1, self.width())
        source = QRectF(self.at.x() * k - 6, self.at.y() * k - 6, 13, 13)
        p.drawImage(QRectF(centre.x() - 52, centre.y() - 52, 104, 104), self.image, source)
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor('#141610'), 5))
        p.drawEllipse(centre, 52, 52)
        p.setPen(QPen(color, 3))
        p.drawEllipse(centre, 52, 52)
        p.setPen(QPen(QColor('#FFFFFF'), 1.5))
        p.drawRect(QRectF(centre.x() - 4, centre.y() - 4, 8, 8))
        tag = QRectF(centre.x() - 40, centre.y() + 58, 80, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#141610'))
        p.drawRoundedRect(tag, 8, 8)
        p.setPen(QColor('#FFFFFF'))
        font = QFont(self.font())
        font.setBold(True)
        p.setFont(font)
        p.drawText(tag, Qt.AlignmentFlag.AlignCenter, color.name().upper())


class ColorDialog(QDialog):
    """`self.chosen` holds the '#RRGGBB' result after accept()."""

    def __init__(self, parent, initial: str, title: str, on_change=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.on_change = on_change
        self.first = QColor(initial) if QColor(initial).isValid() else QColor('#FFFFFF')
        self.chosen = self.first.name().upper()
        self.picker = None
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 20, 22, 20)
        box.setSpacing(14)
        box.addWidget(label(title, 'h2'))
        self.field = ColorField()
        box.addWidget(self.field)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.dropper = button('', self.start_picking, None, 'palette', 'Pick a color from anywhere on the screen')
        self.dropper.setFixedSize(46, 42)
        row.addWidget(self.dropper)
        self.hue = HueBar()
        row.addWidget(self.hue, 1)
        box.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.before_after = Tile(size=QSize(92, 44), paint=self.paint_preview, fill=None,
                                 tip='Left: the color before. Right: the new color. Click to go back.')
        self.before_after.clicked.connect(lambda: self.set_color(self.first))
        row.addWidget(self.before_after)
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setAccessibleName('Hex color code')
        self.hex.setValidator(QRegularExpressionValidator(QRegularExpression('#?[0-9A-Fa-f]{0,6}')))
        self.hex.textEdited.connect(self.hex_typed)
        row.addWidget(self.hex, 1)
        box.addLayout(row)

        for caption, colors in (('Suggested', SUGGESTED), ('Recent', RECENT[:8])):
            if not colors:
                continue
            cap = label(caption)
            cap.setStyleSheet(f'color: {C["soft"]}; font-weight: 600; font-size: 9pt;')
            box.addWidget(cap)
            grid = QGridLayout()
            grid.setSpacing(8)
            for i, name in enumerate(colors):
                swatch = Tile(size=QSize(34, 34), fill=name, tip=name)
                swatch.setCheckable(False)
                swatch.clicked.connect(lambda _=False, n=name: self.set_color(QColor(n)))
                grid.addWidget(swatch, i // 8, i % 8)
            box.addLayout(grid)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button('Cancel', self.reject))
        buttons.addWidget(button('Use this color', self.accept, 'primary', 'check'))
        box.addLayout(buttons)
        self.field.moved.connect(lambda _s, _v: self.from_controls())
        self.hue.moved.connect(lambda _h: self.from_controls())
        self.set_color(self.first, notify=False)
        self.setFixedSize(self.sizeHint())

    def paint_preview(self, p: QPainter, r: QRectF):
        p.fillRect(r, self.first)
        p.fillRect(QRectF(r.center().x(), r.top(), r.width() / 2 + 1, r.height()), QColor(self.chosen))

    def from_controls(self):
        self.field.hue = self.hue.hue
        self.field.update()
        self._apply(QColor.fromHsvF(self.hue.hue, self.field.sat, self.field.val), keep_hex=False)

    def hex_typed(self, text: str):
        text = text if text.startswith('#') else '#' + text
        if len(text) == 7 and QColor(text).isValid():
            self.set_color(QColor(text), keep_hex=True)

    def set_color(self, color: QColor, notify: bool = True, keep_hex: bool = False):
        h, s, v, _a = color.getHsvF()
        if h >= 0:                      # greys have no hue: keep the bar where it was
            self.hue.hue = self.field.hue = min(h, 0.999)
        self.field.sat, self.field.val = s, v
        self.hue.update()
        self.field.update()
        self._apply(color, keep_hex, notify)

    def _apply(self, color: QColor, keep_hex: bool, notify: bool = True):
        self.chosen = color.name().upper()
        if not keep_hex:
            self.hex.setText(self.chosen)
        self.before_after.update()
        if notify and self.on_change:
            self.on_change(self.chosen)

    def start_picking(self):
        """Hide this window, then let the user click any pixel on the screen."""
        self.hide()
        QTimer.singleShot(180, self._open_picker)

    def _open_picker(self):
        self.picker = ScreenPicker()
        self.picker.picked.connect(self._picked)
        self.picker.cancelled.connect(self.show)
        self.picker.show()
        self.picker.activateWindow()
        self.picker.setFocus()

    def _picked(self, color: QColor):
        self.show()
        self.set_color(color)


def pick_color(parent, initial: str, title: str, on_change=None) -> str | None:
    """Open the picker. Returns '#RRGGBB', or None when cancelled. `on_change` gets every
    colour while the user is still choosing, for a live preview."""
    dialog = ColorDialog(parent, initial, title, on_change)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    if dialog.chosen in RECENT:
        RECENT.remove(dialog.chosen)
    RECENT.insert(0, dialog.chosen)
    del RECENT[8:]
    return dialog.chosen


class BackdropDialog(QDialog):
    """Make a new backdrop: one colour, a linear gradient or a radial gradient.
    `self.spec` holds the result after accept()."""
    KINDS = {'Solid': 'solid', 'Linear': 'linear', 'Radial': 'radial'}

    def __init__(self, parent, start: dict | None, on_change=None):
        super().__init__(parent)
        self.setWindowTitle('New backdrop')
        self.on_change = on_change
        start = start or {}
        colors = list(start.get('colors') or [])
        first = start.get('color') or (colors[0] if colors else '#FF8A6B')
        second = colors[1] if len(colors) > 1 else '#F6D860'
        kind = start.get('type') if start.get('type') in self.KINDS.values() else 'solid'
        self.kind, self.first, self.second, self.angle = kind, first, second, int(start.get('angle', 160))

        box = QVBoxLayout(self)
        box.setContentsMargins(22, 20, 22, 20)
        box.setSpacing(14)
        box.addWidget(label('New backdrop', 'h2'))
        self.preview = Tile(size=QSize(332, 150), paint=lambda p, r: paint_backdrop(self.spec)(p, r), fill=None)
        self.preview.setEnabled(False)
        box.addWidget(self.preview)
        kinds, self.kind_buttons = segmented(list(self.KINDS), lambda n: self.change('kind', self.KINDS[n]))
        box.addWidget(kinds)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.first_tile = Tile('Color', QSize(160, 46), paint=lambda p, r: p.fillRect(r, QColor(self.first)), fill=None,
                               tip='Choose the color')
        self.second_tile = Tile('Second color', QSize(160, 46), paint=lambda p, r: p.fillRect(r, QColor(self.second)),
                                fill=None, tip='Choose the second color')
        for tile, key in ((self.first_tile, 'first'), (self.second_tile, 'second')):
            tile.setCheckable(False)
            tile.clicked.connect(lambda _=False, k=key: self.choose(k))
            row.addWidget(tile)
        row.addStretch(1)
        box.addLayout(row)
        self.direction = SliderRow('Direction', 0, 360, self.angle)
        self.direction.moved.connect(lambda v: self.change('angle', v))
        box.addWidget(self.direction)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button('Cancel', self.reject))
        buttons.addWidget(button('Add backdrop', self.accept, 'primary', 'plus'))
        box.addLayout(buttons)
        self.refresh(notify=False)

    @property
    def spec(self) -> dict:
        if self.kind == 'solid':
            return {'name': f'Color {self.first}', 'type': 'solid', 'color': self.first}
        if self.kind == 'linear':
            return {'name': 'Linear gradient', 'type': 'linear', 'colors': [self.first, self.second], 'angle': self.angle}
        return {'name': 'Radial gradient', 'type': 'radial', 'colors': [self.first, self.second]}

    def change(self, key: str, value):
        setattr(self, key, value)
        self.refresh()

    def choose(self, key: str):
        before = getattr(self, key)
        chosen = pick_color(self, before, 'Color' if key == 'first' else 'Second color', lambda c: self.change(key, c))
        self.change(key, chosen or before)

    def refresh(self, notify: bool = True):
        for name, kind in self.KINDS.items():
            if kind == self.kind:
                self.kind_buttons[name].setChecked(True)
        self.second_tile.setVisible(self.kind != 'solid')
        self.first_tile.caption = 'Color' if self.kind == 'solid' else ('Start' if self.kind == 'linear' else 'Centre')
        self.second_tile.caption = 'End' if self.kind == 'linear' else 'Edge'
        self.direction.setVisible(self.kind == 'linear')
        for tile in (self.preview, self.first_tile, self.second_tile):
            tile.update()
        self.adjustSize()
        if notify and self.on_change:
            self.on_change(self.spec)


def make_backdrop_dialog(parent, start: dict | None, on_change=None) -> dict | None:
    dialog = BackdropDialog(parent, start, on_change)
    return dialog.spec if dialog.exec() == QDialog.DialogCode.Accepted else None
