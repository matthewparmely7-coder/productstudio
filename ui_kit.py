"""Shared look and small widgets for ProductStudio: colours, style sheet, icons, tiles."""
from __future__ import annotations

import math
from string import Template

from PySide6.QtCore import (QByteArray, QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
                            Signal)
from PySide6.QtGui import (QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

C = {
    'bg': '#0E0E10', 'bar': '#121214', 'panel': '#18181B', 'raised': '#232327', 'line': '#2E2E33',
    'dash': '#4A4A52', 'text': '#F4F4F0', 'soft': '#D6D6D2', 'muted': '#A3A3A8', 'lime': '#C8F542',
    'limehi': '#DAFF6B', 'ink': '#141610', 'coral': '#FF7A59', 'tile': '#E9E9E4', 'tileink': '#3A3A36',
}

QSS = Template('''
QWidget { font-family: "Segoe UI"; font-size: 10pt; color: $text; }
QMainWindow, QDialog, QWidget#page { background: $bg; }
QFrame#bar, QFrame#rail, QFrame#side { background: $bar; }
QFrame#bar { border-bottom: 1px solid $raised; }
QFrame#rail { border-right: 1px solid $raised; }
QFrame#side { border-left: 1px solid $raised; }
QFrame#float { background: $panel; border: 1px solid $line; border-radius: 14px; }
QLabel#h1 { font-size: 34pt; font-weight: 700; color: $text; }
QLabel#h2 { font-size: 13pt; font-weight: 700; color: $text; }
QLabel#brand { font-size: 12pt; font-weight: 700; color: $text; }
QLabel#muted { color: $muted; }
QLabel#lead { color: #B9B9BD; font-size: 12pt; }
QPushButton { background: $panel; border: 1px solid $line; border-radius: 12px; padding: 0 16px;
              min-height: 42px; font-weight: 600; }
QPushButton:hover { background: $raised; }
QPushButton:disabled { color: #77777D; }
QPushButton#primary { background: $lime; border: 0; color: $ink; }
QPushButton#primary:hover { background: $limehi; }
QPushButton#primary:disabled { background: #4C5A22; color: #1E230F; }
QPushButton#ghost { background: transparent; border: 0; padding: 0; min-width: 44px; }
QPushButton#ghost:hover { background: $raised; }
QPushButton#winclose { background: transparent; border: 0; padding: 0; min-width: 44px; border-radius: 10px; }
QPushButton#winclose:hover { background: #D7263D; }
QPushButton#step { background: transparent; border: 1px solid $line; border-radius: 22px; padding: 0 20px;
                   min-height: 42px; color: $soft; }
QPushButton#step:checked { background: $lime; border-color: $lime; color: $ink; }
QPushButton#chip { background: transparent; border: 1px solid $line; border-radius: 13px; min-height: 26px;
                   padding: 0 6px; font-size: 8.5pt; color: $soft; }
QPushButton#chip:hover { background: $raised; }
QPushButton#chip:checked { background: $lime; border-color: $lime; color: $ink; }
QPushButton#seg { background: transparent; border: 0; border-radius: 10px; color: $muted; min-height: 40px; }
QPushButton#seg:checked { background: $line; color: $text; }
QLineEdit, QSpinBox, QComboBox { background: $panel; border: 1px solid $line; border-radius: 10px; padding: 0 10px;
                                 min-height: 40px; selection-background-color: #5C7A00; }
QComboBox::drop-down { border: 0; width: 26px; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; border: 0; }
QComboBox QAbstractItemView { background: $raised; selection-background-color: #4C5A22; color: $text; }
QSlider::groove:horizontal { height: 4px; background: $line; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $lime; border-radius: 2px; }
QSlider::handle:horizontal { background: $text; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }
QScrollArea { border: 0; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: $line; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid $dash; border-radius: 5px; background: $panel; }
QCheckBox::indicator:checked { background: $lime; border-color: $lime; }
QProgressBar { background: $raised; border: 0; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: $lime; border-radius: 3px; }
QMenu { background: $raised; border: 1px solid $line; padding: 6px; }
QMenu::item { padding: 8px 18px; border-radius: 6px; }
QMenu::item:selected { background: $line; }
QToolTip { background: $raised; color: $text; border: 1px solid $line; padding: 4px 8px; }
''').substitute(C)

ICONS = {
    'undo': '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    'redo': '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
    'compare': '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M12 4v16"/>',
    'export': '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    'upload': '<path d="M12 16V5M7 10l5-5 5 5M5 20h14"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'minus': '<path d="M5 12h14"/>',
    'lock': '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    'center': '<circle cx="12" cy="12" r="4"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/>',
    'flip': '<path d="M12 3v18M8 7l-5 5 5 5zM16 7l5 5-5 5z"/>',
    'brush': '<path d="M15 4l5 5L10 19H5v-5z"/><path d="M12 7l5 5"/>',
    'image': '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 16l5-5 4 4 3-3 6 6"/>',
    'back': '<path d="M15 5l-7 7 7 7"/>',
    'check': '<path d="M5 12l5 5 9-10"/>',
    'none': '<circle cx="12" cy="12" r="8"/><path d="M6.5 17.5l11-11"/>',
    'move': '<path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3"/>',
    'win_min': '<path d="M6 12h12"/>',
    'win_max': '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    'win_close': '<path d="M6 6l12 12M18 6L6 18"/>',
    'trash': '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    'checkall': '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
    'info': '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.6v.4"/>',
    'warn': '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 16.8v.4"/>',
    'error': '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    'palette': '<path d="M14 6l4 4M4 20l2-6 9-9 4 4-9 9z"/>',
}


def icon_pixmap(name: str, color: str = C['text'], size: int = 20) -> QPixmap:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="%s" stroke-width="2" '
           'stroke-linecap="round" stroke-linejoin="round">%s</svg>') % (color, ICONS[name])
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(2)
    return pm


def icon(name: str, color: str = C['text'], size: int = 20) -> QIcon:
    return QIcon(icon_pixmap(name, color, size))


def to_qimage(pil_image) -> QImage:
    rgba = pil_image.convert('RGBA')
    data = rgba.tobytes()
    return QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()


def to_pixmap(pil_image) -> QPixmap:
    return QPixmap.fromImage(to_qimage(pil_image))


def label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    item.setWordWrap(wrap)
    return item


def button(text: str, action=None, kind: str | None = None, icon_name: str | None = None, tip: str | None = None):
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if kind:
        b.setObjectName(kind)
    if icon_name:
        b.setIcon(icon(icon_name, C['ink'] if kind == 'primary' else C['text']))
        b.setIconSize(QSize(20, 20))
    if tip:
        b.setToolTip(tip)
        b.setAccessibleName(tip)
    if action:
        b.clicked.connect(action)
    return b


def toggle_maximized(window):
    if window.isMaximized():
        window.showNormal()
    else:
        window.showMaximized()


class TitleBar(QFrame):
    """The app's own top bar. It replaces the Windows title bar: drag it to move the
    window, double-click it to maximize."""

    def __init__(self):
        super().__init__()
        self.setObjectName('bar')
        self.setFixedHeight(60)

    def mousePressEvent(self, e):
        handle = self.window().windowHandle()
        if e.button() == Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()

    def mouseDoubleClickEvent(self, _e):
        toggle_maximized(self.window())


def window_controls() -> QWidget:
    """Minimize, maximize and close buttons for the right end of a TitleBar."""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(8, 0, 0, 0)
    row.setSpacing(0)
    actions = (('win_min', 'Minimize', lambda w: w.showMinimized()),
               ('win_max', 'Maximize or restore', toggle_maximized),
               ('win_close', 'Close', lambda w: w.close()))
    for name, tip, action in actions:
        b = QPushButton()
        b.setObjectName('winclose' if name == 'win_close' else 'ghost')
        b.setIcon(icon(name, C['soft'], 18))
        b.setIconSize(QSize(18, 18))
        b.setFixedSize(44, 40)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.clicked.connect(lambda _=False, a=action, source=b: a(source.window()))
        row.addWidget(b)
    return holder


class Clickable(QWidget):
    """A plain widget that reports clicks, used for the logo."""
    clicked = Signal()

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


class Tile(QAbstractButton):
    """A picture button with an optional caption and a lime ring when selected."""

    def __init__(self, caption: str = '', size: QSize = QSize(66, 66), paint=None, pixmap: QPixmap | None = None,
                 fill: str | None = C['tile'], dashed: bool = False, accent: bool = False, tip: str | None = None):
        super().__init__()
        self.caption, self.paint_fn, self.pixmap = caption, paint, pixmap
        self.fill, self.dashed, self.accent, self.face = fill, dashed, accent, size
        self.setCheckable(not dashed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(size.width(), size.height() + (20 if caption else 0))
        self.setToolTip(tip or '')
        self.setAccessibleName(tip or caption)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        outer = QRectF(1, 1, self.face.width() - 2, self.face.height() - 2)
        face = outer.adjusted(3, 3, -3, -3)
        if self.dashed:
            pen = QPen(QColor(C['lime'] if self.accent else C['dash']), 1.5, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(face, 11, 11)
        else:
            path = QPainterPath()
            path.addRoundedRect(face, 11, 11)
            p.setClipPath(path)
            if self.fill:
                p.fillPath(path, QColor(self.fill))
            if self.paint_fn:
                self.paint_fn(p, face)
            p.setClipping(False)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if self.isChecked():
                p.setPen(QPen(QColor(C['lime']), 2))
                p.drawRoundedRect(outer, 13, 13)
            elif self.underMouse():
                p.setPen(QPen(QColor(C['dash']), 1))
                p.drawRoundedRect(outer, 13, 13)
        if self.pixmap is not None:
            pm = self.pixmap
            ratio = pm.devicePixelRatio()
            w, h = pm.width() / ratio, pm.height() / ratio
            k = min(face.width() * 0.74 / w, face.height() * 0.74 / h, 1.0)
            target = QRectF(0, 0, w * k, h * k)
            target.moveCenter(face.center())
            p.drawPixmap(target, pm, QRectF(pm.rect()))
        if self.caption:
            p.setPen(QColor(C['lime'] if self.accent else (C['text'] if self.isChecked() else C['soft'])))
            font = QFont(self.font())
            font.setPointSizeF(8.5)
            p.setFont(font)
            p.drawText(QRectF(-10, self.face.height(), self.width() + 20, 20),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self.caption)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class SliderRow(QWidget):
    """A caption above a slider. `moved` fires while dragging, `released` when done."""
    moved = Signal(int)
    released = Signal()

    def __init__(self, caption: str, low: int, high: int, value: int):
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        cap = label(caption)
        cap.setStyleSheet(f'color: {C["soft"]}; font-weight: 600; font-size: 9pt;')
        box.addWidget(cap)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(low, high)
        self.slider.setValue(value)
        self.slider.setMinimumHeight(28)
        self.slider.setAccessibleName(caption)
        self.slider.valueChanged.connect(self.moved)
        self.slider.sliderReleased.connect(self.released)
        box.addWidget(self.slider)

    def set(self, value: int):
        self.slider.blockSignals(True)
        self.slider.setValue(int(value))
        self.slider.blockSignals(False)


def segmented(names: list, on_pick) -> tuple:
    """A row of mutually exclusive pill buttons. Returns (container, {name: button})."""
    holder = QWidget()
    holder.setStyleSheet(f'QWidget#segs {{ background: {C["panel"]}; border-radius: 12px; }}')
    holder.setObjectName('segs')
    row = QHBoxLayout(holder)
    row.setContentsMargins(4, 4, 4, 4)
    row.setSpacing(4)
    buttons = {}
    for name in names:
        b = QPushButton(name)
        b.setObjectName('seg')
        b.setCheckable(True)
        b.setAutoExclusive(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda _=False, n=name: on_pick(n))
        row.addWidget(b)
        buttons[name] = b
    return holder, buttons


# ----------------------------------------------------------- small tile pictures

def checker(p: QPainter, rect: QRectF, tile: int = 10, light: str = '#3A3A40', dark: str = '#2A2A2F'):
    p.save()
    p.setClipRect(rect, Qt.ClipOperation.IntersectClip)
    p.fillRect(rect, QColor(dark))
    y, row = rect.top(), 0
    while y < rect.bottom():
        x = rect.left() + (tile if row % 2 else 0)
        while x < rect.right():
            p.fillRect(QRectF(x, y, tile, tile), QColor(light))
            x += tile * 2
        y += tile
        row += 1
    p.restore()


def paint_backdrop(spec: dict):
    """Paint a backdrop spec on a tile, the same way the engine renders it."""
    def paint(p: QPainter, r: QRectF):
        kind, colors = spec.get('type', 'solid'), spec.get('colors') or []
        if kind == 'linear' and len(colors) >= 2:
            a = math.radians(spec.get('angle', 180))
            dx, dy = math.sin(a), -math.cos(a)
            half = (abs(dx) + abs(dy)) / 2
            reach = QPointF(half * dx * r.width(), half * dy * r.height())
            g = QLinearGradient(r.center() - reach, r.center() + reach)
            g.setColorAt(0, QColor(colors[0]))
            g.setColorAt(1, QColor(colors[1]))
            p.fillRect(r, g)
        elif kind == 'radial' and len(colors) >= 2:
            g = QRadialGradient(QPointF(r.center().x(), r.top() + r.height() * 0.38), r.width() * 0.75)
            g.setColorAt(0, QColor(colors[0]))
            g.setColorAt(1, QColor(colors[1]))
            p.fillRect(r, g)
        else:
            p.fillRect(r, QColor(spec.get('color') or (colors[0] if colors else '#F1F0ED')))
    return paint


def paint_shadow(style: str):
    def paint(p: QPainter, r: QRectF):
        side = r.width() * 0.42
        box = QRectF(0, 0, side, side)
        box.moveCenter(r.center() - QPointF(0, 2))
        p.setPen(Qt.PenStyle.NoPen)
        if style == 'soft':
            for i in range(6):
                p.setBrush(QColor(0, 0, 0, 16))
                p.drawRoundedRect(box.translated(0, 7).adjusted(-i, -i + 2, i, i), 6 + i, 6 + i)
        elif style == 'floor':
            p.setBrush(QColor(0, 0, 0, 120))
            p.drawEllipse(QRectF(box.left() + 2, box.bottom() - 3, box.width() - 4, 8))
        elif style == 'hard':
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawRoundedRect(box.translated(6, 6), 5, 5)
        p.setBrush(QColor('#C9803F'))
        p.drawRoundedRect(box, 5, 5)
    return paint


def paint_shape(w: int, h: int):
    def paint(p: QPainter, r: QRectF):
        k = min(r.width() * 0.5 / w, r.height() * 0.5 / h)
        box = QRectF(0, 0, w * k, h * k)
        box.moveCenter(r.center())
        p.setPen(QPen(QColor(C['soft']), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box, 3, 3)
    return paint


def paint_layout(kind: str):
    def bar(p, cx, cy, w, h, angle=0.0):
        p.save()
        p.translate(cx, cy)
        p.rotate(angle)
        p.drawRoundedRect(QRectF(-w / 2, -h / 2, w, h), h / 2, h / 2)
        p.restore()

    def paint(p: QPainter, r: QRectF):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C['tileink']))
        w, h, x0, y0 = r.width(), r.height(), r.left(), r.top()
        if kind in ('corner', 'single'):
            bar(p, x0 + w * 0.76, y0 + h * 0.80, w * 0.28, h * 0.11)
        elif kind == 'center':
            bar(p, x0 + w / 2, y0 + h / 2, w * 0.5, h * 0.18)
        elif kind == 'diagonal':
            bar(p, x0 + w / 2, y0 + h / 2, w * 0.56, h * 0.18, -25)
        elif kind in ('grid', 'brick'):
            for row in range(3):
                shift = w * 0.16 if (kind == 'brick' and row == 1) else 0
                for col in range(-1, 4):
                    bar(p, x0 + w * (0.18 + col * 0.32) + shift, y0 + h * (0.2 + row * 0.3), w * 0.2, h * 0.08)
        elif kind == 'band':
            p.setBrush(QColor('#B4B4AC'))
            p.drawRect(QRectF(x0, y0 + h * 0.70, w, h * 0.30))
            p.setBrush(QColor(C['tileink']))
            for col in range(3):
                bar(p, x0 + w * (0.18 + col * 0.32), y0 + h * 0.85, w * 0.2, h * 0.08)
    return paint


# ------------------------------------------------------------------ messages

def fade_in(widget: QWidget, ms: int = 190):
    """Let a page or panel appear softly instead of popping in."""
    parent = widget.parentWidget()
    while parent is not None:           # Qt cannot nest these effects, so only one fades at a time
        if parent.graphicsEffect() is not None:
            return
        parent = parent.parentWidget()
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    motion = QPropertyAnimation(effect, b'opacity', effect)
    motion.setDuration(ms)
    motion.setStartValue(0.0)
    motion.setEndValue(1.0)
    motion.setEasingCurve(QEasingCurve.Type.OutCubic)

    def done():
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
    motion.finished.connect(done)
    motion.start()


def glide(widget: QWidget, to: QPoint, ms: int = 200):
    """Move a widget smoothly to a new spot."""
    if widget.pos() == to:
        return
    running = getattr(widget, '_glide', None)
    if running is not None:
        running.stop()                  # never two moves fighting over the same widget
    motion = QPropertyAnimation(widget, b'pos', widget)
    motion.setDuration(ms)
    motion.setEndValue(to)
    motion.setEasingCurve(QEasingCurve.Type.OutCubic)
    widget._glide = motion
    motion.start()


TOAST_KINDS = {            # colour, icon, seconds on screen
    'info': ('#8EC5FF', 'info', 4.0),
    'success': (C['lime'], 'checkall', 4.0),
    'warning': ('#F6D860', 'warn', 6.5),
    'error': (C['coral'], 'error', 9.0),
}


class Toast(QFrame):
    """A message that drops in at the top centre and leaves on its own.

    It slides down and fades in. A coloured line along the bottom edge shrinks like a
    timer, so you can see how long it will stay. Resting the mouse on it holds it, and a
    click closes it. It fades out when it leaves.
    """
    closed = Signal(object)

    def __init__(self, parent, text: str, kind: str = 'info', seconds: float | None = None):
        super().__init__(parent)
        self.kind = kind if kind in TOAST_KINDS else 'info'
        color, icon_name, default = TOAST_KINDS[self.kind]
        self.color, self.text = QColor(color), text
        self.total = float(seconds or default)
        self.left = self.total
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('Click to close')
        self.setAccessibleName(text)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 20, 12)
        row.setSpacing(12)
        picture = QLabel()
        picture.setPixmap(icon_pixmap(icon_name, color, 22))
        row.addWidget(picture, 0, Qt.AlignmentFlag.AlignTop)
        words = QLabel(text)
        words.setStyleSheet(f'color: {C["text"]}; font-weight: 600; background: transparent;')
        if words.fontMetrics().horizontalAdvance(text) > 520 or '\n' in text:
            words.setWordWrap(True)
            words.setFixedWidth(520)
        row.addWidget(words)
        self.adjustSize()
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0.0)
        self.setGraphicsEffect(self.effect)
        self.fade = QPropertyAnimation(self.effect, b'opacity', self)
        self.fade.setDuration(200)
        self.fade.setEndValue(1.0)
        self.fade.start()

    def restart(self):
        self.left = self.total
        self.update()

    def tick(self):
        if self.underMouse():
            return
        self.left -= 0.03
        if self.left <= 0:
            self.dismiss()
        else:
            self.update()

    def dismiss(self):
        if not self.timer.isActive() and self.left <= -1:
            return
        self.timer.stop()
        self.left = -1                      # marks the toast as leaving
        self.fade.stop()
        self.fade.setDuration(170)
        self.fade.setEndValue(0.0)
        self.fade.finished.connect(self._gone)
        self.fade.start()

    def _gone(self):
        """It keeps its place in the stack until it has faded, so nothing slides over it."""
        self.hide()
        self.closed.emit(self)
        self.deleteLater()

    def mousePressEvent(self, _e):
        self.dismiss()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 14, 14)
        p.fillPath(shape, QColor('#1C1C20'))
        p.setPen(QPen(QColor(C['line']), 1))
        p.drawPath(shape)
        # The countdown: a line along the bottom edge only, shrinking toward the left.
        box = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setClipPath(shape)
        p.fillRect(QRectF(box.left(), box.bottom() - 3, box.width() * max(0.0, self.left) / self.total, 3), self.color)


class GlowBar(QWidget):
    """A progress bar with a soft glow and moving light stripes. It eases toward each new value."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(28)
        self.total, self.value, self.shown, self.phase, self.paused = 1, 0, 0.0, 0.0, False
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.tick)

    def set(self, value: int, total: int | None = None, jump: bool = False):
        if total is not None:
            self.total = max(1, total)
        self.value = max(0, min(self.total, value))
        if jump:
            self.shown = self.value / self.total
        self.update()

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def tick(self):
        self.shown += (self.value / self.total - self.shown) * 0.16
        if not self.paused:
            self.phase = (self.phase + 1.5) % 26
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = 10.0
        track = QRectF(6, (self.height() - h) / 2, self.width() - 12, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C['raised']))
        p.drawRoundedRect(track, h / 2, h / 2)
        width = track.width() * self.shown
        if width < 1:
            return
        fill = QRectF(track.left(), track.top(), max(h, width), h)
        first, second = ('#8A8A80', '#B9B9B0') if self.paused else (C['lime'], C['limehi'])
        if not self.paused:
            for i in range(1, 5):       # the glow: a few wider, fainter copies behind the bar
                p.setBrush(QColor(200, 245, 66, 16))
                grown = fill.adjusted(-i * 1.4, -i * 1.4, i * 1.4, i * 1.4)
                p.drawRoundedRect(grown, grown.height() / 2, grown.height() / 2)
        shade = QLinearGradient(fill.topLeft(), fill.topRight())
        shade.setColorAt(0, QColor(first))
        shade.setColorAt(1, QColor(second))
        shape = QPainterPath()
        shape.addRoundedRect(fill, h / 2, h / 2)
        p.fillPath(shape, shade)
        p.save()
        p.setClipPath(shape)
        p.setBrush(QColor(255, 255, 255, 60))
        x = fill.left() - 26 + self.phase
        while x < fill.right():
            stripe = QPainterPath(QPointF(x, fill.bottom()))
            stripe.lineTo(x + 9, fill.bottom())
            stripe.lineTo(x + 9 + h, fill.top())
            stripe.lineTo(x + h, fill.top())
            stripe.closeSubpath()
            p.drawPath(stripe)
            x += 26
        p.restore()
        if not self.paused:
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawEllipse(QPointF(fill.right() - h / 2, fill.center().y()), 2.6, 2.6)


class StepButton(QAbstractButton):
    """A step in the top bar: a small picture and a short word. The active step glows."""

    def __init__(self, text: str, paint, tip: str = ''):
        super().__init__()
        self.words, self.paint_fn = text, paint
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.setAccessibleName(text)
        font = QFont(self.font())
        font.setBold(True)
        self.setFont(font)
        self.setFixedSize(62 + self.fontMetrics().horizontalAdvance(text) + 18, 50)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        if on:
            for i in range(1, 4):       # the glow
                p.setBrush(QColor(200, 245, 66, 20))
                p.drawRoundedRect(r.adjusted(-i, -i, i, i), 12 + i, 12 + i)
        p.setBrush(QColor('#23271A' if on else (C['raised'] if self.underMouse() else C['panel'])))
        p.setPen(QPen(QColor(C['lime'] if on else C['line']), 1.6 if on else 1))
        p.drawRoundedRect(r, 12, 12)
        square = QRectF(r.left() + 6, r.top() + 6, r.height() - 12, r.height() - 12)
        clip = QPainterPath()
        clip.addRoundedRect(square, 7, 7)
        p.save()
        p.setClipPath(clip)
        self.paint_fn(p, square)
        p.restore()
        p.setFont(self.font())
        p.setPen(QColor(C['text'] if on else C['soft']))
        p.drawText(QRectF(square.right() + 10, r.top(), r.right() - square.right() - 10, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.words)


class RemovableTile(Tile):
    """A tile the user can take out of a list: a small × appears on hover, and right-click offers Remove."""
    removed = Signal()

    def __init__(self, *args, menu_actions: list | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.extra = menu_actions or []
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def close_rect(self) -> QRectF:
        return QRectF(self.face.width() - 25, 7, 18, 18)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.underMouse():
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            spot = self.close_rect()
            p.setPen(QPen(QColor(255, 255, 255, 200), 1))
            p.setBrush(QColor(14, 14, 16, 225))
            p.drawEllipse(spot)
            p.drawPixmap(QPointF(spot.left() + 3, spot.top() + 3), icon_pixmap('win_close', '#FFFFFF', 12))

    def _menu(self, pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction('Remove from the list', self.removed.emit)
        for text, action in self.extra:
            menu.addAction(text, action)
        menu.exec(self.mapToGlobal(pos))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.close_rect().contains(e.position()):
            self.setDown(False)
            self.removed.emit()
            return
        super().mouseReleaseEvent(e)


class AnchorPad(QWidget):
    """Nine spots for a watermark: three rows by three columns. The chosen spot shows a small bar."""
    picked = Signal(str)
    ROWS, COLS = 'tmb', 'lcr'

    def __init__(self):
        super().__init__()
        self.setFixedSize(120, 84)
        self.value = 'br'
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('Where the watermark sits')
        self.setAccessibleName('Watermark position')

    def set(self, value: str):
        self.value = value
        self.update()

    def cells(self):
        area = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        w, h = area.width() / 3, area.height() / 3
        for r, row in enumerate(self.ROWS):
            for c, col in enumerate(self.COLS):
                yield row + col, QRectF(area.left() + c * w, area.top() + r * h, w, h)

    def mouseReleaseEvent(self, e):
        for code, cell in self.cells():
            if cell.contains(e.position()):
                self.value = code
                self.update()
                self.picked.emit(code)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(C['line']), 1))
        p.setBrush(QColor(C['tile']))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
        p.setPen(Qt.PenStyle.NoPen)
        for code, cell in self.cells():
            if code == self.value:
                p.setBrush(QColor(C['tileink']))
                bar = QRectF(0, 0, 22, 8)
                bar.moveCenter(cell.center())
                p.drawRoundedRect(bar, 4, 4)
            else:
                p.setBrush(QColor('#B4B4AC'))
                p.drawEllipse(cell.center(), 3, 3)
